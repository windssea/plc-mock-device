"""Application service that owns configuration, schedulers and protocol adapters."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters import MQTTAdapter, ModbusAdapter, OPCUAAdapter
from .config_store import ConfigConflictError, ConfigStore
from .generators import Generator, create_generator
from .models import AppConfig, DeviceConfig, MqttConnectionConfig, MqttDeviceProtocolConfig, ProtocolType
from .state import ActualState, DeviceRuntimeStatus, DesiredState, Sample

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ApplyResult:
    config: AppConfig
    restarted_adapters: list[str] = field(default_factory=list)
    restarted_devices: list[str] = field(default_factory=list)
    restart_required: list[str] = field(default_factory=list)


class RuntimeManager:
    """Coordinates transactional configuration changes and runtime resources."""

    def __init__(self, config_path: str, database_path: str | None = None) -> None:
        bootstrap = Path(config_path)
        database = Path(database_path) if database_path is not None else bootstrap.with_suffix(".db")
        self.store = ConfigStore(database, bootstrap_path=bootstrap)
        self.config: AppConfig | None = None
        self.config_error: str | None = None
        self.adapters = {
            ProtocolType.OPCUA: OPCUAAdapter(),
            ProtocolType.MQTT: MQTTAdapter(),
            ProtocolType.MODBUS: ModbusAdapter(),
        }
        self.device_statuses: dict[str, DeviceRuntimeStatus] = {}
        self._device_tasks: dict[str, asyncio.Task[None]] = {}
        self._generators: dict[tuple[str, str], Generator] = {}
        self._write_lock = asyncio.Lock()
        self._closing = False

    async def start(self) -> None:
        try:
            config = await self.store.load()
        except Exception as exc:
            self.config_error = str(exc)
            logger.error("Configuration load failed: %s", exc)
            return
        self.config = config
        self.config_error = None
        await self._start_all_adapters(config, strict=False)
        await self._sync_device_tasks(None, config)

    async def stop(self) -> None:
        self._closing = True
        tasks = list(self._device_tasks.values())
        self._device_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(
            *(adapter.stop() for adapter in self.adapters.values()),
            return_exceptions=True,
        )

    async def apply_config(self, candidate: AppConfig) -> ApplyResult:
        async with self._write_lock:
            old = self.config
            if old is not None and candidate.config_revision != old.config_revision:
                raise ConfigConflictError(
                    f"revision conflict: expected {candidate.config_revision}, "
                    f"current {old.config_revision}"
                )
            expected = old.config_revision if old is not None else candidate.config_revision
            saved = await self.store.save(
                candidate,
                expected_revision=expected,
            )
            restarted_adapters = self._affected_adapters(old, saved)
            restarted_devices = self._affected_devices(old, saved)
            restart_required = ["web"] if old is not None and old.web != saved.web else []
            try:
                for protocol in restarted_adapters:
                    adapter = self.adapters[ProtocolType(protocol)]
                    await adapter.reconfigure(saved, saved.devices)
                await self._sync_device_tasks(old, saved, restarted_devices)
                self.config = saved
                self.config_error = None
                self._set_all_revisions(saved.config_revision)
                return ApplyResult(
                    saved, restarted_adapters, restarted_devices, restart_required
                )
            except Exception as exc:
                logger.exception("Applying revision %s failed", saved.config_revision)
                try:
                    await self.store.restore(
                        old,
                        expected_revision=saved.config_revision,
                    )
                    if old is not None:
                        await self._restore_runtime(old)
                        self.config = old
                    else:
                        await self._stop_runtime()
                        self.config = None
                except Exception as rollback_exc:
                    self.config_error = f"apply failed: {exc}; rollback failed: {rollback_exc}"
                    logger.exception("Configuration rollback failed")
                    raise RuntimeError(self.config_error) from rollback_exc
                raise RuntimeError(f"configuration was rolled back: {exc}") from exc

    async def replace_device(self, old_id: str | None, device: DeviceConfig) -> ApplyResult:
        if self.config is None:
            raise RuntimeError("no valid configuration is loaded")
        devices = [item.model_copy(deep=True) for item in self.config.devices]
        if old_id is None:
            if any(item.id == device.id for item in devices):
                raise ValueError(f"device already exists: {device.id}")
            devices.append(device)
        else:
            index = next((i for i, item in enumerate(devices) if item.id == old_id), None)
            if index is None:
                raise KeyError(old_id)
            if device.id != old_id and any(item.id == device.id for item in devices):
                raise ValueError(f"device already exists: {device.id}")
            devices[index] = device
        return await self.apply_config(self.config.model_copy(update={"devices": devices}, deep=True))

    async def delete_device(self, device_id: str) -> ApplyResult:
        if self.config is None:
            raise RuntimeError("no valid configuration is loaded")
        devices = [item for item in self.config.devices if item.id != device_id]
        if len(devices) == len(self.config.devices):
            raise KeyError(device_id)
        return await self.apply_config(self.config.model_copy(update={"devices": devices}, deep=True))

    async def replace_mqtt_connection(
        self, old_id: str | None, connection: MqttConnectionConfig
    ) -> ApplyResult:
        if self.config is None:
            raise RuntimeError("no valid configuration is loaded")
        connections = [item.model_copy(deep=True) for item in self.config.mqtt_connections]
        if old_id is None:
            if any(item.id == connection.id for item in connections):
                raise ValueError(f"MQTT connection already exists: {connection.id}")
            connections.append(connection)
        else:
            index = next((i for i, item in enumerate(connections) if item.id == old_id), None)
            if index is None:
                raise KeyError(old_id)
            if connection.id != old_id:
                raise ValueError("MQTT connection id cannot be changed")
            connections[index] = connection
        updates: dict[str, Any] = {"mqtt_connections": connections}
        if connection.id == "default":
            updates["mqtt"] = connection.to_endpoint()
        candidate = AppConfig.model_validate(
            self.config.model_copy(update=updates, deep=True).model_dump(mode="python")
        )
        return await self.apply_config(candidate)

    async def delete_mqtt_connection(self, connection_id: str) -> ApplyResult:
        if self.config is None:
            raise RuntimeError("no valid configuration is loaded")
        if connection_id == "default":
            raise ConfigConflictError("default MQTT connection cannot be deleted")
        if not any(item.id == connection_id for item in self.config.mqtt_connections):
            raise KeyError(connection_id)
        referenced = [
            item.id for item in self.config.devices
            if isinstance(item.protocol_config, MqttDeviceProtocolConfig)
            and item.protocol_config.connection_id == connection_id
        ]
        if referenced:
            raise ConfigConflictError(
                f"MQTT connection {connection_id} is used by devices: {', '.join(referenced)}"
            )
        connections = [
            item for item in self.config.mqtt_connections if item.id != connection_id
        ]
        candidate = AppConfig.model_validate(
            self.config.model_copy(
                update={"mqtt_connections": connections}, deep=True
            ).model_dump(mode="python")
        )
        return await self.apply_config(candidate)

    async def set_desired_state(self, device_id: str, desired: DesiredState) -> ApplyResult:
        if self.config is None:
            raise RuntimeError("no valid configuration is loaded")
        device = next((item for item in self.config.devices if item.id == device_id), None)
        if device is None:
            raise KeyError(device_id)
        if device.desired_state == desired.value:
            return ApplyResult(self.config)
        updated = device.model_copy(update={"desired_state": desired.value}, deep=True)
        return await self.replace_device(device_id, updated)

    async def _start_all_adapters(self, config: AppConfig, *, strict: bool) -> None:
        for protocol, adapter in self.adapters.items():
            try:
                await adapter.start(config, config.devices)
            except Exception as exc:
                logger.exception("%s adapter failed to start", protocol.value)
                if strict:
                    raise

    async def _stop_runtime(self) -> None:
        tasks = list(self._device_tasks.values())
        self._device_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(a.stop() for a in self.adapters.values()), return_exceptions=True)

    async def _restore_runtime(self, config: AppConfig) -> None:
        await self._stop_runtime()
        await self._start_all_adapters(config, strict=True)
        self.device_statuses.clear()
        self._generators.clear()
        await self._sync_device_tasks(None, config)

    def _affected_adapters(self, old: AppConfig | None, new: AppConfig) -> list[str]:
        if old is None:
            return [item.value for item in ProtocolType]
        affected: list[str] = []
        for protocol in ProtocolType:
            if getattr(old, protocol.value) != getattr(new, protocol.value):
                affected.append(protocol.value)
                continue
            if (
                protocol is ProtocolType.MQTT
                and old.mqtt_connections != new.mqtt_connections
            ):
                affected.append(protocol.value)
                continue
            old_topology = self._protocol_topology(old, protocol)
            new_topology = self._protocol_topology(new, protocol)
            if old_topology != new_topology:
                affected.append(protocol.value)
        return affected

    @staticmethod
    def _protocol_topology(config: AppConfig, protocol: ProtocolType) -> list[dict[str, Any]]:
        result = []
        for device in config.devices:
            if device.protocol != protocol:
                continue
            item: dict[str, Any] = {
                "id": device.id,
                "enabled": device.enabled,
                "protocol_config": device.protocol_config.model_dump(mode="json"),
                "signals": [],
            }
            if protocol is ProtocolType.MQTT:
                item["default_interval_ms"] = device.default_interval_ms
            for signal in device.signals:
                item["signals"].append(
                    {
                        "id": signal.id,
                        "name": signal.name,
                        "unit": signal.unit,
                        "value_type": signal.value_type.value,
                        "binding": signal.binding.model_dump(mode="json"),
                    }
                )
            result.append(item)
        return result

    @staticmethod
    def _affected_devices(old: AppConfig | None, new: AppConfig) -> list[str]:
        if old is None:
            return [item.id for item in new.devices]
        old_map = {item.id: item.model_dump(mode="json") for item in old.devices}
        new_map = {item.id: item.model_dump(mode="json") for item in new.devices}
        return sorted(
            device_id
            for device_id in old_map.keys() | new_map.keys()
            if old_map.get(device_id) != new_map.get(device_id)
        )

    async def _sync_device_tasks(
        self,
        old: AppConfig | None,
        new: AppConfig,
        affected: list[str] | None = None,
    ) -> None:
        affected_ids = set(affected if affected is not None else self._affected_devices(old, new))
        new_map = {item.id: item for item in new.devices}
        old_map = {item.id: item for item in old.devices} if old is not None else {}
        resume_values: set[str] = set()
        for device_id in affected_ids:
            old_device = old_map.get(device_id)
            new_device = new_map.get(device_id)
            if old_device is None or new_device is None:
                continue
            old_data = old_device.model_dump(mode="json")
            new_data = new_device.model_dump(mode="json")
            old_data.pop("desired_state", None)
            new_data.pop("desired_state", None)
            if old_data == new_data:
                resume_values.add(device_id)
        for device_id in affected_ids:
            task = self._device_tasks.pop(device_id, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            for key in [key for key in self._generators if key[0] == device_id]:
                self._generators.pop(key, None)
            if device_id not in new_map:
                self.device_statuses.pop(device_id, None)
        for device_id in affected_ids:
            device = new_map.get(device_id)
            if device is None:
                continue
            previous_values = self.device_statuses.get(device_id, DeviceRuntimeStatus(
                device_id=device.id, protocol=device.protocol
            )).latest_values
            status = DeviceRuntimeStatus(
                device_id=device.id,
                protocol=device.protocol,
                desired_state=DesiredState(device.desired_state),
                actual_state=ActualState.STOPPED,
                config_revision=new.config_revision,
                latest_values=previous_values,
            )
            self.device_statuses[device_id] = status
            set_active = getattr(self.adapters[device.protocol], "set_device_active", None)
            if callable(set_active):
                set_active(device.id, device.enabled and device.desired_state == DesiredState.RUNNING.value)
            if device.enabled and device.desired_state == DesiredState.RUNNING.value:
                status.actual_state = ActualState.STARTING
                status.last_transition_at = _utc_now()
                self._device_tasks[device_id] = asyncio.create_task(
                    self._run_device(
                        device,
                        new.random_seed,
                        resume_from=status.latest_values if device_id in resume_values else None,
                    ),
                    name=f"device-{device_id}",
                )

    async def _run_device(
        self,
        device: DeviceConfig,
        global_seed: int,
        resume_from: dict[str, Sample] | None = None,
    ) -> None:
        status = self.device_statuses[device.id]
        for signal in device.signals:
            generator = create_generator(signal, global_seed, device.id)
            previous = (resume_from or {}).get(signal.id)
            if previous is not None and hasattr(generator, "current"):
                generator.current = previous.value  # type: ignore[attr-defined]
            self._generators[(device.id, signal.id)] = generator
        loop = asyncio.get_running_loop()
        next_due = {signal.id: loop.time() for signal in device.signals}
        status.actual_state = ActualState.RUNNING
        status.last_error = None
        status.last_transition_at = _utc_now()
        try:
            while True:
                if not device.signals:
                    await asyncio.sleep(1)
                    continue
                now = loop.time()
                due = [signal for signal in device.signals if next_due[signal.id] <= now]
                if not due:
                    await asyncio.sleep(max(0, min(next_due.values()) - now))
                    continue
                for signal in due:
                    interval = (signal.interval_ms or device.default_interval_ms) / 1000.0
                    next_due[signal.id] = now + interval
                    generator = self._generators[(device.id, signal.id)]
                    sample = Sample(
                        device_id=device.id,
                        signal_id=signal.id,
                        value=generator.next_value(),
                        value_type=signal.value_type,
                    )
                    status.latest_values[signal.id] = sample
                    try:
                        await self.adapters[device.protocol].update(sample)
                        if status.actual_state == ActualState.DEGRADED:
                            status.actual_state = ActualState.RUNNING
                            status.last_error = None
                    except Exception as exc:
                        status.actual_state = ActualState.DEGRADED
                        status.last_error = str(exc)
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            status.actual_state = ActualState.STOPPED
            status.last_transition_at = _utc_now()
            raise
        except Exception as exc:
            status.actual_state = ActualState.ERROR
            status.last_error = str(exc)
            status.last_transition_at = _utc_now()
            logger.exception("Device %s scheduler failed", device.id)

    def _set_all_revisions(self, revision: int) -> None:
        for status in self.device_statuses.values():
            status.config_revision = revision

    def status_dict(self) -> dict[str, Any]:
        adapters: dict[str, Any] = {}
        for protocol, adapter in self.adapters.items():
            raw = adapter.status_dict()
            if raw["last_error"]:
                state = "degraded" if raw["running"] else "error"
            elif raw["running"] and (raw["connected"] or protocol is not ProtocolType.MQTT):
                state = "running"
            elif raw["running"]:
                state = "degraded"
            else:
                state = "stopped"
            adapters[protocol.value] = {
                "state": state,
                "connected": raw["connected"],
                "last_error": raw["last_error"],
                "last_transition_at": raw["last_transition_at"],
                "dropped_messages": raw["dropped_messages"],
            }
        return {
            "config_revision": self.config.config_revision if self.config else 0,
            "config_error": self.config_error,
            "adapters": adapters,
            "devices": [
                status.model_dump(mode="json")
                for status in sorted(self.device_statuses.values(), key=lambda item: item.device_id)
            ],
        }
