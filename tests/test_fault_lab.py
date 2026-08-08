import json
import time

from fastapi.testclient import TestClient

import fault_lab.app as fault_app


def test_database_error_generates_real_metric_and_structured_log(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "fault-lab.jsonl"
    monkeypatch.setattr(fault_app, "LOG_PATH", log_path)
    client = TestClient(fault_app.app)

    try:
        activated = client.post("/api/scenarios/database-error/activate")
        response = client.post("/api/checkout", headers={"x-trace-id": "trace-test-db"})
        metrics = client.get("/metrics").text
    finally:
        client.post("/api/reset")

    assert activated.status_code == 200
    assert response.status_code == 500
    assert response.json()["trace_id"] == "trace-test-db"
    assert "fault_lab_database_failures_total" in metrics

    logs = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    error = next(item for item in logs if item.get("trace_id") == "trace-test-db")
    assert error["data_origin"] == "live_local"
    assert error["event"] == "database_connection_refused"
    assert error["status"] == 500


def test_catalog_exposes_ground_truth_without_activating_fault() -> None:
    client = TestClient(fault_app.app)
    response = client.get("/api/scenarios")

    assert response.status_code == 200
    scenarios = {item["id"]: item for item in response.json()["scenarios"]}
    assert scenarios["dependency-timeout"]["ground_truth"] == "支付服务调用超时"
    assert all(item["safe"] for item in scenarios.values())


def test_fault_scenario_auto_resets_after_safety_deadline(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "fault-lab.jsonl"
    monkeypatch.setattr(fault_app, "LOG_PATH", log_path)
    monkeypatch.setattr(fault_app, "SCENARIO_TTL_SECONDS", 1)
    client = TestClient(fault_app.app)

    client.post("/api/scenarios/high-latency/activate")
    monkeypatch.setattr(fault_app, "_scenario_deadline", time.monotonic() - 1)
    state = client.get("/api/state").json()

    assert state["active_scenario"]["id"] == "normal"
    assert state["expires_at"] is None
    logs = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert any(item["event"] == "scenario_auto_reset" for item in logs)


def test_fault_log_redacts_secrets_and_rotates(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "fault-lab.jsonl"
    monkeypatch.setattr(fault_app, "LOG_PATH", log_path)
    monkeypatch.setattr(fault_app, "LOG_MAX_BYTES", 240)
    monkeypatch.setattr(fault_app, "LOG_BACKUP_COUNT", 2)

    fault_app._append_log(
        level="INFO",
        event="secret_test",
        authorization="Bearer top-secret-token",
        message="api_key=should-not-leak",
    )
    fault_app._append_log(
        level="INFO",
        event="rotation_test",
        token="another-secret",
        message="x" * 160,
    )

    assert log_path.exists()
    backup_path = tmp_path / "fault-lab.jsonl.1"
    assert backup_path.exists()
    combined = log_path.read_text(encoding="utf-8") + backup_path.read_text(encoding="utf-8")
    assert "top-secret-token" not in combined
    assert "should-not-leak" not in combined
    assert "another-secret" not in combined
    assert "[REDACTED]" in combined
