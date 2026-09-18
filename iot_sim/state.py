"""JSON-safe runtime state models shared by the API and adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .models import ProtocolType, ValueType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DesiredState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


class ActualState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    ERROR = "error"


class AdapterState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    ERROR = "error"


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Sample(RuntimeModel):
    device_id: str
    signal_id: str
    value: bool | int | float
    value_type: ValueType
    timestamp_utc: datetime = Field(default_factory=utc_now)
    quality: str = "Good"


class DeviceRuntimeStatus(RuntimeModel):
    device_id: str
    protocol: ProtocolType
    desired_state: DesiredState = DesiredState.STOPPED
    actual_state: ActualState = ActualState.STOPPED
    config_revision: int = Field(default=0, ge=0)
    last_error: str | None = None
    last_transition_at: datetime = Field(default_factory=utc_now)
    latest_values: dict[str, Sample] = Field(default_factory=dict)


class AdapterRuntimeStatus(RuntimeModel):
    protocol: ProtocolType
    state: AdapterState = AdapterState.STOPPED
    last_error: str | None = None
    last_transition_at: datetime = Field(default_factory=utc_now)


class RuntimeStatus(RuntimeModel):
    config_revision: int = Field(default=0, ge=0)
    config_error: str | None = None
    adapters: dict[ProtocolType, AdapterRuntimeStatus] = Field(default_factory=dict)
    devices: dict[str, DeviceRuntimeStatus] = Field(default_factory=dict)

