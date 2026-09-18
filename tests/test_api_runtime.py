from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from iot_sim.api import create_app
from iot_sim.adapters import MQTTAdapter
from iot_sim.config_store import ConfigLoadError, ConfigStore
from iot_sim.models import AppConfig, BootstrapConfig
from iot_sim.runtime import RuntimeManager
from iot_sim.state import DesiredState, Sample
from iot_sim.models import ValueType


def config_dict(*, state: str = "stopped") -> dict:
    return {
        "version": 1,
        "config_revision": 0,
        "random_seed": 1,
        "web": {"host": "127.0.0.1", "port": 8080},
        "opcua": {"enabled": False},
        "mqtt": {"enabled": True},
        "modbus": {"enabled": False},
        "devices": [
            {
                "id": "mqtt-1",
                "name": "MQTT One",
                "enabled": True,
                "desired_state": state,
                "default_interval_ms": 1000,
                "protocol": "mqtt",
                "protocol_config": {
                    "topic": "devices/mqtt-1/telemetry",
                    "qos": 0,
                    "retain": False,
                },
                "signals": [
                    {
                        "id": "count",
                        "name": "Count",
                        "value_type": "uint16",
                        "generator": {
                            "type": "increment",
                            "initial_value": 0,
                            "value_min": 0,
                            "value_max": 100,
                            "step_min": 1,
                            "step_max": 1,
                            "boundary": "wrap",
                        },
                        "binding": {"type": "mqtt", "field_name": "count"},
                    }
                ],
            }
        ],
    }


def write_config(path: Path, data: dict | None = None) -> None:
    payload = data or config_dict()
    bootstrap = {
        key: payload[key]
        for key in ("version", "random_seed", "web", "opcua", "mqtt", "modbus")
        if key in payload
    }
    path.write_text(yaml.safe_dump(bootstrap, sort_keys=False), encoding="utf-8")
    database = path.with_suffix(".db")
    if payload.get("devices") and not database.exists():
        ConfigStore(database).save_sync(
            AppConfig.model_validate(payload), expected_revision=0, increment_revision=False
        )


