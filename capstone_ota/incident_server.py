"""Control-centre stand-in: serves signed releases, collects incident reports, and lets a
human approve or reject the restore in a browser. Approval produces a signed order.

Experimental-network HTTP only (no TLS, no login), like the rest of the project's transport.
"""
from __future__ import annotations

import argparse
import base64
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading
import time

from .bundle import version_key
from .device import atomic_write
from .order import default_expiry, sign_order

MAX_BODY = 65536
NONCE = re.compile(r"[0-9a-f]{8,64}")
INCIDENT_PATH = re.compile(r"/incidents/(INC-[0-9]{4,})/(order|approve|reject|outcome)")


def _valid_report(report) -> bool:
    try:
        return (isinstance(report, dict)
                and isinstance(report.get("vehicle"), str) and 0 < len(report["vehicle"]) <= 64
                and isinstance(report.get("nonce"), str) and bool(NONCE.fullmatch(report["nonce"]))
                and bool(version_key(report.get("committed_version"))))
    except ValueError:
        return False


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(args[2].releases_dir), **kwargs)

    def log_message(self, *_args):
        pass

    def list_directory(self, path):
        self.send_error(404)
        return None

    def _reply(self, status, body=b"", content_type="application/json", location=None):
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        if body:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, value):
        self._reply(status, json.dumps(value).encode())

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        return self.rfile.read(length) if 0 <= length <= MAX_BODY else None

    def _serve_release(self, handler):
        path = self.path.split("?", 1)[0]
        if path != "/releases" and not path.startswith("/releases/"):
            return False
        self.path = path[len("/releases"):] or "/"
        handler()
        return True

    def do_HEAD(self):
        if not self._serve_release(super().do_HEAD):
            self._reply(404)

    def do_GET(self):
        if self._serve_release(super().do_GET):
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._reply(200, self.server.render_index().encode(), "text/html; charset=utf-8")
            return
        match = INCIDENT_PATH.fullmatch(path)
        if match and match.group(2) == "order":
            status, body = self.server.order_for(match.group(1))
            self._reply(status, json.dumps(body).encode() if body else b"")
            return
        self._reply(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        body = self._body()
        if body is None:
            self._reply(413)
            return
        if path == "/incidents":
            try:
                report = json.loads(body)
            except ValueError:
                report = None
            if not _valid_report(report):
                self._json(400, {"error": "invalid report"})
                return
            created, incident = self.server.add_report(report)
            self._json(201 if created else 200, {"id": incident})
            return
        match = INCIDENT_PATH.fullmatch(path)
        if not match or match.group(2) == "order":
            self._reply(404)
            return
        incident, action = match.groups()
        if action == "outcome":
            try:
                outcome = json.loads(body)
            except ValueError:
                outcome = None
            if not isinstance(outcome, dict) or not isinstance(outcome.get("result"), str):
                self._json(400, {"error": "invalid outcome"})
                return
            status = self.server.close_incident(incident, outcome)
            self._json(status, {})
            return
        status = self.server.decide(incident, action)  # The form body is deliberately ignored.
        self._reply(status, location="/" if status == 303 else None)


class IncidentServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, releases_dir, store, order_key, release_labels, order_lifetime: float = 300.0):
        self.releases_dir = Path(releases_dir).resolve()
        self.store = Path(store)
        self.order_key = Path(order_key)
        self.release_labels = dict(release_labels)  # committed version -> release directory label
        self.order_lifetime = order_lifetime
        self._lock = threading.Lock()
        self._incidents: list[dict] = self._load()  # Before binding: a corrupt store must not open a port.
        super().__init__(address, _Handler)

    def _load(self) -> list[dict]:
        try:
            raw = self.store.read_text()
        except FileNotFoundError:
            return []
        keys = {"id", "status", "report", "order", "outcome"}
        try:
            incidents = json.loads(raw)
            valid = (isinstance(incidents, list)
                     and all(isinstance(item, dict) and keys <= item.keys() and isinstance(item["report"], dict)
                             and re.fullmatch(r"INC-[0-9]{4,}", str(item["id"])) for item in incidents))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("incident store is corrupt; refusing to overwrite it")
        return incidents

    @property
    def incidents(self) -> list[dict]:
        with self._lock:
            return json.loads(json.dumps(self._incidents))

    def _save(self) -> None:
        atomic_write(self.store, json.dumps(self._incidents, indent=1).encode())

    def _find(self, incident):
        return next((item for item in self._incidents if item["id"] == incident), None)

    def add_report(self, report: dict) -> tuple[bool, str]:
        with self._lock:
            for item in self._incidents:
                if item["report"]["nonce"] == report["nonce"] and item["report"]["vehicle"] == report["vehicle"]:
                    return False, item["id"]
            number = max((int(item["id"][4:]) for item in self._incidents), default=0) + 1
            incident = f"INC-{number:04d}"
            self._incidents.append({"id": incident, "status": "reported", "reported_at": time.time(),
                                    "report": report, "order": None, "outcome": None})
            self._save()
            return True, incident

    def decide(self, incident: str, action: str) -> int:
        with self._lock:
            item = self._find(incident)
            if item is None:
                return 404
            if item["status"] != "reported":
                return 409
            report = item["report"]
            label = self.release_labels.get(report["committed_version"])
            if action == "approve" and label is None:
                return 409  # Only a release the server knows can be restored; never a guess.
            order = {"incident": incident, "vehicle": report["vehicle"], "nonce": report["nonce"],
                     "action": "restore" if action == "approve" else "reject",
                     "release": label if action == "approve" else None,
                     # The restored version is the vehicle's own committed version, never a UI input.
                     "version": report["committed_version"] if action == "approve" else None,
                     "expires": default_expiry(self.order_lifetime)}
            raw, signature = sign_order(order, self.order_key)
            item.update(status="approved" if action == "approve" else "rejected", decided_at=time.time(),
                        order={"order": base64.b64encode(raw).decode(),
                               "signature": base64.b64encode(signature).decode()})
            self._save()
            return 303

    def order_for(self, incident: str) -> tuple[int, dict | None]:
        with self._lock:
            item = self._find(incident)
            if item is None:
                return 404, None
            return (204, None) if item["order"] is None else (200, item["order"])

    def close_incident(self, incident: str, outcome: dict) -> int:
        with self._lock:
            item = self._find(incident)
            if item is None:
                return 404
            if item["order"] is None or item["status"] == "closed":
                return 409
            item.update(status="closed", outcome=outcome, closed_at=time.time())
            self._save()
            return 200

    def render_index(self) -> str:
        rows = []
        for item in reversed(self.incidents):
            report, status = item["report"], item["status"]
            fields = [("시각", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item["reported_at"]))),
                      ("차량", report["vehicle"]), ("유형", report.get("type", "")),
                      ("대상 슬롯", f"{report.get('slot', '')} (커밋 버전 {report['committed_version']}, svn {report.get('committed_svn', '')})"),
                      ("기대 해시", report.get("expected_tree_sha256")), ("관측 해시", report.get("observed_tree_sha256")),
                      ("출발지", report.get("source")), ("게이트 상태", report.get("gateway_state")),
                      ("클러스터", report.get("cluster")), ("상세", report.get("detail")),
                      ("결과", (item.get("outcome") or {}).get("result"))]
            table = "".join(f"<tr><th>{escape(name)}</th><td>{escape(str(value))}</td></tr>"
                            for name, value in fields if value is not None)
            buttons = ""
            if status == "reported":
                buttons = (f'<form method="post" action="/incidents/{item["id"]}/approve"><button>V2로 복원 승인</button></form>'
                           f'<form method="post" action="/incidents/{item["id"]}/reject"><button>거부 · 입고 지시</button></form>')
            rows.append(f'<section><h2>{escape(item["id"])} <span class="s">{escape(status)}</span></h2>'
                        f'<table>{table}</table><div class="b">{buttons}</div></section>')
        body = "".join(rows) or "<p>접수된 사고가 없습니다.</p>"
        return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="refresh" content="2">'
                '<title>보안 사고 관제</title><style>body{font:15px sans-serif;max-width:760px;margin:2em auto;padding:0 1em}'
                'th{text-align:left;padding-right:1em;white-space:nowrap}td{word-break:break-all}.s{font-size:.7em;'
                'border:1px solid;padding:0 .4em}.b{display:flex;gap:.5em;margin-top:.5em}section{border:1px solid #888;'
                'padding:1em;margin:1em 0}</style></head><body><h1>보안 사고 관제</h1>'
                '<p><small>시연 환경: V3 실패 판정은 시험용으로 강제한 것이며 실제 CPU 경합 측정이 아닙니다.</small></p>'
                + body + "</body></html>")


def _address(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1:8080")
    parser.add_argument("--releases", required=True, type=Path)
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--order-key", required=True, type=Path)
    parser.add_argument("--release-label", action="append", default=[], metavar="VERSION=LABEL")
    args = parser.parse_args(argv)
    labels = dict(item.split("=", 1) for item in args.release_label)
    with IncidentServer(_address(args.bind), args.releases, args.store, args.order_key, labels) as server:
        print(f"incident server http://{args.bind}/", file=sys.stderr)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
