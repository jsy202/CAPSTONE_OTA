"""Control-centre stand-in: serves signed releases, collects incident reports, and lets a
human approve or reject the restore in a browser. Approval produces a signed order.

Experimental-network HTTP only (no TLS, no login), like the rest of the project's transport.
"""
from __future__ import annotations

import argparse
import base64
import hmac
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


LINE = 32  # hash characters per line: a 64-character hash reads as two clear rows

_STYLE = (
    "body{font:16px sans-serif;max-width:980px;margin:2em auto;padding:0 1em}"
    "section{border:2px solid #666;border-radius:12px;padding:1.2em 1.5em;margin:1.2em 0}"
    "h2{margin-top:0}.s{font-size:.6em;border:2px solid;border-radius:6px;padding:.1em .6em;vertical-align:middle}"
    ".s.reported{color:#92400e;background:#fef3c7}.s.approved{color:#166534;background:#dcfce7}"
    ".s.rejected{color:#991b1b;background:#fee2e2}.s.closed{color:#374151;background:#e5e7eb}"
    "th{text-align:left;padding:.2em 1.2em .2em 0;white-space:nowrap}td{word-break:break-all}"
    ".hash{background:#f8fafc;border:1px solid #cbd5e1;border-radius:10px;padding:1em 1.2em;margin:0 0 1em}"
    ".hl{font-size:1.05em;font-weight:700;margin-top:.5em}.hl small{font-weight:400;color:#475569}"
    ".h{font:600 28px/1.5 ui-monospace,Menlo,Consolas,monospace;letter-spacing:.05em;word-break:break-all}"
    ".h .d{border-radius:3px}.h.ok .d{background:#fde68a}.h.bad .d{background:#dc2626;color:#fff}"
    ".verdict{margin-top:.7em;font-size:1.5em;font-weight:800;color:#b91c1c}"
    ".b{display:flex;gap:1em;margin-top:1.2em}.b form{flex:1}"
    "button{width:100%;font-size:28px;font-weight:800;padding:.9em 0;border:0;border-radius:14px;color:#fff;cursor:pointer}"
    "button.go{background:#15803d}button.go:hover{background:#166534}"
    "button.no{background:#b91c1c}button.no:hover{background:#991b1b}"
)


def _hash_lines(mine: str, other: str) -> str:
    """`mine` split into rows, with every character that differs from `other` marked."""
    lines = []
    for start in range(0, len(mine), LINE):
        runs, current, flag = [], "", None
        for index in range(start, min(start + LINE, len(mine))):
            differs = index >= len(other) or other[index] != mine[index]
            if differs != flag and current:
                runs.append((flag, current))
                current = ""
            flag, current = differs, current + mine[index]
        runs.append((flag, current))
        lines.append("".join(f'<span class="d">{escape(text)}</span>' if flag else escape(text) for flag, text in runs))
    return "<br>".join(lines)


def _hash_block(expected, observed) -> str:
    if not isinstance(expected, str) or not isinstance(observed, str):
        return ""
    wrong = sum(1 for index, char in enumerate(expected) if index >= len(observed) or observed[index] != char)
    wrong += max(0, len(observed) - len(expected))
    verdict = (f'<div class="verdict">⚠ 해시 불일치 — {wrong}/{max(len(expected), len(observed))}자가 다름</div>'
               if wrong else '<div class="verdict" style="color:#166534">해시 일치</div>')
    return ('<div class="hash"><div class="hl">기대 해시 <small>(커밋 기록)</small></div>'
            f'<div class="h ok">{_hash_lines(expected, observed)}</div>'
            '<div class="hl">관측 해시 <small>(현재 슬롯)</small></div>'
            f'<div class="h bad">{_hash_lines(observed, expected)}</div>{verdict}</div>')


