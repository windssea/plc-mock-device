"""Normalized SQLite configuration persistence with restricted YAML bootstrap."""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml
from pydantic import ValidationError

from .models import (
    AppConfig,
    BootstrapConfig,
    IncrementGeneratorConfig,
    ModbusDeviceProtocolConfig,
    ModbusSignalMapping,
    MqttDeviceProtocolConfig,
    MqttConnectionConfig,
    MqttSignalMapping,
    OpcUaDeviceProtocolConfig,
    OpcUaSignalMapping,
    RandomRangeGeneratorConfig,
)
from .sqlite_schema import (
    V3_REQUIRED_COLUMNS,
    V3_INDEXES,
    V3_MQTT_CONNECTIONS_TABLE,
    V3_MQTT_DEVICE_CONFIGS_TABLE,
    V2_SCHEMA_STATEMENTS,
    V2_TABLES,
    V3_TABLES,
    V3_TRIGGERS,
)

SCHEMA_VERSION = 3
CONNECT_RETRY_ATTEMPTS = 6
CONNECT_RETRY_INITIAL_SECONDS = 0.01
CONNECT_RETRY_MAX_SECONDS = 0.2


class ConfigStoreError(RuntimeError):
    pass


class ConfigLoadError(ConfigStoreError):
    pass


class ConfigConflictError(ConfigStoreError):
    pass


class ConfigSaveError(ConfigStoreError):
    pass


def load_bootstrap_config_sync(path: str | Path) -> BootstrapConfig:
    source = Path(path)
    if not source.exists():
        raise ConfigLoadError(f"bootstrap configuration does not exist: {source}")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        if raw is None:
            raise ConfigLoadError("bootstrap configuration is empty")
        if not isinstance(raw, dict):
            raise ConfigLoadError("bootstrap configuration root must be a mapping")
        restricted = sorted({"config_revision", "devices"}.intersection(raw))
        if restricted:
            fields = ", ".join(restricted)
            raise ConfigLoadError(
                f"bootstrap YAML cannot contain {fields}; add devices through the Web/API "
                "or use the explicit configuration import endpoint"
            )
        return BootstrapConfig.model_validate(raw)
    except ConfigLoadError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigLoadError(f"invalid bootstrap configuration: {exc}") from exc


def load_yaml_config_sync(path: str | Path) -> AppConfig:
    return load_bootstrap_config_sync(path).to_app_config()


