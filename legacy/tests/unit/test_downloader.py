import ipaddress
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from capstone_ota.agent.downloader import download_https
from capstone_ota.common.errors import OtaError


PAYLOAD = b"signed dashboard artifact"


def create_tls_material(root, prefix):
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{prefix} CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = root / f"{prefix}-ca.pem"
    cert_path = root / f"{prefix}-server.pem"
    key_path = root / f"{prefix}-server.key"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_path, cert_path, key_path


class ArtifactHandler(BaseHTTPRequestHandler):
    mode = "normal"

    def do_GET(self):
        body = PAYLOAD
        declared = len(body) + 4 if self.mode == "wrong-length" else len(body)
        self.send_response(200)
        self.send_header("Content-Length", str(declared))
        self.end_headers()
        if self.mode == "interrupted":
            self.wfile.write(body[:5])
            self.wfile.flush()
            self.connection.shutdown(1)
        else:
            self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


@pytest.fixture
def https_server(tmp_path):
    ca, cert, key = create_tls_material(tmp_path, "trusted")
    server = ThreadingHTTPServer(("127.0.0.1", 0), ArtifactHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}/release.tar.gz", ca
    finally:
        server.shutdown()
        thread.join()


def test_download_uses_trusted_ca_and_returns_completed_file(tmp_path, https_server):
    url, ca = https_server
    destination = tmp_path / "release.tar.gz"

    result = download_https(url, destination, ca, len(PAYLOAD), 1024)

    assert result.path == destination
    assert result.size == len(PAYLOAD)
    assert destination.read_bytes() == PAYLOAD
    assert not (tmp_path / "release.tar.gz.part").exists()


def test_download_rejects_untrusted_server_certificate(tmp_path, https_server):
    url, _trusted_ca = https_server
    other_ca, _cert, _key = create_tls_material(tmp_path, "other")

    with pytest.raises(OtaError) as error:
        download_https(url, tmp_path / "release.tar.gz", other_ca, len(PAYLOAD), 1024)

    assert error.value.code == "DOWNLOAD_TLS_FAILED"


@pytest.mark.parametrize(
    ("mode", "expected_size", "max_bytes", "code"),
    [
        ("normal", len(PAYLOAD) + 1, 1024, "DOWNLOAD_SIZE_MISMATCH"),
        ("normal", len(PAYLOAD), 5, "DOWNLOAD_TOO_LARGE"),
        ("wrong-length", len(PAYLOAD), 1024, "DOWNLOAD_SIZE_MISMATCH"),
        ("interrupted", len(PAYLOAD), 1024, "DOWNLOAD_FAILED"),
    ],
)
def test_failed_download_removes_partial_file(
    tmp_path, https_server, mode, expected_size, max_bytes, code
):
    url, ca = https_server
    ArtifactHandler.mode = mode
    destination = tmp_path / "release.tar.gz"
    try:
        with pytest.raises(OtaError) as error:
            download_https(url, destination, ca, expected_size, max_bytes)
        assert error.value.code == code
        assert not destination.exists()
        assert not (tmp_path / "release.tar.gz.part").exists()
    finally:
        ArtifactHandler.mode = "normal"


def test_download_rejects_plain_http_before_network_access(tmp_path):
    with pytest.raises(OtaError) as error:
        download_https("http://127.0.0.1/release", tmp_path / "release", tmp_path / "ca", 1, 2)
    assert error.value.code == "DOWNLOAD_URL_INVALID"
