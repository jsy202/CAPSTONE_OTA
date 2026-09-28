import io
import tarfile

import pytest

from capstone_ota.agent.archive import ArchiveLimits, safe_extract_tar
from capstone_ota.common.errors import OtaError


def make_archive(path, members):
    with tarfile.open(path, "w:gz") as archive:
        for name, kind, content in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                data = content.encode()
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = content
                archive.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = content
                archive.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                archive.addfile(info)


def limits(files=10, bytes_=1024):
    return ArchiveLimits(max_files=files, max_expanded_bytes=bytes_)


def test_safe_archive_extracts_regular_nested_payload(tmp_path):
    archive = tmp_path / "valid.tar.gz"
    destination = tmp_path / "output"
    make_archive(
        archive,
        [("bin", "dir", ""), ("bin/digital-dash", "file", "app"), ("qml/Main.qml", "file", "ui")],
    )

    safe_extract_tar(archive, destination, limits())

    assert (destination / "bin" / "digital-dash").read_text() == "app"
    assert (destination / "qml" / "Main.qml").read_text() == "ui"


@pytest.mark.parametrize(
    "member",
    [
        ("/etc/passwd", "file", "bad"),
        ("../outside", "file", "bad"),
        ("bin/link", "symlink", "../../outside"),
        ("bin/hard", "hardlink", "bin/digital-dash"),
        ("bin/pipe", "fifo", ""),
    ],
)
def test_unsafe_archive_member_is_rejected_before_extraction(tmp_path, member):
    archive = tmp_path / "bad.tar.gz"
    destination = tmp_path / "output"
    make_archive(archive, [("safe", "file", "untouched"), member])

    with pytest.raises(OtaError) as error:
        safe_extract_tar(archive, destination, limits())

    assert error.value.code == "ARCHIVE_UNSAFE"
    assert not destination.exists()


def test_archive_file_count_limit_is_enforced(tmp_path):
    archive = tmp_path / "many.tar.gz"
    make_archive(archive, [(f"file-{index}", "file", "x") for index in range(3)])
    with pytest.raises(OtaError) as error:
        safe_extract_tar(archive, tmp_path / "output", limits(files=2))
    assert error.value.code == "ARCHIVE_LIMIT_EXCEEDED"


def test_archive_expanded_size_limit_is_enforced(tmp_path):
    archive = tmp_path / "large.tar.gz"
    make_archive(archive, [("large", "file", "123456")])
    with pytest.raises(OtaError) as error:
        safe_extract_tar(archive, tmp_path / "output", limits(bytes_=5))
    assert error.value.code == "ARCHIVE_LIMIT_EXCEEDED"
