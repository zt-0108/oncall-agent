import json
from pathlib import Path

from fastapi.testclient import TestClient

import fault_lab.app as fault_app


def test_external_case_catalog_has_pinned_provenance() -> None:
    path = Path("fault_lab/datasets/external_cases.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["data_origin"] == "external_benchmark"
    assert payload["sources"][0]["commit_sha"] == "4695aa69f4f1f57b9094ca04ff235908b73a8e24"
    assert payload["sources"][0]["license"] == "MIT"
    assert {case["fault_class"] for case in payload["cases"]} == {
        "cpu",
        "mem",
        "delay",
        "loss",
        "socket",
        "disk",
    }
    assert all(case["data_origin"] == "replay_external" for case in payload["cases"])
    assert all(case["safe"] for case in payload["cases"])


def test_external_memory_case_generates_live_replay_evidence(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "fault-lab.jsonl"
    monkeypatch.setattr(fault_app, "LOG_PATH", log_path)
    client = TestClient(fault_app.app)

    try:
        activated = client.post("/api/scenarios/rcaeval-memory-pressure/activate")
        first = client.post("/api/checkout")
        second = client.post("/api/checkout")
        metrics = client.get("/metrics").text
    finally:
        client.post("/api/reset")

    assert activated.status_code == 200
    assert first.status_code == 200
    assert second.status_code == 500
    assert 'fault_lab_resource_pressure{resource="memory",service="cartservice"} 96.0' in metrics
    logs = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    error = next(item for item in logs if item.get("event") == "memory_allocation_failed")
    assert error["data_origin"] == "live_local"
    assert error["scenario_provenance"] == "replay_external"
    assert error["benchmark"] == "RCAEval RE2"
