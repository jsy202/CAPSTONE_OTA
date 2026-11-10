import json

from bench.ivi_release import make
from capstone_ota.bundle import verify


def test_make_signs_both_releases_and_keeps_the_private_key_out_of_them(tmp_path):
    result = make(tmp_path / 'demo')
    public = tmp_path / 'demo/keys/ivi-public.pem'
    for name, version, svn in (('v2', '2.0.0', 1), ('v3', '3.0.0', 2)):
        folder = tmp_path / 'demo/releases' / name
        meta = verify((folder / 'release.json').read_bytes(), (folder / 'release.sig').read_bytes(), public)
        assert (meta['version'], meta['svn']) == (version, svn)
        assert b'PRIVATE' not in (folder / 'app.tar.gz').read_bytes()
    assert (tmp_path / 'demo/keys/ivi-private.pem').stat().st_mode & 0o077 == 0
    assert json.dumps(result)