def test_empty_bootstrap_adds_device_to_sqlite_and_restart_keeps_it(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    write_config(path, BootstrapConfig(random_seed=77).to_app_config().model_dump(mode="json"))
    device = config_dict()["devices"][0]

    with TestClient(create_app(str(path))) as client:
        config = client.get("/api/v1/config").json()
        assert config["config_revision"] == 0
        assert config["random_seed"] == 77
        assert config["devices"] == []
        response = client.post("/api/v1/devices", json=device)
        assert response.status_code == 202
        assert response.json()["config"]["config_revision"] == 1

    with TestClient(create_app(str(path))) as client:
        persisted = client.get("/api/v1/config").json()
        assert persisted["config_revision"] == 1
        assert [item["id"] for item in persisted["devices"]] == ["mqtt-1"]


def test_api_crud_actions_and_export(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    write_config(path)
    with TestClient(create_app(str(path))) as client:
        response = client.get("/api/v1/config")
        assert response.status_code == 200
        assert response.json()["devices"][0]["id"] == "mqtt-1"

        response = client.post("/api/v1/devices/mqtt-1/actions/start")
        assert response.status_code == 200
        assert response.json()["config"]["config_revision"] == 1
        assert client.get("/api/v1/devices/mqtt-1/status").json()["actual_state"] in {
            "starting",
            "running",
        }

        copied = client.get("/api/v1/devices/mqtt-1").json()
        copied["id"] = "mqtt-2"
        copied["name"] = "MQTT Two"
        copied["protocol_config"]["topic"] = "devices/mqtt-2/telemetry"
        assert client.post("/api/v1/devices", json=copied).status_code == 202
        assert len(client.get("/api/v1/devices").json()) == 2
        assert client.delete("/api/v1/devices/mqtt-2").status_code == 202

        exported = client.get("/api/v1/config/export")
        assert exported.status_code == 200
        assert "mqtt-1" in exported.text


def test_api_mqtt_connection_crud_and_reference_guard(tmp_path: Path) -> None:
    path = tmp_path / "mqtt-connections.yaml"
    write_config(path)
    with TestClient(create_app(str(path))) as client:
        profile = {
            "id": "secondary", "name": "Secondary", "enabled": True,
            "host": "localhost", "port": 1883, "protocol_version": "3.1.1",
            "client_id": "secondary-client", "keepalive": 60,
            "username": "mqtt", "password": "db-secret", "password_env": None,
            "transport": "tcp", "tls_enabled": False,
            "reconnect_min_seconds": 1, "reconnect_max_seconds": 30,
            "max_inflight_messages": 20, "max_queued_messages": 1000,
        }
        response = client.post("/api/v1/mqtt/connections", json=profile)
        assert response.status_code == 202
        assert "db-secret" not in response.text
        assert response.json()["config"]["mqtt_connections"][1]["password"] == "********"
        connections = client.get("/api/v1/mqtt/connections").json()
        assert [item["id"] for item in connections] == ["default", "secondary"]
        assert connections[1]["password"] == "********"

        connections[1]["name"] = "Secondary Updated"
        updated = client.put(
            "/api/v1/mqtt/connections/secondary", json=connections[1]
        )
        assert updated.status_code == 202
        stored = client.app.state.manager.store.load_sync()
        assert stored.mqtt_connections[1].password == "db-secret"

        rejected = profile | {"id": "new-redacted", "password": "********"}
        assert client.post("/api/v1/mqtt/connections", json=rejected).status_code == 422

        device = client.get("/api/v1/devices/mqtt-1").json()
        device["protocol_config"]["connection_id"] = "secondary"
        assert client.put("/api/v1/devices/mqtt-1", json=device).status_code == 202
        blocked = client.delete("/api/v1/mqtt/connections/secondary")
        assert blocked.status_code == 409
        assert "mqtt-1" in blocked.json()["detail"]


def test_api_redacts_and_preserves_full_config_passwords(tmp_path: Path) -> None:
    path = tmp_path / "redaction.yaml"
    payload = config_dict()
    payload["mqtt"].update({"username": "mqtt", "password": "top-secret"})
    write_config(path, payload)
    with TestClient(create_app(str(path))) as client:
        response = client.get("/api/v1/config")
        assert "top-secret" not in response.text
        redacted = response.json()
        assert redacted["mqtt"]["password"] == "********"
        assert redacted["mqtt_connections"][0]["password"] == "********"

        saved = client.put("/api/v1/config", json=redacted)
        assert saved.status_code in {200, 202}
        assert "top-secret" not in saved.text
        assert client.app.state.manager.store.load_sync().mqtt.password == "top-secret"

        validated = client.post("/api/v1/config/validate", json={
            **redacted,
            "mqtt": {**redacted["mqtt"], "password": "validate-secret"},
            "mqtt_connections": [
                {**redacted["mqtt_connections"][0], "password": "validate-secret"}
            ],
        })
        assert validated.status_code == 200
        assert "validate-secret" not in validated.text
        assert validated.json()["config"]["mqtt"]["password"] == "********"

        invalid = client.post("/api/v1/config/validate", json={
            **redacted,
            "mqtt": {
                **redacted["mqtt"], "password": "invalid-secret",
                "password_env": "MQTT_PASSWORD",
            },
        })
        assert invalid.status_code == 422
        assert "invalid-secret" not in invalid.text

        invalid_import_data = {
            **redacted,
            "mqtt": {
                **redacted["mqtt"], "password": "import-secret",
                "password_env": "MQTT_PASSWORD",
            },
        }
        invalid_import = client.post(
            "/api/v1/config/import",
            content=yaml.safe_dump(invalid_import_data, sort_keys=False),
            headers={"Content-Type": "application/yaml"},
        )
        assert invalid_import.status_code == 422
        assert "import-secret" not in invalid_import.text

        malformed_import = client.post(
            "/api/v1/config/import",
            content="mqtt: [malformed-secret",
            headers={"Content-Type": "application/yaml"},
        )
        assert malformed_import.status_code == 422
        assert "malformed-secret" not in malformed_import.text

        malicious_key_import = client.post(
            "/api/v1/config/import",
            content="mqtt: {password:LEAKME: x}",
            headers={"Content-Type": "application/yaml"},
        )
        assert malicious_key_import.status_code == 422
        assert "LEAKME" not in malicious_key_import.text

        exported = client.get("/api/v1/config/export")
        assert "top-secret" not in exported.text
        assert "********" in exported.text
        imported = client.post(
            "/api/v1/config/import",
            content=exported.content,
            headers={"Content-Type": "application/yaml"},
        )
        assert imported.status_code in {200, 202}
        assert client.app.state.manager.store.load_sync().mqtt.password == "top-secret"

        latest = client.get("/api/v1/config").json()
        latest["mqtt"]["password"] = None
        latest["mqtt_connections"][0]["password"] = None
        cleared = client.put("/api/v1/config", json=latest)
        assert cleared.status_code in {200, 202}
        assert client.app.state.manager.store.load_sync().mqtt.password is None
        assert client.get("/").status_code == 200


def test_console_and_static_assets_disable_browser_cache(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    write_config(path)
    expected = "no-store, no-cache, must-revalidate, max-age=0"

    with TestClient(create_app(str(path))) as client:
        for url in (
            "/",
            "/static/styles.css",
            "/static/signal-form.js",
            "/static/protocol-form.js",
            "/static/app.js",
        ):
            response = client.get(url)
            assert response.status_code == 200
            assert response.headers["cache-control"] == expected
            assert response.headers["pragma"] == "no-cache"
            assert response.headers["expires"] == "0"

        app_js = client.get("/static/app.js")
        conditional = client.get(
            "/static/app.js",
            headers={"If-None-Match": app_js.headers["etag"]},
        )
        assert conditional.status_code == 304
        assert conditional.headers["cache-control"] == expected


def test_api_recovers_from_invalid_yaml_via_import(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("devices: [", encoding="utf-8")
    with TestClient(create_app(str(path))) as client:
        assert client.get("/api/v1/config").status_code == 503
        payload = yaml.safe_dump(config_dict(), sort_keys=False)
        response = client.post(
            "/api/v1/config/import",
            content=payload,
            headers={"Content-Type": "application/yaml"},
        )
        assert response.status_code == 202
        assert client.get("/api/v1/config").status_code == 200
        assert ConfigStore(path.with_suffix(".db")).load_sync().config_revision == 1


def test_management_api_starts_in_recovery_for_invalid_database_schema(tmp_path: Path) -> None:
    yaml_path = tmp_path / "devices.yaml"
    db_path = tmp_path / "config.db"
    write_config(yaml_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA user_version = 1")
    with TestClient(create_app(str(yaml_path), str(db_path))) as client:
        response = client.get("/api/v1/config")
        assert response.status_code == 503
        status_payload = client.get("/api/v1/status").json()
        assert "missing config_document" in status_payload["config_error"]


def test_api_rejects_stale_config_revision(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    write_config(path)
    with TestClient(create_app(str(path))) as client:
        stale = client.get("/api/v1/config").json()
        assert client.post("/api/v1/devices/mqtt-1/actions/start").status_code == 200
        response = client.put("/api/v1/config", json=stale)
        assert response.status_code == 409
        assert "revision conflict" in response.json()["detail"]

        imported = yaml.safe_dump(config_dict(state="stopped"), sort_keys=False)
        response = client.post(
            "/api/v1/config/import",
            content=imported,
            headers={"Content-Type": "application/yaml"},
        )
        assert response.status_code == 409
        assert "revision conflict" in response.json()["detail"]

        current_import = config_dict(state="stopped")
        current_import["config_revision"] = 1
        response = client.post(
            "/api/v1/config/import",
            content=yaml.safe_dump(current_import, sort_keys=False),
            headers={"Content-Type": "application/yaml"},
        )
        assert response.status_code == 200
        assert response.json()["config"]["config_revision"] == 2


class FailingAdapter:
    async def reconfigure(self, *_args, **_kwargs):
        raise OSError("port already in use")

    async def start(self, *_args, **_kwargs):
        return None

    async def stop(self):
        return None

    async def update(self, _sample):
        return None


@pytest.mark.asyncio
async def test_runtime_rolls_back_database_when_adapter_apply_fails(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    write_config(path)
    manager = RuntimeManager(str(path))
    await manager.start()
    old = manager.config
    assert old is not None
    manager.adapters[old.devices[0].protocol] = FailingAdapter()  # type: ignore[assignment]
    candidate = old.model_copy(
        update={
            "mqtt": old.mqtt.model_copy(update={"client_id": "changed"}),
        },
        deep=True,
    )
    with pytest.raises(RuntimeError, match="rolled back"):
        await manager.apply_config(candidate)
    restored = ConfigStore(path.with_suffix(".db")).load_sync()
    assert restored.config_revision == old.config_revision
    assert restored.mqtt.client_id == old.mqtt.client_id
    await manager.stop()


@pytest.mark.asyncio
async def test_recovery_apply_failure_restores_original_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    original = b"devices: [\n"
    path.write_bytes(original)
    manager = RuntimeManager(str(path))
    await manager.start()
    assert manager.config is None
    manager.adapters[next(iter(manager.adapters))] = FailingAdapter()  # type: ignore[assignment]
    candidate = AppConfig.model_validate(config_dict())
    with pytest.raises(RuntimeError, match="rolled back"):
        await manager.apply_config(candidate)
    assert path.read_bytes() == original
    with pytest.raises(ConfigLoadError, match="no configuration"):
        ConfigStore(path.with_suffix(".db")).load_sync()
    await manager.stop()


def test_runtime_restart_ignores_changed_bootstrap_yaml(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    write_config(path)
    with TestClient(create_app(str(path))) as client:
        assert client.post("/api/v1/devices/mqtt-1/actions/start").status_code == 200
        assert client.get("/api/v1/config").json()["config_revision"] == 1

    changed = config_dict(state="stopped")
    changed["random_seed"] = 999
    write_config(path, changed)
    with TestClient(create_app(str(path))) as client:
        persisted = client.get("/api/v1/config").json()
        assert persisted["config_revision"] == 1
        assert persisted["random_seed"] == 1
        assert persisted["devices"][0]["desired_state"] == "running"


@pytest.mark.asyncio
async def test_increment_resumes_from_last_value_after_pause(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    write_config(path, config_dict(state="running"))
    manager = RuntimeManager(str(path))
    await manager.start()
    await asyncio.sleep(0.05)
    before = manager.device_statuses["mqtt-1"].latest_values["count"].value
    await manager.set_desired_state("mqtt-1", DesiredState.STOPPED)
    await manager.set_desired_state("mqtt-1", DesiredState.RUNNING)
    await asyncio.sleep(0.05)
    after = manager.device_statuses["mqtt-1"].latest_values["count"].value
    assert after == before + 1
    await manager.stop()


@pytest.mark.asyncio
async def test_mqtt_pause_discards_pending_snapshot() -> None:
    adapter = MQTTAdapter()
    adapter._fields[("d1", "s1")] = "value"  # noqa: SLF001 - focused adapter state test
    adapter.set_device_active("d1", True)
    await adapter.update(
        Sample(device_id="d1", signal_id="s1", value=1, value_type=ValueType.UINT16)
    )
    assert "d1" in adapter._dirty  # noqa: SLF001
    adapter.set_device_active("d1", False)
    assert "d1" not in adapter._dirty  # noqa: SLF001
    await adapter.update(
        Sample(device_id="d1", signal_id="s1", value=2, value_type=ValueType.UINT16)
    )
    assert adapter._latest["d1"]["value"] == 1  # noqa: SLF001


@pytest.mark.asyncio
async def test_mqtt_reconfigure_clears_removed_payload_fields() -> None:
    adapter = MQTTAdapter()
    adapter._latest["d1"]["removed"] = 7  # noqa: SLF001
    adapter._timestamps["d1"] = "old"  # noqa: SLF001
    adapter._dirty.add("d1")  # noqa: SLF001
    await adapter.start({"mqtt": {"enabled": False}}, [])
    assert not adapter._latest  # noqa: SLF001
    assert not adapter._timestamps  # noqa: SLF001
    assert not adapter._dirty  # noqa: SLF001


@pytest.mark.asyncio
async def test_mqtt_client_pool_routes_devices_and_uses_sqlite_password(monkeypatch) -> None:
    created = {}

    class FakeClient:
        def __init__(self, **kwargs):
            self.client_id = kwargs["client_id"]
            self.auth = None
            self.tls = False
            self.published = []
            created[self.client_id] = self

        def username_pw_set(self, username, password):
            self.auth = (username, password)

        def tls_set(self):
            self.tls = True

        def max_inflight_messages_set(self, _value): pass
        def max_queued_messages_set(self, _value): pass
        def reconnect_delay_set(self, **_kwargs): pass
        def connect_async(self, *_args): pass
        def loop_start(self): pass
        def loop_stop(self): pass
        def disconnect(self): pass

        def publish(self, topic, payload, **kwargs):
            self.published.append((topic, payload, kwargs))
            return type("PublishResult", (), {"rc": 0})()

    monkeypatch.setattr("iot_sim.adapters.mqtt.mqtt.Client", FakeClient)
    config = AppConfig.model_validate({
        "mqtt": {"client_id": "default-client", "username": "mqtt", "password": "db-secret"},
        "mqtt_connections": [{
            "id": "line-b", "name": "Line B", "host": "broker-b",
            "client_id": "line-b-client", "username": "line", "password": "line-secret",
            "tls_enabled": True,
        }],
        "devices": [
            {
                "id": "d-default", "name": "Default", "protocol": "mqtt",
                "protocol_config": {"topic": "default/data"}, "signals": [],
            },
            {
                "id": "d-line-b", "name": "Line B", "protocol": "mqtt",
                "protocol_config": {"connection_id": "line-b", "topic": "line-b/data"},
                "signals": [],
            },
        ],
    })
    adapter = MQTTAdapter()
    await adapter.start(config, config.devices)
    assert created["default-client"].auth == ("mqtt", "db-secret")
    assert created["line-b-client"].auth == ("line", "line-secret")
    assert created["line-b-client"].tls is True
    adapter._latest["d-default"] = {"value": 1}  # noqa: SLF001
    adapter._latest["d-line-b"] = {"value": 2}  # noqa: SLF001
    await adapter._publish("d-default", config.devices[0].protocol_config)  # noqa: SLF001
    await adapter._publish("d-line-b", config.devices[1].protocol_config)  # noqa: SLF001
    assert created["default-client"].published[0][0] == "default/data"
    assert created["line-b-client"].published[0][0] == "line-b/data"
    await adapter.stop()


def test_mqtt_callback_ignores_event_loop_close_race() -> None:
    class ClosingLoop:
        def is_closed(self):
            return False

        def call_soon_threadsafe(self, *_args):
            raise RuntimeError("Event loop is closed")

    adapter = MQTTAdapter()
    adapter._loop = ClosingLoop()  # type: ignore[assignment]  # noqa: SLF001
    adapter._dispatch_connection_state("default", True, None)  # noqa: SLF001
