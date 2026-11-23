"""The control-centre server: receives incident reports, a human decides, a signed order comes back."""
import base64
import http.client
import json
import re
import threading
import time

import pytest

from capstone_ota.incident_server import IncidentServer
from capstone_ota.order import verify_order
from test_order import NONCE, make_keypair, order_keys  # noqa: F401

REPORT = {"vehicle": "PI2-001", "nonce": NONCE, "type": "recovery_slot_tamper", "slot": "A",
          "committed_version": "2.0.0", "committed_svn": 2,
          "expected_tree_sha256": "a1" * 32, "observed_tree_sha256": "b2" * 32,
          "detail": "recovery application tree hash mismatch", "source": "192.0.2.50:41000",
          "gateway_state": "closed", "gateway_log_tail": [], "cluster": "unaffected"}


@pytest.fixture
def server(tmp_path, order_keys):
    releases = tmp_path / "releases"
    (releases / "v2").mkdir(parents=True)
    (releases / "v2/release.json").write_text('{"ok": true}')
    (tmp_path / "store-secret.txt").write_text("outside the release directory")
    running = IncidentServer(("127.0.0.1", 0), releases, tmp_path / "incidents.json", order_keys[0],
                             release_labels={"2.0.0": "v2"})
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    yield running
    running.shutdown()
    running.server_close()


def call(server, method, path, body=None, content_type="application/json"):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    payload = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b"")
    connection.request(method, path, body=payload or None, headers={"Content-Type": content_type})
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, data


def report(server, **changes):
    status, data = call(server, "POST", "/incidents", dict(REPORT, **changes))
    return status, json.loads(data)["id"]


def fetch_order(server, incident, public):
    status, data = call(server, "GET", f"/incidents/{incident}/order")
    assert status == 200
    body = json.loads(data)
    return verify_order(base64.b64decode(body["order"]), base64.b64decode(body["signature"]), public,
                        vehicle="PI2-001", nonce=NONCE, now=time.time())


def test_report_approve_then_signed_restore_order(server, order_keys):
    status, incident = report(server)
    assert status == 201
    assert call(server, "GET", f"/incidents/{incident}/order")[0] == 204
    # The form body names another version; the server must ignore it.
    assert call(server, "POST", f"/incidents/{incident}/approve", b"version=1.0.0",
                "application/x-www-form-urlencoded")[0] == 303
    order = fetch_order(server, incident, order_keys[1])
    assert order["action"] == "restore" and order["version"] == "2.0.0" and order["release"] == "v2"
    assert order["incident"] == incident


def test_reject_produces_reject_order(server, order_keys):
    _status, incident = report(server)
    assert call(server, "POST", f"/incidents/{incident}/reject")[0] == 303
    order = fetch_order(server, incident, order_keys[1])
    assert order["action"] == "reject" and order["release"] is None


def test_cannot_approve_after_reject(server, order_keys):
    _status, incident = report(server)
    call(server, "POST", f"/incidents/{incident}/reject")
    assert call(server, "POST", f"/incidents/{incident}/approve")[0] == 409
    assert fetch_order(server, incident, order_keys[1])["action"] == "reject"


def test_duplicate_report_returns_same_incident(server):
    first_status, first = report(server)
    second_status, second = report(server)
    assert (first_status, second_status) == (201, 200) and first == second
    assert len(server.incidents) == 1


def test_unknown_committed_version_cannot_be_approved(server):
    _status, incident = report(server, committed_version="9.9.9", nonce="ee" * 16)
    assert call(server, "POST", f"/incidents/{incident}/approve")[0] == 409
    assert call(server, "GET", f"/incidents/{incident}/order")[0] == 204


def test_outcome_closes_only_decided_incidents(server):
    _status, incident = report(server)
    assert call(server, "POST", f"/incidents/{incident}/outcome", {"result": "rolled_back"})[0] == 409
    call(server, "POST", f"/incidents/{incident}/approve")
    assert call(server, "POST", f"/incidents/{incident}/outcome", {"result": "rolled_back"})[0] == 200
    assert server.incidents[0]["status"] == "closed" and server.incidents[0]["outcome"]["result"] == "rolled_back"


@pytest.mark.parametrize("bad", [{"vehicle": ""}, {"nonce": "not hex!"}, {"committed_version": "two"}])
def test_invalid_report_is_rejected(server, bad):
    assert call(server, "POST", "/incidents", dict(REPORT, **bad))[0] == 400


def test_index_lists_incident_and_escapes_values(server):
    report(server, detail="<script>alert(1)</script>")
    status, page = call(server, "GET", "/")
    text = page.decode()
    visible = re.sub(r"<[^>]+>", "", text).replace("\n", "")
    assert status == 200
    # Differing characters are wrapped in highlight tags, so compare what the operator reads.
    for name in ("expected_tree_sha256", "observed_tree_sha256"):
        assert REPORT[name][:32] in visible and REPORT[name][32:] in visible
    assert "<script>alert(1)" not in text and "&lt;script&gt;" in text


def test_releases_are_served_and_traversal_is_blocked(server):
    assert call(server, "GET", "/releases/v2/release.json")[0] == 200
    assert call(server, "GET", "/releases/../store-secret.txt")[0] == 404
    assert call(server, "GET", "/releases/")[0] == 404


