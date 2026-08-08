"""经人工确认的故障记忆模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class IncidentMemoryCreate(BaseModel):
    """只有显式确认的复盘才能进入故障记忆库。"""

    session_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=2, max_length=200)
    service_name: str = Field(min_length=1, max_length=120)
    symptoms: list[str] = Field(min_length=1, max_length=20)
    alerts: list[str] = Field(default_factory=list, max_length=20)
    key_evidence: list[str] = Field(min_length=1, max_length=30)
    confirmed_root_cause: str = Field(min_length=3, max_length=4000)
    resolution: str = Field(min_length=3, max_length=4000)
    verification: str = Field(min_length=3, max_length=2000)
    diagnosis_report: str = Field(min_length=1, max_length=30000)
    source: Literal["live_local", "github_replay", "manual"] = "live_local"
    reviewer: str = Field(default="local_operator", min_length=1, max_length=120)
    confirmed: Literal[True]

    @field_validator("symptoms", "alerts", "key_evidence")
    @classmethod
    def normalize_items(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(values):
            raise ValueError("列表中不能包含空项")
        if any(len(value) > 500 for value in normalized):
            raise ValueError("单项内容不能超过 500 个字符")
        return normalized


class IncidentMemory(BaseModel):
    incident_id: str
    session_id: str
    title: str
    service_name: str
    symptoms: list[str]
    alerts: list[str]
    key_evidence: list[str]
    confirmed_root_cause: str
    resolution: str
    verification: str
    diagnosis_report: str
    source: str
    reviewer: str
    confirmed_at: datetime
    vector_source: str


class IncidentMemorySearchResult(BaseModel):
    incident_id: str
    title: str
    service_name: str
    confirmed_root_cause: str
    resolution: str
    source: str
    confirmed_at: str
    score: float
