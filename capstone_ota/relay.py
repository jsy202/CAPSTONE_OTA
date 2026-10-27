"""Central caches a signed release, then Cluster independently downloads/checks it."""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shlex
import tempfile
import threading
from urllib.parse import urlsplit

from .bundle import MAX_ARCHIVE, verify
from .cluster import check_command
from .device import fetch
from .process import exchange


def cache_release(url, public_key, destination):
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.query or parsed.fragment or parsed.username):
        raise ValueError("use an HTTP(S) release directory URL without credentials/query")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    base = url.rstrip("/") + "/"
    fetch(base + "release.json", destination / "release.json", 16384)
    fetch(base + "release.sig", destination / "release.sig", 64)
    metadata = verify((destination / "release.json").read_bytes(),
                      (destination / "release.sig").read_bytes(), public_key)
    digest = fetch(base + "app.tar.gz", destination / "app.tar.gz", MAX_ARCHIVE)
    if digest.hex() != metadata["sha256"]:
        raise ValueError("relay archive hash mismatch")
    return destination


@contextmanager
def serving_release(directory, bind="127.0.0.1", advertise=None):
    host = advertise or bind
    if host == "0.0.0.0" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
        raise ValueError("advertise must be the Central IPv4 address or DNS hostname")

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path not in {"/release.json", "/release.sig", "/app.tar.gz"}:
                self.send_error(404)
                return
            super().do_GET()

        def do_HEAD(self):
            self.send_error(405)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer((bind, 0), partial(Handler, directory=str(Path(directory).absolute())))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def target_command(config):
    required = {"host", "checkout", "python", "root", "public_key"}
    if (not isinstance(config, dict) or not required <= config.keys()
            or config.keys() - required - {"check_command", "initialization_only", "systemd"}
            or any(not isinstance(config[k], str) or not config[k] or "\x00" in config[k] for k in required)
            or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", config["host"])):
        raise ValueError("invalid Cluster target configuration")
    for key in ("checkout", "python", "root", "public_key"):
        if not config[key].startswith("/"):
            raise ValueError("target paths must be absolute")
    initial = config.get("initialization_only", False)
    systemd = config.get("systemd", False)
    checker = config.get("check_command")
    if (type(initial) is not bool or type(systemd) is not bool
            or (checker is None) != initial):
        raise ValueError("choose a check_command or explicit initialization_only=true")
    arguments = [config["python"], "-m", "capstone_ota", "--root", config["root"],
                 "cluster-update", "--public-key", config["public_key"]]
    if initial:
        arguments.append("--initialization-only")
    else:
        import json
        arguments += ["--check-command", json.dumps(check_command(checker))]
    if systemd:
        arguments.append("--systemd")
    return arguments


def forward(url, public_key, config, bind="127.0.0.1", advertise=None):
    arguments = target_command(config)  # Validate before fetching or contacting the target.
    with tempfile.TemporaryDirectory(prefix="capstone-relay-") as temporary:
        cache = cache_release(url, public_key, Path(temporary) / "release")
        metadata = verify((cache / "release.json").read_bytes(), (cache / "release.sig").read_bytes(), public_key)
        with serving_release(cache, bind, advertise) as relay_url:
            remote = "cd " + shlex.quote(config["checkout"]) + " && " + shlex.join([*arguments, relay_url])
            command = ["ssh", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes", "-oConnectTimeout=5",
                       config["host"], remote]
            try:
                rows, _pid = exchange(command, [], timeout=300, allow_rollback=True, allow_negative_verdict=True)
                if len(rows) != 1 or not isinstance(rows[0], dict):
                    raise ValueError("missing Cluster status")
                result = rows[0]
                outcome = result.get("result")
                if (outcome not in {"committed", "rolled_back", "failed", "recovery_failed"}
                        or result.get("phase") != ("trial" if outcome == "recovery_failed" else "stable")
                        or type(result.get("min_svn")) is not int or result["min_svn"] < 0
                        or result.get("validation") != ("initialization_only" if config.get("initialization_only") else "external")
                        or (outcome == "committed" and (result.get("version") != metadata["version"]
                            or result["min_svn"] != metadata["svn"] or result.get("active_slot") not in {"A", "B"}
                            or result.get("trial_version") is not None))):
                    raise ValueError("invalid Cluster status")
            except (OSError, ValueError) as exc:
                raise ValueError("Cluster outcome unconfirmed; query its status before retrying") from exc
    return dict(result, target="cluster", transport="central-cache-http+ssh")