def test_incidents_survive_a_server_restart(server, order_keys, tmp_path):
    _status, incident = report(server)
    call(server, "POST", f"/incidents/{incident}/approve")
    restarted = IncidentServer(("127.0.0.1", 0), tmp_path / "releases", tmp_path / "incidents.json",
                               order_keys[0], release_labels={"2.0.0": "v2"})
    try:
        assert [item["id"] for item in restarted.incidents] == [incident]
        assert restarted.incidents[0]["status"] == "approved"
        assert restarted.order_for(incident)[0] == 200
        created, second = restarted.add_report(dict(REPORT, nonce="cd" * 16))
        assert created and second != incident  # numbering continues, no ID is reused
        assert restarted.add_report(REPORT) == (False, incident)  # the old report is still recognised
    finally:
        restarted.server_close()


def test_corrupt_store_is_not_overwritten(tmp_path, order_keys):
    store = tmp_path / "incidents.json"
    store.write_text("{not json")
    with pytest.raises(ValueError, match="incident store"):
        IncidentServer(("127.0.0.1", 0), tmp_path, store, order_keys[0], release_labels={})
    assert store.read_text() == "{not json"


def test_index_highlights_only_the_characters_that_differ(server):
    expected, observed = "a" * 64, "a" * 60 + "b" * 4
    report(server, expected_tree_sha256=expected, observed_tree_sha256=observed)
    text = call(server, "GET", "/")[1].decode()
    assert text.count('<span class="d">bbbb</span>') == 1      # observed: the 4 wrong characters
    assert text.count('<span class="d">aaaa</span>') == 1      # expected: the same 4 positions
    assert "4/64자가 다름" in text


def test_matching_hashes_are_not_flagged(server):
    same = "c" * 64
    report(server, expected_tree_sha256=same, observed_tree_sha256=same)
    text = call(server, "GET", "/")[1].decode()
    assert '<span class="d">' not in text and "해시 일치" in text


# --- hardening (deferred minors) -------------------------------------------------------------
def post_form(server, path, **headers):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    connection.request("POST", path, body=b"", headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, data


def test_cross_origin_decision_is_refused(server):
    _status, incident = report(server)
    host = f"127.0.0.1:{server.server_address[1]}"
    assert post_form(server, f"/incidents/{incident}/approve", Origin="http://evil.example")[0] == 403
    assert post_form(server, f"/incidents/{incident}/approve", **{"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert server.incidents[0]["status"] == "reported"
    assert post_form(server, f"/incidents/{incident}/approve", Origin=f"http://{host}")[0] == 303
    assert server.incidents[0]["status"] == "approved"


def test_outcome_and_reports_need_the_token_when_configured(tmp_path, order_keys):
    secured = IncidentServer(("127.0.0.1", 0), tmp_path, tmp_path / "s.json", order_keys[0],
                             release_labels={"2.0.0": "v2"}, api_token="s3cret-token")
    threading.Thread(target=secured.serve_forever, daemon=True).start()
    try:
        def send(path, body, token=None):
            connection = http.client.HTTPConnection("127.0.0.1", secured.server_address[1], timeout=5)
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            connection.request("POST", path, body=json.dumps(body).encode(), headers=headers)
            response = connection.getresponse()
            response.read()
            connection.close()
            return response.status
        assert send("/incidents", REPORT) == 401
        assert send("/incidents", REPORT, "wrong") == 401
        assert send("/incidents", REPORT, "s3cret-token") == 201
        assert send("/incidents/INC-0001/outcome", {"result": "rolled_back"}) == 401
        assert secured.incidents[0]["status"] == "reported"
    finally:
        secured.shutdown()
        secured.server_close()


def test_an_unknown_committed_version_is_shown_as_its_number(server):
    report(server, committed_version="9.9.9")
    _status, page = call(server, "GET", "/")
    assert "9.9.9" in page.decode()


def test_decision_button_names_the_reported_version_and_conflict_is_readable(server):
    _status, incident = report(server, committed_version="2.0.0")
    _status, page = call(server, "GET", "/")
    assert "V2로 복원 승인" in page.decode() and "2.0.0로" not in page.decode()      # the product name, not the build number
    assert post_form(server, f"/incidents/{incident}/approve")[0] == 303
    status, body = post_form(server, f"/incidents/{incident}/reject", Accept="text/html")
    assert status == 409 and "이미 처리" in body.decode("utf-8")


def test_stalled_connection_is_dropped(tmp_path, order_keys):
    import socket
    slow = IncidentServer(("127.0.0.1", 0), tmp_path, tmp_path / "s.json", order_keys[0],
                          release_labels={}, request_timeout=0.3)
    threading.Thread(target=slow.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(("127.0.0.1", slow.server_address[1]), timeout=3) as stalled:
            stalled.sendall(b"POST /incidents HTTP/1.1\r\nContent-Le")  # never finishes
            started = time.monotonic()
            assert stalled.recv(100) == b""  # the server hangs up instead of waiting forever
            assert time.monotonic() - started < 2.5
    finally:
        slow.shutdown()
        slow.server_close()
