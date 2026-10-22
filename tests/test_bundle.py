import hashlib
import importlib
import importlib.util
import io
import json
import tarfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def bundle():
    assert importlib.util.find_spec("capstone_ota.bundle") is not None, "minimal package API is missing"
    return importlib.import_module("capstone_ota.bundle")


@pytest.fixture
def keys(tmp_path):
    private = Ed25519PrivateKey.generate()
    key = tmp_path / "signing.key"
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    public = tmp_path / "signing.pub"
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo))
    return key, public


@pytest.fixture
def payload(tmp_path):
    root = tmp_path / "payload"
    (root / "bin").mkdir(parents=True)
    app = root / "bin/app"
    app.write_text("#!/bin/sh\nexit 0\n")
    app.chmod(0o755)
    (root / "config.json").write_text('{"period_ms":100}')
    return root


def test_signed_package_round_trip(bundle, keys, payload, tmp_path):
    release = tmp_path / "release"
    metadata = bundle.pack(payload, release, "1.2.3", 2, keys[0])
    assert metadata == {"version": "1.2.3", "svn": 2,
                        "sha256": hashlib.sha256((release / "app.tar.gz").read_bytes()).hexdigest()}
    verified = bundle.verify((release / "release.json").read_bytes(),
                             (release / "release.sig").read_bytes(), keys[1])
    assert verified == metadata
    destination = tmp_path / "unpacked"
    bundle.extract(release / "app.tar.gz", destination)
    assert (destination / "bin/app").read_text() == "#!/bin/sh\nexit 0\n"
    assert (destination / "bin/app").stat().st_mode & 0o111


def test_modified_metadata_cannot_be_verified(bundle, keys, payload, tmp_path):
    release = tmp_path / "release"
    bundle.pack(payload, release, "1.0.0", 1, keys[0])
    raw = (release / "release.json").read_bytes().replace(b'"svn":1', b'"svn":9')
    with pytest.raises(ValueError, match="signature"):
        bundle.verify(raw, (release / "release.sig").read_bytes(), keys[1])


@pytest.mark.parametrize("version,svn", [("1.0", 1), ("01.0.0", 1), ("1.0.0-beta", 1),
                                        ("1.0.0", 0), ("1.0.0", True), ("1.0.0", -1)])
def test_invalid_release_is_not_packaged(bundle, keys, payload, tmp_path, version, svn):
    output = tmp_path / "release"
    with pytest.raises(ValueError):
        bundle.pack(payload, output, version, svn, keys[0])
    assert not output.exists()


def test_pack_refuses_existing_output_and_non_executable_app(bundle, keys, payload, tmp_path):
    output = tmp_path / "release"
    output.mkdir()
    (output / "keep").write_text("original")
    with pytest.raises((ValueError, FileExistsError)):
        bundle.pack(payload, output, "1.0.0", 1, keys[0])
    assert (output / "keep").read_text() == "original"
    (payload / "bin/app").chmod(0o644)
    with pytest.raises(ValueError, match="executable"):
        bundle.pack(payload, tmp_path / "other", "1.0.0", 1, keys[0])


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("/escape", "file"),
                                     ("bin/../escape", "file"), ("bin/link", "symlink"),
                                     ("bin/link", "hardlink"), ("bin/pipe", "fifo")])
def test_extract_rejects_unsafe_entries(bundle, tmp_path, name, kind):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as target:
        entry = tarfile.TarInfo(name)
        if kind == "file":
            entry.size = 1
            target.addfile(entry, io.BytesIO(b"x"))
        else:
            entry.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                          "fifo": tarfile.FIFOTYPE}[kind]
            entry.linkname = "../../escape"
            target.addfile(entry)
    with pytest.raises(ValueError, match="archive"):
        bundle.extract(archive, tmp_path / "out")
    assert not (tmp_path / "escape").exists()


def test_extract_does_not_follow_an_existing_destination(bundle, tmp_path):
    archive = tmp_path / "safe.tar.gz"
    with tarfile.open(archive, "w:gz"):
        pass
    destination = tmp_path / "out"
    destination.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((ValueError, FileExistsError)):
        bundle.extract(archive, destination)


def test_signed_invalid_schema_is_rejected(bundle, keys):
    private = serialization.load_pem_private_key(keys[0].read_bytes(), password=None)
    raw = json.dumps({"version": "1.0.0", "svn": 1, "sha256": "0" * 64, "url": "http://evil"}).encode()
    with pytest.raises(ValueError, match="metadata"):
        bundle.verify(raw, private.sign(raw), keys[1])
