"""Root's evidence reader must not follow files controlled by an app."""
import pytest


def test_capture_rejects_symlink_before_reading_private_content(tmp_path, monkeypatch):
    from bench.verify_service import save_capture
    import bench.verify_service as checker
    secret = tmp_path / 'secret'; secret.write_bytes(b'not a screenshot')
    link = tmp_path / 'capture.png'; link.symlink_to(secret)
    original = checker.os.open
    def open_file(path, flags):
        return original(link if str(path) == '/run/capstone-cluster/capture.png' else path, flags)
    monkeypatch.setattr(checker.os, 'open', open_file)
    with pytest.raises(OSError):
        save_capture(tmp_path / 'evidence', 'A')
    assert not (tmp_path / 'evidence').exists()
