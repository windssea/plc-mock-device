"""Small helpers that keep adapters independent from the config implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable


def value(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from a Pydantic model, dataclass, or plain mapping."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def scalar(obj: Any) -> Any:
    """Return the serialized value of Enum-like configuration fields."""
    return obj.value if isinstance(obj, Enum) else obj


def text(obj: Any, default: str = "") -> str:
    raw = scalar(obj)
    return default if raw is None else str(raw)


def endpoint(config: Any, name: str) -> Any:
    """Accept either an AppConfig or an already selected endpoint config."""
    nested = value(config, name)
    return nested if nested is not None else config


def configured_devices(config: Any, devices: Iterable[Any] | None) -> list[Any]:
    if devices is not None:
        return list(devices)
    return list(value(config, "devices", []) or [])


def protocol_devices(devices: Iterable[Any], protocol: str) -> list[Any]:
    return [
        device
        for device in devices
        if bool(value(device, "enabled", True))
        and text(value(device, "protocol")).lower() == protocol
    ]


def device_id(device: Any) -> str:
    return text(value(device, "id", value(device, "device_id")))


def signal_id(signal: Any) -> str:
    return text(value(signal, "id", value(signal, "signal_id")))


def device_protocol_config(device: Any) -> Any:
    return value(device, "protocol_config", {}) or {}


def signal_binding(signal: Any) -> Any:
    return value(signal, "binding", {}) or {}


def sample_field(sample: Any, name: str, default: Any = None) -> Any:
    aliases = {
        "device_id": ("device_id", "deviceId"),
        "signal_id": ("signal_id", "signalId"),
        "timestamp_utc": ("timestamp_utc", "timestamp", "timestampUtc"),
    }
    for candidate in aliases.get(name, (name,)):
        found = value(sample, candidate, None)
        if found is not None:
            return found
    return default


def utc_datetime(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw.astimezone(timezone.utc)
    if isinstance(raw, str):
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def iso_utc(raw: Any) -> str:
    return utc_datetime(raw).isoformat().replace("+00:00", "Z")

