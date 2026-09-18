from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from iot_sim.config_store import ConfigConflictError, ConfigLoadError, ConfigStore
from iot_sim.models import AppConfig, BootstrapConfig


def full_variant_config(*, revision: int = 4) -> AppConfig:
    huge = 10**100
    return AppConfig.model_validate({
        "version": 1,
        "config_revision": revision,
        "random_seed": huge,
        "web": {"host": "localhost", "port": 8081},
        "opcua": {"enabled": False, "port": 14840},
        "mqtt": {"enabled": True, "username": "", "password_env": None},
        "modbus": {"enabled": False, "port": 11502},
        "devices": [
            {
                "id": "z-opc", "name": "Z OPC", "enabled": False,
                "desired_state": "stopped", "protocol": "opcua",
                "protocol_config": {"object_name": None},
                "signals": [{
                    "id": "z-huge", "name": "Huge", "unit": "", "value_type": "float64",
                    "generator": {"type": "increment", "initial_value": huge,
                                  "value_min": 0, "value_max": 10**101,
                                  "step_min": 1, "step_max": 2, "boundary": "clamp"},
                    "binding": {"type": "opcua", "node_id": "z.huge",
                                "browse_name": "", "display_name": None, "data_type": "float64"},
                }],
            },
            {
                "id": "a-mqtt", "name": "A MQTT", "protocol": "mqtt",
                "protocol_config": {"topic": "devices/a", "qos": 1, "retain": False,
                                    "publish_interval_ms": None, "payload_mode": "flat"},
                "signals": [{
                    "id": "b-random", "name": "Random", "value_type": "uint16",
                    "generator": {"type": "random_range", "min": 1, "max": 9, "precision": 0},
                    "binding": {"type": "mqtt", "field_name": "same-field"},
                }],
            },
            {
                "id": "m-modbus", "name": "M Modbus", "protocol": "modbus",
                "protocol_config": {"unit_id": 9},
                "signals": [
                    {
                        "id": "z-register", "name": "Register", "value_type": "float64",
                        "generator": {"type": "random_range", "min": -0.0, "max": 1.0, "precision": 15},
                        "binding": {"type": "modbus", "table": "holding_register", "address": 10,
                                    "wire_type": "float64", "signed": False, "scale": 10**100,
                                    "offset": -0.0, "byte_order": "little", "word_order": "little"},
                    },
                    {
                        "id": "a-coil", "name": "Coil", "unit": None, "value_type": "bool",
                        "generator": {"type": "random_range", "min": 0, "max": 1, "precision": 0},
                        "binding": {"type": "modbus", "table": "coil", "address": 0,
                                    "wire_type": "bool", "signed": None},
                    },
                ],
            },
        ],
    })


