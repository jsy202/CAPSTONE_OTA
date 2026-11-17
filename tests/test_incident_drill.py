"""End to end: attack through Pi1's gateway, Pi2 refuses V1, Pi1 reports, a human decision follows."""
import json

from demo.incident_drill import main, run


def test_drill_auto_approve_restores_v2(tmp_path):
    result = run(tmp_path / "out", decision="approve")
    assert result["final"]["result"] == "rolled_back" and result["final"]["version"] == "2.0.0"
    assert result["final"]["phase"] == "stable"
    assert result["response"]["action"] == "restored"
    assert result["v1_executed"] is False
    assert result["reattack_response"].startswith("ERROR gateway closed")
    # The attack really travelled through Pi1's gateway before Pi2 refused to run it.
    assert any(entry.get("slot") == "A" and entry.get("forwarded") for entry in result["gateway_log"])
    assert result["attack_response"] == "OK"
    assert result["forced_verdict"] is True
    assert json.loads((tmp_path / "out/result.json").read_text())["final"]["result"] == "rolled_back"


def test_drill_auto_reject_requires_maintenance(tmp_path):
    result = run(tmp_path / "out", decision="reject")
    assert result["final"]["result"] == "recovery_failed"
    assert result["response"]["action"] == "maintenance_required"
    assert result["v1_executed"] is False


def test_drill_command_line_entry(tmp_path, capsys):
    assert main(["run", "--output", str(tmp_path / "out"), "--auto-reject"]) == 0
    summary = capsys.readouterr().out
    assert "maintenance_required" in summary and (tmp_path / "out/result.json").exists()
