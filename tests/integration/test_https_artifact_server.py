import ipaddress
import ssl
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from capstone_ota.publisher.http_server import create_https_server


def server_certificate(root):
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CAPSTONE Test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(name)
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
        .sign(key, hashes.SHA256())
    )
    ca_path = root / "ca.pem"
    cert_path = root / "server.pem"
    key_path = root / "server.key"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_path, cert_path, key_path


def test_https_server_exposes_only_regular_release_files(tmp_path):
    release_root = tmp_path / "releases"
    release_root.mkdir()
    (release_root / "1.0.0.tar.gz").write_bytes(b"artifact")
    (release_root / "2.0.0.vehicle-manifest.json").write_bytes(b"bundle")
    (release_root / "2.0.0.vehicle-manifest.sig").write_bytes(b"signature")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (release_root / "linked.tar.gz").symlink_to(outside)
    ca, cert, key = server_certificate(tmp_path)
    server = create_https_server(release_root, "127.0.0.1", 0, cert, key)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = ssl.create_default_context(cafile=str(ca))
    base = f"https://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(f"{base}/releases/1.0.0.tar.gz", context=context) as response:
            assert response.read() == b"artifact"
        # vehicle-publish advertises these exact names to the coordinator.
        for name, content in (("2.0.0.vehicle-manifest.json", b"bundle"), ("2.0.0.vehicle-manifest.sig", b"signature")):
            with urllib.request.urlopen(f"{base}/releases/{name}", context=context) as response:
                assert response.read() == content
        for path in ("/secret.txt", "/releases/../secret.txt", "/releases/linked.tar.gz"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"{base}{path}", context=context)
            assert error.value.code in {403, 404}
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