def create_v1_database(path, config: AppConfig, *, payload: str | None = None) -> str:
    document = payload if payload is not None else json.dumps(
        config.model_dump(mode="json"), ensure_ascii=False, allow_nan=False
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE config_document (id INTEGER PRIMARY KEY, revision INTEGER, config_json TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO config_document VALUES (1,?,?,?)",
            (config.config_revision, document, "2026-01-01T00:00:00+00:00"),
        )
        connection.execute("PRAGMA user_version=1")
    return document


@pytest.mark.asyncio
async def test_transactional_save_load_and_revision(tmp_path) -> None:
    path = tmp_path / "config.db"
    store = ConfigStore(path)
    saved = await store.save(AppConfig())
    assert saved.config_revision == 1
    assert path.exists()
    assert (await store.load()) == saved

    saved.web.port = 9090
    second = await store.save(saved)
    assert second.config_revision == 2
    assert (await store.load()).web.port == 9090


@pytest.mark.asyncio
async def test_stale_revision_is_rejected(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.db")
    first = await store.save(AppConfig())
    stale = first.model_copy(deep=True)
    current = await store.save(first)
    assert current.config_revision == 2
    with pytest.raises(ConfigConflictError):
        await store.save(stale)


@pytest.mark.asyncio
async def test_update_serializes_concurrent_mutations(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.db")
    await store.save(AppConfig())

    def increment_seed(config: AppConfig) -> AppConfig:
        config.random_seed += 1
        return config

    results = await asyncio.gather(*(store.update(increment_seed) for _ in range(5)))
    assert [result.config_revision for result in results] == [2, 3, 4, 5, 6]
    assert (await store.load()).random_seed == 5


def test_yaml_bootstrap_creates_revision_zero_without_devices_and_is_used_once(tmp_path) -> None:
    yaml_path = tmp_path / "devices.yaml"
    db_path = tmp_path / "config.db"
    bootstrap = BootstrapConfig(
        random_seed=10,
        opcua={"port": 14840},
        mqtt={"host": "bootstrap-broker"},
        modbus={"port": 11502},
    )
    initial = bootstrap.to_app_config()
    yaml_path.write_text(
        yaml.safe_dump(bootstrap.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )

    assert ConfigStore(db_path, yaml_path).load_sync() == initial
    assert initial.config_revision == 0
    assert initial.devices == []
    assert initial.opcua.port == 14840
    assert initial.mqtt.host == "bootstrap-broker"
    assert initial.modbus.port == 11502
    # A legacy/full YAML becomes invalid under the new boundary, but an existing
    # DB row must still bypass it entirely during upgrades.
    changed = initial.model_copy(update={"config_revision": 99, "random_seed": 999})
    yaml_path.write_text(
        yaml.safe_dump(changed.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )

    loaded = ConfigStore(db_path, yaml_path).load_sync()
    assert loaded.config_revision == 0
    assert loaded.random_seed == 10


def test_concurrent_independent_stores_bootstrap_one_database(tmp_path) -> None:
    bootstrap = BootstrapConfig(random_seed=123)
    initial = bootstrap.to_app_config()
    yaml_path = tmp_path / "devices.yaml"
    yaml_path.write_text(
        yaml.safe_dump(bootstrap.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )

    for round_number in range(10):
        db_path = tmp_path / f"concurrent-{round_number}.db"
        workers = 8
        barrier = threading.Barrier(workers)

        def load_from_independent_store(_index: int) -> AppConfig:
            store = ConfigStore(db_path, yaml_path)
            barrier.wait(timeout=10)
            return store.load_sync()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(load_from_independent_store, range(workers)))
        assert results == [initial] * workers
        assert ConfigStore(db_path, yaml_path).load_sync() == initial


def test_invalid_initial_yaml_leaves_database_empty_and_file_unchanged(tmp_path) -> None:
    yaml_path = tmp_path / "broken.yaml"
    yaml_path.write_text("devices: [", encoding="utf-8")
    db_path = tmp_path / "config.db"
    store = ConfigStore(db_path, yaml_path)
    with pytest.raises(ConfigLoadError, match="invalid bootstrap"):
        store.load_sync()
    assert yaml_path.read_text(encoding="utf-8") == "devices: ["
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM app_metadata").fetchone()[0] == 0


def test_corrupt_database_never_falls_back_to_valid_yaml(tmp_path) -> None:
    yaml_path = tmp_path / "devices.yaml"
    yaml_path.write_text(
        yaml.safe_dump(BootstrapConfig().model_dump(mode="json")), encoding="utf-8"
    )
    db_path = tmp_path / "config.db"
    db_path.write_bytes(b"this is not sqlite")
    with pytest.raises(ConfigLoadError, match="database"):
        ConfigStore(db_path, yaml_path).load_sync()
    assert db_path.read_bytes() == b"this is not sqlite"


@pytest.mark.parametrize("forbidden", ["devices", "config_revision"])
def test_bootstrap_rejects_runtime_fields_with_recovery_hint(tmp_path, forbidden) -> None:
    yaml_path = tmp_path / f"forbidden-{forbidden}.yaml"
    raw = BootstrapConfig().model_dump(mode="json")
    raw[forbidden] = [] if forbidden == "devices" else 0
    yaml_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigLoadError, match="Web/API.*explicit configuration import"):
        ConfigStore(tmp_path / f"{forbidden}.db", yaml_path).load_sync()


def test_declared_schema_without_config_table_is_load_error(tmp_path) -> None:
    db_path = tmp_path / "missing-table.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA user_version = 1")
    with pytest.raises(ConfigLoadError, match="missing config_document"):
        ConfigStore(db_path).load_sync()


def test_declared_v2_schema_with_missing_tables_is_load_error(tmp_path) -> None:
    db_path = tmp_path / "missing-v2-tables.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(ConfigLoadError, match="schema 2 is missing tables"):
        ConfigStore(db_path).load_sync()


def test_database_directory_error_is_wrapped(tmp_path, monkeypatch) -> None:
    store = ConfigStore(tmp_path / "cannot-create" / "config.db")

    def deny_mkdir(*_args, **_kwargs):
        raise PermissionError("read-only directory")

    monkeypatch.setattr(type(store.path.parent), "mkdir", deny_mkdir)
    with pytest.raises(ConfigLoadError, match="cannot prepare configuration database path"):
        store.load_sync()


def test_restore_uses_expected_revision_and_can_restore_no_config(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.db")
    old = store.save_sync(AppConfig())
    current = store.save_sync(old.model_copy(update={"random_seed": 2}))
    with pytest.raises(ConfigConflictError, match="rollback revision conflict"):
        store.restore_sync(old, expected_revision=99)
    store.restore_sync(old, expected_revision=current.config_revision)
    assert store.load_sync() == old

    first_store = ConfigStore(tmp_path / "empty-after-rollback.db")
    first = first_store.save_sync(AppConfig())
    first_store.restore_sync(None, expected_revision=first.config_revision)
    with pytest.raises(ConfigLoadError, match="no configuration"):
        first_store.load_sync()


def test_sqlite_durability_pragmas_and_schema_version(tmp_path) -> None:
    path = tmp_path / "config.db"
    ConfigStore(path).save_sync(AppConfig())
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_mqtt_profiles_are_normalized_and_password_is_persisted(tmp_path) -> None:
    path = tmp_path / "mqtt-profiles.db"
    device = {
        "id": "line-b", "name": "Line B", "protocol": "mqtt",
        "protocol_config": {"connection_id": "backup", "topic": "line-b/data"},
        "signals": [],
    }
    expected = AppConfig.model_validate({
        "mqtt": {
            "host": "localhost", "username": "mqtt", "password": "direct-secret",
            "transport": "tcp", "tls_enabled": False,
        },
        "mqtt_connections": [{
            "id": "backup", "name": "Backup", "host": "broker-b", "port": 1884,
            "client_id": "line-b-client", "username": "other", "password": "backup-secret",
            "transport": "tcp", "tls_enabled": True,
        }],
        "devices": [device],
    })
    ConfigStore(path).save_sync(expected, expected_revision=0, increment_revision=False)
    loaded = ConfigStore(path).load_sync()
    assert loaded == expected
    assert loaded.devices[0].protocol_config.connection_id == "backup"
    with sqlite3.connect(path) as connection:
        profiles = connection.execute(
            "SELECT id,password,tls_enabled FROM mqtt_connections ORDER BY sort_order"
        ).fetchall()
        assert profiles == [("default", "direct-secret", 0), ("backup", "backup-secret", 1)]
        assert connection.execute(
            "SELECT connection_id FROM mqtt_device_configs WHERE device_id='line-b'"
        ).fetchone()[0] == "backup"


def test_v2_database_migrates_default_mqtt_connection(tmp_path) -> None:
    path = tmp_path / "legacy-v2.db"
    with sqlite3.connect(path) as connection:
        ConfigStore._create_v2_schema(connection)  # noqa: SLF001 - migration fixture
        connection.execute(
            "INSERT INTO app_metadata VALUES (1,1,7,'42','2026-01-01T00:00:00+00:00')"
        )
        connection.execute("INSERT INTO web_settings VALUES (1,'127.0.0.1',8080)")
        connection.execute(
            "INSERT INTO opcua_settings VALUES (1,0,'0.0.0.0',4840,'/iot/','IoT','urn:a','urn:n')"
        )
        connection.execute(
            """INSERT INTO mqtt_settings VALUES
               (1,1,'legacy-broker',1883,'3.1.1','legacy-client',60,'legacy-user',NULL,1,30,20,1000)"""
        )
        connection.execute("INSERT INTO modbus_settings VALUES (1,0,'0.0.0.0',1502)")
        connection.execute("PRAGMA user_version=2")
    loaded = ConfigStore(path).load_sync()
    assert loaded.config_revision == 7
    assert loaded.mqtt_connections[0].id == "default"
    assert loaded.mqtt_connections[0].host == "legacy-broker"
    assert loaded.mqtt_connections[0].password is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_v2_full_relational_round_trip_preserves_variants_order_and_number_tokens(tmp_path) -> None:
    path = tmp_path / "variants.db"
    expected = full_variant_config()
    ConfigStore(path).save_sync(expected, expected_revision=0, increment_revision=False)
    loaded = ConfigStore(path).load_sync()
    assert json.dumps(loaded.model_dump(mode="json"), allow_nan=False) == json.dumps(
        expected.model_dump(mode="json"), allow_nan=False
    )
    assert [device.id for device in loaded.devices] == ["z-opc", "a-mqtt", "m-modbus"]
    assert [signal.id for signal in loaded.devices[2].signals] == ["z-register", "a-coil"]
    assert math.copysign(1, loaded.devices[2].signals[0].generator.min) == -1
    assert math.copysign(1, loaded.devices[2].signals[0].binding.offset) == -1

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "config_document" not in tables
        assert connection.execute("SELECT random_seed_token FROM app_metadata").fetchone()[0] == str(10**100)
        assert connection.execute(
            "SELECT initial_value_token FROM increment_generators").fetchone()[0] == str(10**100)
        tokens = connection.execute(
            "SELECT min_value_token FROM random_range_generators WHERE device_id='m-modbus' AND signal_id='z-register'"
        ).fetchone()
        assert tokens[0] == "-0.0"
        assert connection.execute(
            "SELECT offset_token FROM modbus_signal_mappings WHERE signal_id='z-register'"
        ).fetchone()[0] == "-0.0"
        assert connection.execute(
            "SELECT scale_token FROM modbus_signal_mappings WHERE signal_id='z-register'"
        ).fetchone()[0] == "1e+100"
        assert connection.execute("SELECT count(*) FROM devices").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM signals").fetchone()[0] == 4
        assert sum(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                   for table in ("increment_generators", "random_range_generators")) == 4
        assert sum(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                   for table in ("opcua_signal_mappings", "mqtt_signal_mappings", "modbus_signal_mappings")) == 4
        for table in tables:
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            assert "config_json" not in columns


def test_v1_database_migrates_atomically_to_v2(tmp_path) -> None:
    path = tmp_path / "v1.db"
    expected = full_variant_config()
    create_v1_database(path, expected)
    assert ConfigStore(path).load_sync().model_dump(mode="json") == expected.model_dump(mode="json")
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='config_document'"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT updated_at FROM app_metadata").fetchone()[0] == \
            "2026-01-01T00:00:00+00:00"


def test_failed_v1_migration_rolls_back_the_complete_v1_database(tmp_path, monkeypatch) -> None:
    path = tmp_path / "failed-v1.db"
    expected = full_variant_config()
    original = create_v1_database(path, expected)

    def fail_insert(_cls, _connection, _config, _updated_at):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(ConfigStore, "_insert_graph", classmethod(fail_insert))
    with pytest.raises(ConfigLoadError, match="injected migration failure"):
        ConfigStore(path).load_sync()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        row = connection.execute("SELECT revision,config_json FROM config_document WHERE id=1").fetchone()
        assert row == (expected.config_revision, original)
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='app_metadata'"
        ).fetchone()[0] == 0


def test_corrupt_v1_document_is_not_replaced_by_bootstrap(tmp_path) -> None:
    path = tmp_path / "corrupt-v1.db"
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(BootstrapConfig().model_dump(mode="json")), encoding="utf-8")
    create_v1_database(path, AppConfig(), payload="{broken")
    with pytest.raises(ConfigLoadError, match="invalid v1 configuration"):
        ConfigStore(path, bootstrap_path).load_sync()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT config_json FROM config_document").fetchone()[0] == "{broken"


def test_concurrent_independent_stores_migrate_v1_once(tmp_path) -> None:
    path = tmp_path / "concurrent-v1.db"
    expected = full_variant_config()
    create_v1_database(path, expected)
    workers = 6
    barrier = threading.Barrier(workers)

    def migrate(_index):
        barrier.wait(timeout=10)
        return ConfigStore(path).load_sync()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(migrate, range(workers)))
    assert [item.model_dump(mode="json") for item in results] == [
        expected.model_dump(mode="json")
    ] * workers
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_modbus_overlap_trigger_rejects_direct_relational_corruption(tmp_path) -> None:
    path = tmp_path / "overlap.db"
    ConfigStore(path).save_sync(
        full_variant_config(), expected_revision=0, increment_revision=False
    )
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?)",
            ("m-modbus", "overlap", 2, "Overlap", None, "float32", None,
             "random_range", "modbus"),
        )
        connection.execute(
            "INSERT INTO random_range_generators VALUES (?,?,'random_range',?,?,?)",
            ("m-modbus", "overlap", "0.0", "1.0", 2),
        )
        with pytest.raises(sqlite3.IntegrityError, match="overlapping Modbus mappings"):
            connection.execute(
                "INSERT INTO modbus_signal_mappings VALUES (?,?,'modbus',?,?,?,?,?,?,?,?,?)",
                ("m-modbus", "overlap", "holding_register", 12, "float32", 2,
                 None, "1.0", "0.0", "big", "big"),
            )