def _number_token(value: int | float) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigSaveError(f"not a configuration number: {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigSaveError("configuration numbers must be finite")
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def _parse_number_token(token: str) -> int | float:
    try:
        value = json.loads(token)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ConfigLoadError(f"invalid canonical number token: {token!r}") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigLoadError(f"invalid canonical number token: {token!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigLoadError(f"non-finite canonical number token: {token!r}")
    if _number_token(value) != token:
        raise ConfigLoadError(f"non-canonical number token: {token!r}")
    return value


def _canonical_config(config: AppConfig) -> str:
    return json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


class ConfigStore:
    """Persist one complete AppConfig as a normalized relational graph."""

    def __init__(self, database_path: str | Path, bootstrap_path: str | Path | None = None) -> None:
        self.path = Path(database_path)
        self.database_path = self.path
        self.bootstrap_path = Path(bootstrap_path) if bootstrap_path is not None else None
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        delay = CONNECT_RETRY_INITIAL_SECONDS
        for attempt in range(CONNECT_RETRY_ATTEMPTS):
            try:
                return self._connect_once()
            except ConfigStoreError:
                raise
            except sqlite3.Error as exc:
                if _is_busy_error(exc) and attempt + 1 < CONNECT_RETRY_ATTEMPTS:
                    time.sleep(delay)
                    delay = min(delay * 2, CONNECT_RETRY_MAX_SECONDS)
                    continue
                raise ConfigLoadError(f"cannot open configuration database: {exc}") from exc
            except OSError as exc:
                raise ConfigLoadError(f"cannot prepare configuration database path: {exc}") from exc
        raise AssertionError("unreachable")

    def _connect_once(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise sqlite3.OperationalError("database is busy enabling WAL mode")
            self._migrate(connection)
            self._validate_schema(connection)
            return connection
        except BaseException:
            with suppress(sqlite3.Error):
                connection.close()
            raise

    @classmethod
    def _migrate(cls, connection: sqlite3.Connection) -> None:
        observed_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if observed_version == SCHEMA_VERSION:
            return
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 0:
                cls._create_v2_schema(connection)
                cls._migrate_v2_to_v3(connection)
                connection.execute("PRAGMA user_version = 3")
            elif version == 1:
                cls._migrate_v1_to_v2(connection)
                connection.execute("PRAGMA user_version = 3")
            elif version == 2:
                existing_tables = {
                    str(row[0]) for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                missing = V2_TABLES - existing_tables
                if missing:
                    raise ConfigLoadError(
                        "configuration database schema 2 is missing tables: "
                        + ", ".join(sorted(missing))
                    )
                cls._migrate_v2_to_v3(connection)
                connection.execute("PRAGMA user_version = 3")
            elif version != 3:
                raise ConfigLoadError(
                    f"configuration database schema {version} is newer than supported 3"
                    if version > 3
                    else f"unsupported configuration database schema {version}"
                )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            if _is_busy_error(exc):
                raise
            raise ConfigLoadError(f"configuration database migration failed: {exc}") from exc
        except ConfigStoreError:
            connection.rollback()
            raise
        except Exception as exc:
            connection.rollback()
            raise ConfigLoadError(f"configuration database migration failed: {exc}") from exc

    @staticmethod
    def _create_v2_schema(connection: sqlite3.Connection) -> None:
        for statement in V2_SCHEMA_STATEMENTS:
            connection.execute(statement)

    @classmethod
    def _migrate_v2_to_v3(cls, connection: sqlite3.Connection) -> None:
        """Add relational MQTT profiles and route existing devices to default."""

        connection.execute("ALTER TABLE mqtt_settings ADD COLUMN password TEXT")
        connection.execute(
            "ALTER TABLE mqtt_settings ADD COLUMN transport TEXT NOT NULL DEFAULT 'tcp' "
            "CHECK (transport = 'tcp')"
        )
        connection.execute(
            "ALTER TABLE mqtt_settings ADD COLUMN tls_enabled INTEGER NOT NULL DEFAULT 0 "
            "CHECK (tls_enabled IN (0,1))"
        )
        connection.execute(V3_MQTT_CONNECTIONS_TABLE)
        connection.execute(
            """INSERT INTO mqtt_connections (
                id,app_id,sort_order,name,enabled,host,port,protocol_version,client_id,
                keepalive,username,password,password_env,transport,tls_enabled,
                reconnect_min_seconds,reconnect_max_seconds,max_inflight_messages,
                max_queued_messages)
               SELECT 'default',app_id,0,'Default MQTT',enabled,host,port,
                protocol_version,client_id,keepalive,username,password,password_env,
                transport,tls_enabled,reconnect_min_seconds,reconnect_max_seconds,
                max_inflight_messages,max_queued_messages FROM mqtt_settings"""
        )
        connection.execute("ALTER TABLE mqtt_device_configs RENAME TO mqtt_device_configs_v2")
        connection.execute(V3_MQTT_DEVICE_CONFIGS_TABLE)
        connection.execute(
            """INSERT INTO mqtt_device_configs
                (device_id,protocol,connection_id,topic,qos,retain,publish_interval_ms,payload_mode)
               SELECT device_id,protocol,'default',topic,qos,retain,publish_interval_ms,payload_mode
               FROM mqtt_device_configs_v2"""
        )
        connection.execute("DROP TABLE mqtt_device_configs_v2")
        connection.execute(
            "CREATE INDEX idx_mqtt_connections_order ON mqtt_connections(app_id,sort_order)"
        )
        connection.execute(
            "CREATE INDEX idx_mqtt_devices_connection ON mqtt_device_configs(connection_id)"
        )

    @classmethod
    def _migrate_v1_to_v2(cls, connection: sqlite3.Connection) -> None:
        columns = cls._table_columns(connection, "config_document")
        required = {"id", "revision", "config_json", "updated_at"}
        if not required.issubset(columns):
            raise ConfigLoadError("v1 configuration database is missing config_document columns")
        rows = connection.execute(
            "SELECT id, revision, config_json, updated_at FROM config_document"
        ).fetchall()
        if len(rows) > 1 or (rows and int(rows[0]["id"]) != 1):
            raise ConfigLoadError("v1 configuration database has invalid config_document rows")
        before: AppConfig | None = None
        updated_at = _utc_now_text()
        if rows:
            row = rows[0]
            try:
                before = AppConfig.model_validate(json.loads(row["config_json"]))
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                raise ConfigLoadError(f"invalid v1 configuration document: {exc}") from exc
            if before.config_revision != int(row["revision"]):
                raise ConfigLoadError("v1 row revision does not match its configuration document")
            updated_at = str(row["updated_at"])
        cls._create_v2_schema(connection)
        cls._migrate_v2_to_v3(connection)
        if before is not None:
            cls._insert_graph(connection, before, updated_at)
            after = cls._read_graph(connection)
            if after is None or _canonical_config(before) != _canonical_config(after):
                raise ConfigLoadError("v1 to v2 migration changed configuration semantics")
        cls._validate_graph(connection)
        connection.execute("DROP TABLE config_document")

    @classmethod
    def _validate_schema(cls, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != SCHEMA_VERSION:
            raise ConfigLoadError(
                f"expected configuration database schema {SCHEMA_VERSION}, found {version}"
            )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = V3_TABLES - tables
        if missing:
            raise ConfigLoadError(f"configuration database schema 3 is missing tables: {', '.join(sorted(missing))}")
        if "config_document" in tables:
            raise ConfigLoadError("configuration database schema 3 still contains legacy config_document")
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        missing_triggers = V3_TRIGGERS - triggers
        if missing_triggers:
            raise ConfigLoadError(
                f"configuration database schema 3 is missing triggers: {', '.join(sorted(missing_triggers))}"
            )
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        missing_indexes = V3_INDEXES - indexes
        if missing_indexes:
            raise ConfigLoadError(
                f"configuration database schema 3 is missing indexes: {', '.join(sorted(missing_indexes))}"
            )
        for table, required in V3_REQUIRED_COLUMNS.items():
            columns = cls._table_columns(connection, table)
            if columns != required:
                difference = sorted(required.symmetric_difference(columns))
                raise ConfigLoadError(
                    f"configuration database schema 3 has invalid {table} columns: {', '.join(difference)}"
                )

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _metadata_revision(connection: sqlite3.Connection) -> int | None:
        row = connection.execute("SELECT revision FROM app_metadata WHERE id=1").fetchone()
        return int(row[0]) if row is not None else None

    @classmethod
    def _insert_graph(cls, connection: sqlite3.Connection, config: AppConfig, updated_at: str) -> None:
        connection.execute(
            "INSERT INTO app_metadata VALUES (1,?,?,?,?)",
            (config.version, config.config_revision, _number_token(config.random_seed), updated_at),
        )
        connection.execute("INSERT INTO web_settings VALUES (1,?,?)", (config.web.host, config.web.port))
        connection.execute(
            "INSERT INTO opcua_settings VALUES (1,?,?,?,?,?,?,?)",
            (int(config.opcua.enabled), config.opcua.host, config.opcua.port,
             config.opcua.endpoint_path, config.opcua.server_name,
             config.opcua.application_uri, config.opcua.namespace_uri),
        )
        connection.execute(
            """INSERT INTO mqtt_settings
               (app_id,enabled,host,port,protocol_version,client_id,keepalive,
                username,password,password_env,transport,tls_enabled,
                reconnect_min_seconds,reconnect_max_seconds,max_inflight_messages,
                max_queued_messages) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (int(config.mqtt.enabled), config.mqtt.host, config.mqtt.port,
             config.mqtt.protocol_version, config.mqtt.client_id, config.mqtt.keepalive,
             config.mqtt.username, config.mqtt.password, config.mqtt.password_env,
             config.mqtt.transport, int(config.mqtt.tls_enabled),
             config.mqtt.reconnect_min_seconds, config.mqtt.reconnect_max_seconds,
             config.mqtt.max_inflight_messages, config.mqtt.max_queued_messages),
        )
        for connection_order, profile in enumerate(config.mqtt_connections):
            connection.execute(
                """INSERT INTO mqtt_connections
                   (id,app_id,sort_order,name,enabled,host,port,protocol_version,
                    client_id,keepalive,username,password,password_env,transport,
                    tls_enabled,reconnect_min_seconds,reconnect_max_seconds,
                    max_inflight_messages,max_queued_messages)
                   VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (profile.id, connection_order, profile.name, int(profile.enabled),
                 profile.host, profile.port, profile.protocol_version, profile.client_id,
                 profile.keepalive, profile.username, profile.password,
                 profile.password_env, profile.transport, int(profile.tls_enabled),
                 profile.reconnect_min_seconds, profile.reconnect_max_seconds,
                 profile.max_inflight_messages, profile.max_queued_messages),
            )
        connection.execute(
            "INSERT INTO modbus_settings VALUES (1,?,?,?)",
            (int(config.modbus.enabled), config.modbus.host, config.modbus.port),
        )
        for device_order, device in enumerate(config.devices):
            connection.execute(
                "INSERT INTO devices VALUES (?,?,?,?,?,?,?,?)",
                (device.id, 1, device_order, device.name, int(device.enabled),
                 device.desired_state, device.default_interval_ms, device.protocol.value),
            )
            protocol_config = device.protocol_config
            if isinstance(protocol_config, OpcUaDeviceProtocolConfig):
                connection.execute("INSERT INTO opcua_device_configs VALUES (?, 'opcua', ?)",
                                   (device.id, protocol_config.object_name))
            elif isinstance(protocol_config, MqttDeviceProtocolConfig):
                connection.execute(
                    "INSERT INTO mqtt_device_configs VALUES (?, 'mqtt', ?,?,?,?,?,?)",
                    (device.id, protocol_config.connection_id, protocol_config.topic, protocol_config.qos,
                     int(protocol_config.retain), protocol_config.publish_interval_ms,
                     protocol_config.payload_mode),
                )
            elif isinstance(protocol_config, ModbusDeviceProtocolConfig):
                connection.execute("INSERT INTO modbus_device_configs VALUES (?, 'modbus', ?)",
                                   (device.id, protocol_config.unit_id))
            for signal_order, signal in enumerate(device.signals):
                connection.execute(
                    "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?)",
                    (device.id, signal.id, signal_order, signal.name, signal.unit,
                     signal.value_type.value, signal.interval_ms, signal.generator.type,
                     signal.binding.type),
                )
                generator = signal.generator
                if isinstance(generator, IncrementGeneratorConfig):
                    connection.execute(
                        "INSERT INTO increment_generators VALUES (?,?,'increment',?,?,?,?,?,?)",
                        (device.id, signal.id, _number_token(generator.initial_value),
                         _number_token(generator.value_min), _number_token(generator.value_max),
                         _number_token(generator.step_min), _number_token(generator.step_max),
                         generator.boundary.value),
                    )
                elif isinstance(generator, RandomRangeGeneratorConfig):
                    connection.execute(
                        "INSERT INTO random_range_generators VALUES (?,?,'random_range',?,?,?)",
                        (device.id, signal.id, _number_token(generator.min),
                         _number_token(generator.max), generator.precision),
                    )
                binding = signal.binding
                if isinstance(binding, OpcUaSignalMapping):
                    connection.execute(
                        "INSERT INTO opcua_signal_mappings VALUES (?,?,'opcua',?,?,?,?)",
                        (device.id, signal.id, binding.node_id, binding.browse_name,
                         binding.display_name, binding.data_type.value if binding.data_type else None),
                    )
                elif isinstance(binding, MqttSignalMapping):
                    connection.execute(
                        "INSERT INTO mqtt_signal_mappings VALUES (?,?,'mqtt',?)",
                        (device.id, signal.id, binding.field_name),
                    )
                elif isinstance(binding, ModbusSignalMapping):
                    connection.execute(
                        "INSERT INTO modbus_signal_mappings VALUES (?,?,'modbus',?,?,?,?,?,?,?,?,?)",
                        (device.id, signal.id, binding.table.value, binding.address,
                         binding.wire_type.value, binding.register_length,
                         None if binding.signed is None else int(binding.signed),
                         _number_token(binding.scale), _number_token(binding.offset),
                         binding.byte_order.value, binding.word_order.value),
                    )

    @classmethod
    def _read_graph(cls, connection: sqlite3.Connection) -> AppConfig | None:
        metadata = connection.execute("SELECT * FROM app_metadata WHERE id=1").fetchone()
        if metadata is None:
            return None
        web = cls._required_row(connection, "SELECT * FROM web_settings WHERE app_id=1", "web_settings")
        opcua = cls._required_row(connection, "SELECT * FROM opcua_settings WHERE app_id=1", "opcua_settings")
        mqtt = cls._required_row(connection, "SELECT * FROM mqtt_settings WHERE app_id=1", "mqtt_settings")
        modbus = cls._required_row(connection, "SELECT * FROM modbus_settings WHERE app_id=1", "modbus_settings")
        raw: dict = {
            "version": metadata["config_version"], "config_revision": metadata["revision"],
            "random_seed": _parse_number_token(metadata["random_seed_token"]),
            "web": {"host": web["host"], "port": web["port"]},
            "opcua": {key: opcua[key] for key in
                      ("enabled","host","port","endpoint_path","server_name","application_uri","namespace_uri")},
            "mqtt": {key: mqtt[key] for key in
                     ("enabled","host","port","protocol_version","client_id","keepalive","username",
                      "password","password_env","transport","tls_enabled",
                      "reconnect_min_seconds","reconnect_max_seconds",
                      "max_inflight_messages","max_queued_messages")},
            "mqtt_connections": [],
            "modbus": {"enabled": modbus["enabled"], "host": modbus["host"], "port": modbus["port"]},
            "devices": [],
        }
        for profile in connection.execute("SELECT * FROM mqtt_connections ORDER BY sort_order"):
            raw["mqtt_connections"].append({
                key: profile[key]
                for key in (
                    "id", "name", "enabled", "host", "port", "protocol_version",
                    "client_id", "keepalive", "username", "password", "password_env",
                    "transport", "tls_enabled", "reconnect_min_seconds",
                    "reconnect_max_seconds", "max_inflight_messages",
                    "max_queued_messages",
                )
            })
        for device in connection.execute("SELECT * FROM devices ORDER BY sort_order"):
            protocol = device["protocol"]
            if protocol == "opcua":
                subtype = cls._required_row(connection, "SELECT * FROM opcua_device_configs WHERE device_id=?",
                                            "opcua_device_configs", (device["id"],))
                protocol_config = {"object_name": subtype["object_name"]}
            elif protocol == "mqtt":
                subtype = cls._required_row(connection, "SELECT * FROM mqtt_device_configs WHERE device_id=?",
                                            "mqtt_device_configs", (device["id"],))
                protocol_config = {key: subtype[key] for key in
                                   ("connection_id","topic","qos","retain","publish_interval_ms","payload_mode")}
            else:
                subtype = cls._required_row(connection, "SELECT * FROM modbus_device_configs WHERE device_id=?",
                                            "modbus_device_configs", (device["id"],))
                protocol_config = {"unit_id": subtype["unit_id"]}
            device_raw = {
                "id": device["id"], "name": device["name"], "enabled": bool(device["enabled"]),
                "desired_state": device["desired_state"],
                "default_interval_ms": device["default_interval_ms"], "protocol": protocol,
                "protocol_config": protocol_config, "signals": [],
            }
            signals = connection.execute(
                "SELECT * FROM signals WHERE device_id=? ORDER BY sort_order", (device["id"],)
            ).fetchall()
            for signal in signals:
                keys = (device["id"], signal["id"])
                if signal["generator_type"] == "increment":
                    gen = cls._required_row(connection,
                        "SELECT * FROM increment_generators WHERE device_id=? AND signal_id=?",
                        "increment_generators", keys)
                    generator = {"type":"increment", "initial_value":_parse_number_token(gen["initial_value_token"]),
                                 "value_min":_parse_number_token(gen["value_min_token"]),
                                 "value_max":_parse_number_token(gen["value_max_token"]),
                                 "step_min":_parse_number_token(gen["step_min_token"]),
                                 "step_max":_parse_number_token(gen["step_max_token"]), "boundary":gen["boundary"]}
                else:
                    gen = cls._required_row(connection,
                        "SELECT * FROM random_range_generators WHERE device_id=? AND signal_id=?",
                        "random_range_generators", keys)
                    generator = {"type":"random_range", "min":_parse_number_token(gen["min_value_token"]),
                                 "max":_parse_number_token(gen["max_value_token"]), "precision":gen["precision"]}
                mapping_type = signal["mapping_type"]
                if mapping_type == "opcua":
                    bind = cls._required_row(connection,
                        "SELECT * FROM opcua_signal_mappings WHERE device_id=? AND signal_id=?",
                        "opcua_signal_mappings", keys)
                    binding = {"type":"opcua", "node_id":bind["node_id"], "browse_name":bind["browse_name"],
                               "display_name":bind["display_name"], "data_type":bind["data_type"]}
                elif mapping_type == "mqtt":
                    bind = cls._required_row(connection,
                        "SELECT * FROM mqtt_signal_mappings WHERE device_id=? AND signal_id=?",
                        "mqtt_signal_mappings", keys)
                    binding = {"type":"mqtt", "field_name":bind["field_name"]}
                else:
                    bind = cls._required_row(connection,
                        "SELECT * FROM modbus_signal_mappings WHERE device_id=? AND signal_id=?",
                        "modbus_signal_mappings", keys)
                    binding = {"type":"modbus", "table":bind["table_name"], "address":bind["address"],
                               "wire_type":bind["wire_type"], "register_length":bind["register_length"],
                               "signed":None if bind["signed"] is None else bool(bind["signed"]),
                               "scale":_parse_number_token(bind["scale_token"]),
                               "offset":_parse_number_token(bind["offset_token"]),
                               "byte_order":bind["byte_order"], "word_order":bind["word_order"]}
                device_raw["signals"].append({
                    "id":signal["id"], "name":signal["name"], "unit":signal["unit"],
                    "value_type":signal["value_type"], "interval_ms":signal["interval_ms"],
                    "generator":generator, "binding":binding,
                })
            raw["devices"].append(device_raw)
        try:
            return AppConfig.model_validate(raw)
        except ValidationError as exc:
            raise ConfigLoadError(f"invalid normalized configuration graph: {exc}") from exc

    @staticmethod
    def _required_row(connection: sqlite3.Connection, sql: str, label: str,
                      parameters: tuple = ()) -> sqlite3.Row:
        row = connection.execute(sql, parameters).fetchone()
        if row is None:
            raise ConfigLoadError(f"normalized configuration is missing {label} row")
        return row

    @classmethod
    def _validate_graph(cls, connection: sqlite3.Connection) -> None:
        foreign = connection.execute("PRAGMA foreign_key_check").fetchone()
        if foreign is not None:
            raise ConfigLoadError(f"foreign key integrity failure: {tuple(foreign)}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ConfigLoadError(f"SQLite integrity failure: {integrity[0] if integrity else 'unknown'}")
        metadata_count = connection.execute("SELECT count(*) FROM app_metadata").fetchone()[0]
        if metadata_count not in (0, 1):
            raise ConfigLoadError("normalized configuration has invalid metadata cardinality")
        if not metadata_count:
            return
        for table in ("web_settings", "opcua_settings", "mqtt_settings", "modbus_settings"):
            if connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != 1:
                raise ConfigLoadError(f"normalized configuration requires exactly one {table} row")
        if connection.execute(
            "SELECT count(*) FROM mqtt_connections WHERE id='default'"
        ).fetchone()[0] != 1:
            raise ConfigLoadError("normalized configuration requires one default MQTT connection")
        orphan_connection = connection.execute(
            """SELECT m.device_id FROM mqtt_device_configs m
               LEFT JOIN mqtt_connections c ON c.id=m.connection_id
               WHERE c.id IS NULL LIMIT 1"""
        ).fetchone()
        if orphan_connection:
            raise ConfigLoadError(
                f"MQTT device {orphan_connection[0]} references an unknown connection"
            )
        bad_device = connection.execute("""SELECT d.id FROM devices d WHERE
          (EXISTS(SELECT 1 FROM opcua_device_configs x WHERE x.device_id=d.id)) +
          (EXISTS(SELECT 1 FROM mqtt_device_configs x WHERE x.device_id=d.id)) +
          (EXISTS(SELECT 1 FROM modbus_device_configs x WHERE x.device_id=d.id)) <> 1 LIMIT 1""").fetchone()
        if bad_device:
            raise ConfigLoadError(f"device {bad_device[0]} does not have exactly one protocol config")
        bad_generator = connection.execute("""SELECT s.device_id,s.id FROM signals s WHERE
          (EXISTS(SELECT 1 FROM increment_generators x WHERE x.device_id=s.device_id AND x.signal_id=s.id)) +
          (EXISTS(SELECT 1 FROM random_range_generators x WHERE x.device_id=s.device_id AND x.signal_id=s.id)) <> 1 LIMIT 1""").fetchone()
        if bad_generator:
            raise ConfigLoadError(f"signal {bad_generator[0]}/{bad_generator[1]} has invalid generator subtype")
        bad_mapping = connection.execute("""SELECT s.device_id,s.id FROM signals s WHERE
          (EXISTS(SELECT 1 FROM opcua_signal_mappings x WHERE x.device_id=s.device_id AND x.signal_id=s.id)) +
          (EXISTS(SELECT 1 FROM mqtt_signal_mappings x WHERE x.device_id=s.device_id AND x.signal_id=s.id)) +
          (EXISTS(SELECT 1 FROM modbus_signal_mappings x WHERE x.device_id=s.device_id AND x.signal_id=s.id)) <> 1 LIMIT 1""").fetchone()
        if bad_mapping:
            raise ConfigLoadError(f"signal {bad_mapping[0]}/{bad_mapping[1]} has invalid mapping subtype")

    def _bootstrap_if_empty(self, connection: sqlite3.Connection) -> None:
        if self._metadata_revision(connection) is not None or self.bootstrap_path is None:
            return
        bootstrap = load_yaml_config_sync(self.bootstrap_path)
        delay = CONNECT_RETRY_INITIAL_SECONDS
        for attempt in range(CONNECT_RETRY_ATTEMPTS):
            try:
                connection.execute("BEGIN IMMEDIATE")
                if self._metadata_revision(connection) is None:
                    self._insert_graph(connection, bootstrap, _utc_now_text())
                    rebuilt = self._read_graph(connection)
                    if rebuilt is None or _canonical_config(bootstrap) != _canonical_config(rebuilt):
                        raise ConfigSaveError("bootstrap relational round-trip failed")
                    self._validate_graph(connection)
                connection.commit()
                return
            except sqlite3.Error as exc:
                connection.rollback()
                if _is_busy_error(exc) and attempt + 1 < CONNECT_RETRY_ATTEMPTS:
                    time.sleep(delay)
                    delay = min(delay * 2, CONNECT_RETRY_MAX_SECONDS)
                    continue
                raise ConfigSaveError(f"failed to bootstrap configuration database: {exc}") from exc
            except ConfigStoreError:
                connection.rollback()
                raise
        raise AssertionError("unreachable")

    def load_sync(self) -> AppConfig:
        connection = self._connect()
        try:
            self._bootstrap_if_empty(connection)
            config = self._read_graph(connection)
            if config is None:
                raise ConfigLoadError("database contains no configuration")
            self._validate_graph(connection)
            return config
        except ConfigStoreError:
            raise
        except sqlite3.Error as exc:
            raise ConfigLoadError(f"failed to load configuration database: {exc}") from exc
        finally:
            connection.close()

    async def load(self) -> AppConfig:
        async with self._lock:
            return await asyncio.to_thread(self.load_sync)

    def save_sync(self, config: AppConfig, *, expected_revision: int | None = None,
                  increment_revision: bool = True, allow_invalid_current: bool = False) -> AppConfig:
        del allow_invalid_current
        config = AppConfig.model_validate(config.model_dump(mode="python"))
        comparison = config.config_revision if expected_revision is None else expected_revision
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._metadata_revision(connection)
            if current is not None and comparison != current:
                raise ConfigConflictError(f"revision conflict: expected {comparison}, current {current}")
            if current is None and comparison not in (None, 0):
                raise ConfigConflictError(f"revision conflict: expected {comparison}, database has no configuration")
            revision = ((current if current is not None else 0) + 1) if increment_revision else config.config_revision
            saved = config.model_copy(update={"config_revision": revision}, deep=True)
            connection.execute("DELETE FROM app_metadata WHERE id=1")
            self._insert_graph(connection, saved, _utc_now_text())
            rebuilt = self._read_graph(connection)
            if rebuilt is None or _canonical_config(saved) != _canonical_config(rebuilt):
                raise ConfigSaveError("normalized configuration round-trip failed")
            self._validate_graph(connection)
            connection.commit()
            return saved
        except ConfigStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise ConfigSaveError(f"failed to save configuration: {exc}") from exc
        finally:
            connection.close()

    async def save(self, config: AppConfig, *, expected_revision: int | None = None,
                   increment_revision: bool = True, allow_invalid_current: bool = False) -> AppConfig:
        async with self._lock:
            return await asyncio.to_thread(self.save_sync, config,
                expected_revision=expected_revision, increment_revision=increment_revision,
                allow_invalid_current=allow_invalid_current)

    def restore_sync(self, config: AppConfig | None, *, expected_revision: int) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._metadata_revision(connection)
            if current != expected_revision:
                raise ConfigConflictError(
                    f"rollback revision conflict: expected {expected_revision}, current {current}"
                )
            connection.execute("DELETE FROM app_metadata WHERE id=1")
            if config is not None:
                restored = AppConfig.model_validate(config.model_dump(mode="python"))
                self._insert_graph(connection, restored, _utc_now_text())
                rebuilt = self._read_graph(connection)
                if rebuilt is None or _canonical_config(restored) != _canonical_config(rebuilt):
                    raise ConfigSaveError("normalized rollback round-trip failed")
            self._validate_graph(connection)
            connection.commit()
        except ConfigStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise ConfigSaveError(f"failed to restore configuration: {exc}") from exc
        finally:
            connection.close()

    async def restore(self, config: AppConfig | None, *, expected_revision: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self.restore_sync, config, expected_revision=expected_revision)

    async def update(self, mutate: Callable[[AppConfig], AppConfig]) -> AppConfig:
        async with self._lock:
            current = await asyncio.to_thread(self.load_sync)
            updated = AppConfig.model_validate(mutate(current.model_copy(deep=True)))
            return await asyncio.to_thread(self.save_sync, updated,
                                           expected_revision=current.config_revision)


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_busy_error(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message
