"""Shared Paho MQTT gateway publisher."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from typing import Any, Iterable

import paho.mqtt.client as mqtt

from ._compat import (
    configured_devices,
    device_id,
    device_protocol_config,
    endpoint,
    iso_utc,
    protocol_devices,
    sample_field,
    signal_binding,
    signal_id,
    text,
    value,
)
from .base import Adapter

logger = logging.getLogger(__name__)


class MQTTAdapter(Adapter):
    name = "mqtt"

    def __init__(self) -> None:
        super().__init__()
        self._client: mqtt.Client | None = None
        self._clients: dict[str, mqtt.Client] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._endpoint: Any = None
        self._connections: dict[str, Any] = {}
        self._connection_events: dict[str, asyncio.Event] = {}
        self._connection_errors: dict[str, str] = {}
        self._device_connections: dict[str, str] = {}
        self._device_config: dict[str, Any] = {}
        self._fields: dict[tuple[str, str], str] = {}
        self._latest: dict[str, dict[str, Any]] = defaultdict(dict)
        self._timestamps: dict[str, str] = {}
        self._dirty: set[str] = set()
        self._active: set[str] = set()
        self._publish_tasks: list[asyncio.Task[None]] = []
        self._connected = asyncio.Event()

    async def start(self, config: Any, devices: Iterable[Any] | None = None) -> None:
        if self.running:
            return
        self._latest.clear()
        self._timestamps.clear()
        self._dirty.clear()
        self._endpoint = endpoint(config, "mqtt")
        mqtt_devices = protocol_devices(configured_devices(config, devices), "mqtt")
        self._device_config = {device_id(device): device for device in mqtt_devices}
        self._active = {
            dev_id
            for dev_id, device in self._device_config.items()
            if text(value(device, "desired_state", "running")) == "running"
        }
        self._fields.clear()
        self._device_connections.clear()
        for dev_id, device in self._device_config.items():
            protocol_config = device_protocol_config(device)
            self._device_connections[dev_id] = text(
                value(protocol_config, "connection_id", "default"), "default"
            )
            for signal in value(device, "signals", []) or []:
                binding = signal_binding(signal)
                self._fields[(dev_id, signal_id(signal))] = text(
                    value(binding, "field_name", signal_id(signal))
                )

        configured_connections = list(value(config, "mqtt_connections", []) or [])
        if not configured_connections:
            configured_connections = [self._endpoint]
        self._connections = {
            text(value(item, "id", "default"), "default"): item
            for item in configured_connections
        }
        unknown = sorted(
            set(self._device_connections.values()) - set(self._connections)
        )
        if unknown:
            raise ValueError(
                f"MQTT devices reference disabled or missing connections: {', '.join(unknown)}"
            )
        enabled_connections = {
            key: item for key, item in self._connections.items()
            if bool(value(item, "enabled", True))
        }
        if not enabled_connections:
            self._status.last_error = None
            self._transition(running=False, connected=False)
            return
        self._loop = asyncio.get_running_loop()
        try:
            for connection_id, connection in enabled_connections.items():
                self._start_client(connection_id, connection)
            self._client = self._clients.get("default")
            self._connected = self._connection_events.get("default", asyncio.Event())
            self._publish_tasks = [
                asyncio.create_task(self._publish_loop(dev_id, device), name=f"mqtt-publish-{dev_id}")
                for dev_id, device in self._device_config.items()
            ]
            self._status.last_error = None
            self._transition(running=True, connected=False)
        except Exception as exc:
            self._status.last_error = str(exc)
            for client in self._clients.values():
                client.loop_stop()
            self._clients.clear()
            self._transition(running=False, connected=False)
            raise

    def _start_client(self, connection_id: str, connection: Any) -> None:
        protocol_text = text(value(connection, "protocol_version", "3.1.1"))
        protocol = mqtt.MQTTv5 if protocol_text in {"5", "5.0", "MQTTv5"} else mqtt.MQTTv311
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=text(value(connection, "client_id", "plcnext-iot-simulator")),
            clean_session=None if protocol == mqtt.MQTTv5 else True,
            protocol=protocol,
            transport=text(value(connection, "transport", "tcp"), "tcp"),
            reconnect_on_failure=True,
        )
        client.on_connect = lambda c, u, f, r, p, cid=connection_id: self._on_connect(cid, c, u, f, r, p)
        client.on_connect_fail = lambda c, u, cid=connection_id: self._on_connect_fail(cid, c, u)
        client.on_disconnect = lambda c, u, f, r, p, cid=connection_id: self._on_disconnect(cid, c, u, f, r, p)
        username = value(connection, "username")
        if username:
            password = value(connection, "password")
            password_env = value(connection, "password_env")
            if password is None and password_env:
                password = os.getenv(text(password_env))
            client.username_pw_set(text(username), None if password is None else text(password))
        if bool(value(connection, "tls_enabled", False)):
            client.tls_set()
        client.max_inflight_messages_set(int(value(connection, "max_inflight_messages", 20)))
        client.max_queued_messages_set(int(value(connection, "max_queued_messages", 1000)))
        client.reconnect_delay_set(
            min_delay=int(value(connection, "reconnect_min_seconds", 1)),
            max_delay=int(value(connection, "reconnect_max_seconds", 30)),
        )
        self._clients[connection_id] = client
        self._connection_events[connection_id] = asyncio.Event()
        client.connect_async(
            text(value(connection, "host", "127.0.0.1")),
            int(value(connection, "port", 1883)),
            int(value(connection, "keepalive", 60)),
        )
        client.loop_start()

    def _on_connect(self, connection_id: str, _client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        ok = reason_code == 0
        self._dispatch_connection_state(
            connection_id, ok,
            None if ok else f"MQTT connect failed: {reason_code}",
        )

    def _on_connect_fail(self, connection_id: str, _client: mqtt.Client, _userdata: Any) -> None:
        self._dispatch_connection_state(
            connection_id, False, "MQTT broker connection failed; retrying"
        )

    def _on_disconnect(self, connection_id: str, _client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        error = None if reason_code == 0 else f"MQTT disconnected: {reason_code}"
        self._dispatch_connection_state(connection_id, False, error)

    def _dispatch_connection_state(
        self, connection_id: str, connected: bool, error: str | None
    ) -> None:
        """Safely cross from Paho's thread while shutdown races the callback."""

        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(
                self._set_connected, connection_id, connected, error
            )
        except RuntimeError:
            # The loop can close after is_closed() and before scheduling.
            return

    def _set_connected(self, connection_id: str, connected: bool, error: str | None) -> None:
        event = self._connection_events.get(connection_id)
        if event is None:
            return
        if error:
            self._connection_errors[connection_id] = error
        if connected:
            self._connection_errors.pop(connection_id, None)
            event.set()
        else:
            event.clear()
        self._status.connected = bool(self._connection_events) and all(
            item.is_set() for item in self._connection_events.values()
        )
        self._status.last_error = "; ".join(
            f"{key}: {message}" for key, message in sorted(self._connection_errors.items())
        ) or None

    async def update(self, sample: Any) -> None:
        dev_id = text(sample_field(sample, "device_id"))
        if dev_id not in self._active:
            return
        sig_id = text(sample_field(sample, "signal_id"))
        field = self._fields.get((dev_id, sig_id))
        if field is None:
            return
        self._latest[dev_id][field] = sample_field(sample, "value")
        self._timestamps[dev_id] = iso_utc(sample_field(sample, "timestamp_utc"))
        self._dirty.add(dev_id)

    async def _publish_loop(self, dev_id: str, device: Any) -> None:
        protocol_config = device_protocol_config(device)
        interval_ms = int(
            value(protocol_config, "publish_interval_ms")
            or value(device, "default_interval_ms", 1000)
        )
        interval = max(1.0, interval_ms / 1000.0)
        try:
            while True:
                await asyncio.sleep(interval)
                event = self._connection_events.get(self._device_connections.get(dev_id, "default"))
                if dev_id not in self._active or dev_id not in self._dirty or event is None or not event.is_set():
                    continue
                await self._publish(dev_id, protocol_config)
        except asyncio.CancelledError:
            raise

    async def _publish(self, dev_id: str, protocol_config: Any) -> None:
        connection_id = self._device_connections.get(dev_id, "default")
        client = self._clients.get(connection_id)
        if client is None:
            return
        payload_mode = text(value(protocol_config, "payload_mode", "nested"))
        values = dict(self._latest.get(dev_id, {}))
        timestamp = self._timestamps.get(dev_id, iso_utc(None))
        if payload_mode == "values":
            payload = values
        elif payload_mode == "flat":
            payload: dict[str, Any] = {"deviceId": dev_id, "timestamp": timestamp, **values}
        else:
            payload = {"deviceId": dev_id, "timestamp": timestamp, "values": values}
        result = client.publish(
            text(value(protocol_config, "topic")),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            qos=int(value(protocol_config, "qos", 0)),
            retain=bool(value(protocol_config, "retain", False)),
        )
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            self._dirty.discard(dev_id)
        else:
            self._status.last_error = f"MQTT publish failed with rc={result.rc}"
            if result.rc == mqtt.MQTT_ERR_QUEUE_SIZE:
                self._status.dropped_messages += 1

    def set_device_active(self, device_id_value: str, active: bool) -> None:
        """Pause/resume one logical publisher without restarting the shared connection."""

        if active:
            self._active.add(device_id_value)
        else:
            self._active.discard(device_id_value)
            self._dirty.discard(device_id_value)

    async def stop(self) -> None:
        tasks, self._publish_tasks = self._publish_tasks, []
        self._loop = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        clients, self._clients = self._clients, {}
        if clients:
            for dev_id in list(self._dirty):
                event = self._connection_events.get(self._device_connections.get(dev_id, "default"))
                if event is not None and event.is_set():
                    protocol_config = device_protocol_config(self._device_config[dev_id])
                    await self._publish(dev_id, protocol_config)
            for client in clients.values():
                client.disconnect()
                client.loop_stop()
        self._client = None
        self._connected.clear()
        self._connection_events.clear()
        self._connection_errors.clear()
        self._connections.clear()
        self._device_connections.clear()
        self._active.clear()
        self._latest.clear()
        self._timestamps.clear()
        self._dirty.clear()
        self._transition(running=False, connected=False)


MqttAdapter = MQTTAdapter
