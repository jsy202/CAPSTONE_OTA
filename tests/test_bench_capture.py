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


def test_dead_verifier_capture_cannot_silently_produce_passing_evidence(tmp_path):
    import subprocess
    import sys
    from types import SimpleNamespace
    from bench.evidence import Captures
    class Peer:
        def run(self, *_args, **_kwargs):
            return SimpleNamespace(stdout=b'other-board-evidence')
    capture = Captures(Peer(), Peer(), '/runtime', 'unit-test', tmp_path)
    capture.verifier_log = (tmp_path / 'verifier-can.log').open('wb')
    capture.verifier_process = subprocess.Popen([sys.executable, '-c',
        'import sys;sys.stderr.write("capture connection lost");sys.exit(3)'],
        stdout=capture.verifier_log, stderr=subprocess.PIPE)
    capture.verifier_process.wait(timeout=5)
    errors = capture.close()
    assert any('verifier capture exited' in e for e in errors)
    assert (tmp_path / 'verifier-capture-stderr.log').read_text() == 'capture connection lost'