_CONFLICT_PAGE = ("<!doctype html><meta charset=utf-8><title>이미 처리됨</title>"
                  "<h1>이미 처리된 사고입니다</h1><p>다른 결정이 먼저 기록되었거나 복원할 수 있는 릴리스가 없습니다.</p>"
                  '<p><a href="/">관제 화면으로 돌아가기</a></p>')


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(args[2].releases_dir), **kwargs)

    def setup(self):
        self.timeout = self.server.request_timeout  # A stalled client must not hold a thread forever.
        super().setup()

    def _authorized(self) -> bool:
        token = self.server.api_token
        if token is None:
            return True
        given = self.headers.get("Authorization", "")
        return hmac.compare_digest(given.encode(), f"Bearer {token}".encode())

    def _same_origin(self) -> bool:
        """Refuse a decision form that another website made the browser submit."""
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin.split("://", 1)[-1] == self.headers.get("Host")

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
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
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
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
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
        if not self._same_origin():
            self._reply(403)
            return
        status = self.server.decide(incident, action)  # The form body is deliberately ignored.
        if status == 409:
            self._reply(409, _CONFLICT_PAGE.encode(), "text/html; charset=utf-8")
        else:
            self._reply(status, location="/" if status == 303 else None)


class IncidentServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, releases_dir, store, order_key, release_labels, order_lifetime: float = 300.0,
                 api_token: str | None = None, request_timeout: float = 10.0):
        self.releases_dir = Path(releases_dir).resolve()
        self.store = Path(store)
        self.order_key = Path(order_key)
        self.release_labels = dict(release_labels)  # committed version -> release directory label
        self.order_lifetime = order_lifetime
        self.api_token = api_token
        self.request_timeout = request_timeout
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

    def _shown_version(self, committed_version) -> str:
        """The release's product name (V2) when this server knows the committed version, else the raw version number."""
        label = self.release_labels.get(committed_version)
        return label.upper() if label else str(committed_version)

    def render_index(self) -> str:
        rows = []
        for item in reversed(self.incidents):
            report, status = item["report"], item["status"]
            version = self._shown_version(report["committed_version"])
            fields = [("시각", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item["reported_at"]))),
                      ("차량", report["vehicle"]), ("유형", report.get("type", "")),
                      ("대상 슬롯", f"{report.get('slot', '')} (커밋 버전 {version}, svn {report.get('committed_svn', '')})"),
                      ("출발지", report.get("source")), ("게이트 상태", report.get("gateway_state")),
                      ("클러스터", report.get("cluster")), ("상세", report.get("detail")),
                      ("결과", (item.get("outcome") or {}).get("result"))]
            table = "".join(f"<tr><th>{escape(name)}</th><td>{escape(str(value))}</td></tr>"
                            for name, value in fields if value is not None)
            buttons = ""
            if status == "reported":
                buttons = (f'<form method="post" action="/incidents/{item["id"]}/approve"><button class="go">{escape(version)}로 복원 승인</button></form>'
                           f'<form method="post" action="/incidents/{item["id"]}/reject"><button class="no">거부 · 입고 지시</button></form>')
            rows.append(f'<section><h2>{escape(item["id"])} <span class="s {escape(status)}">{escape(status)}</span></h2>'
                        f'{_hash_block(report.get("expected_tree_sha256"), report.get("observed_tree_sha256"))}'
                        f'<table>{table}</table><div class="b">{buttons}</div></section>')
        body = "".join(rows) or "<p>접수된 사고가 없습니다.</p>"
        return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="refresh" content="2">'
                '<title>보안 사고 관제</title><style>' + _STYLE + '</style></head><body><h1>보안 사고 관제</h1>'
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
    parser.add_argument("--api-token-file", type=Path,
                        help="require this bearer token for vehicle reports and outcomes")
    args = parser.parse_args(argv)
    token = args.api_token_file.read_text().strip() if args.api_token_file else None
    labels = dict(item.split("=", 1) for item in args.release_label)
    with IncidentServer(_address(args.bind), args.releases, args.store, args.order_key, labels,
                        api_token=token) as server:
        print(f"incident server http://{args.bind}/", file=sys.stderr)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
