"""AIOps 请求与响应模型。"""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class AIOpsRequest(BaseModel):
    """支持实时告警与手工描述两种诊断模式。"""

    session_id: str = Field(
        default_factory=lambda: uuid4().hex,
        description="本次诊断的唯一会话 ID",
    )
    mode: Literal["realtime", "manual"] = "realtime"
    service_name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_manual_input(self) -> "AIOpsRequest":
        if self.mode == "manual" and not (self.description or "").strip():
            raise ValueError("手工诊断模式必须填写故障现象")
        return self


class AlertInfo(BaseModel):
    alertname: str
    severity: str
    instance: str
    duration: str
    description: str | None = None


class DiagnosisResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: dict
