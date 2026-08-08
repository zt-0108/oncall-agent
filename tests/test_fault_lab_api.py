import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.api import fault_lab


def test_control_rejects_non_local_request_without_token(monkeypatch) -> None:
    monkeypatch.setattr(fault_lab.config, "fault_lab_enabled", True)
    monkeypatch.setattr(fault_lab.config, "fault_lab_control_token", None)

    with pytest.raises(HTTPException) as error:
        fault_lab._authorize_control(None, "192.168.1.50")

    assert error.value.status_code == 403


def test_control_accepts_loopback_request_without_token(monkeypatch) -> None:
    monkeypatch.setattr(fault_lab.config, "fault_lab_enabled", True)
    monkeypatch.setattr(fault_lab.config, "fault_lab_control_token", None)

    fault_lab._authorize_control(None, "127.0.0.1")


def test_control_accepts_matching_token(monkeypatch) -> None:
    monkeypatch.setattr(fault_lab.config, "fault_lab_enabled", True)
    monkeypatch.setattr(fault_lab.config, "fault_lab_control_token", SecretStr("local-test-token"))

    fault_lab._authorize_control("local-test-token", "192.168.1.50")
