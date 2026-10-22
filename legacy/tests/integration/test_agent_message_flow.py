import json

from capstone_ota.agent.updater import AgentMessageRouter
from tests.unit.test_updater import setup_update


def test_router_accepts_only_exact_device_topic_and_deduplicates_qos_one_message(tmp_path):
    agent, command, statuses, calls, _config, _bundle = setup_update(tmp_path)
    router = AgentMessageRouter("cluster-pi-01", agent)

    assert router.handle("capstone/other-pi/ota/command", command) is None
    first = router.handle("capstone/cluster-pi-01/ota/command", command)
    second = router.handle("capstone/cluster-pi-01/ota/command", command)

    assert first == second
    assert calls["download"] == 1
    assert statuses[-1]["stage"] == "success"


def test_router_reports_older_signed_release_as_rollback_rejected(tmp_path):
    agent, command, statuses, calls, config, _bundle = setup_update(
        tmp_path, version="1.0.0", current_version="1.0.0"
    )
    config.state_file.parent.mkdir(parents=True)
    config.state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_version": "1.0.0",
                "previous_version": None,
                "completed_jobs": {},
                "failed_versions": [],
                "active_job": None,
            }
        )
    )

    result = AgentMessageRouter("cluster-pi-01", agent).handle(
        "capstone/cluster-pi-01/ota/command", command
    )

    assert result.error_code == "ROLLBACK_REJECTED"
    assert calls["download"] == 0
    assert statuses[-1]["error"]["code"] == "ROLLBACK_REJECTED"
