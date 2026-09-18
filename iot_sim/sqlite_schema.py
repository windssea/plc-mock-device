"""Immutable SQLite schema v2 DDL for normalized simulator configuration."""

V2_SCHEMA_STATEMENTS = (
    """CREATE TABLE app_metadata (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        config_version INTEGER NOT NULL CHECK (config_version = 1),
        revision INTEGER NOT NULL CHECK (revision >= 0),
        random_seed_token TEXT NOT NULL CHECK (length(random_seed_token) > 0),
        updated_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE web_settings (
        app_id INTEGER PRIMARY KEY CHECK (app_id = 1), host TEXT NOT NULL,
        port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
        FOREIGN KEY (app_id) REFERENCES app_metadata(id) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE opcua_settings (
        app_id INTEGER PRIMARY KEY CHECK (app_id = 1),
        enabled INTEGER NOT NULL CHECK (enabled IN (0,1)), host TEXT NOT NULL,
        port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
        endpoint_path TEXT NOT NULL, server_name TEXT NOT NULL,
        application_uri TEXT NOT NULL, namespace_uri TEXT NOT NULL,
        FOREIGN KEY (app_id) REFERENCES app_metadata(id) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE mqtt_settings (
        app_id INTEGER PRIMARY KEY CHECK (app_id = 1),
        enabled INTEGER NOT NULL CHECK (enabled IN (0,1)), host TEXT NOT NULL,
        port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
        protocol_version TEXT NOT NULL CHECK (protocol_version IN ('3.1.1','5.0')),
        client_id TEXT NOT NULL, keepalive INTEGER NOT NULL CHECK (keepalive BETWEEN 1 AND 65535),
        username TEXT, password_env TEXT,
        reconnect_min_seconds INTEGER NOT NULL CHECK (reconnect_min_seconds >= 1),
        reconnect_max_seconds INTEGER NOT NULL CHECK (reconnect_max_seconds >= reconnect_min_seconds),
        max_inflight_messages INTEGER NOT NULL CHECK (max_inflight_messages >= 1),
        max_queued_messages INTEGER NOT NULL CHECK (max_queued_messages >= 1),
        FOREIGN KEY (app_id) REFERENCES app_metadata(id) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE modbus_settings (
        app_id INTEGER PRIMARY KEY CHECK (app_id = 1),
        enabled INTEGER NOT NULL CHECK (enabled IN (0,1)), host TEXT NOT NULL,
        port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
        FOREIGN KEY (app_id) REFERENCES app_metadata(id) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE devices (
        id TEXT PRIMARY KEY, app_id INTEGER NOT NULL DEFAULT 1 CHECK (app_id = 1),
        sort_order INTEGER NOT NULL CHECK (sort_order >= 0), name TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
        desired_state TEXT NOT NULL CHECK (desired_state IN ('running','stopped')),
        default_interval_ms INTEGER NOT NULL CHECK (default_interval_ms >= 1000),
        protocol TEXT NOT NULL CHECK (protocol IN ('opcua','mqtt','modbus')),
        UNIQUE (app_id, sort_order), UNIQUE (id, protocol),
        FOREIGN KEY (app_id) REFERENCES app_metadata(id) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE opcua_device_configs (
        device_id TEXT PRIMARY KEY, protocol TEXT NOT NULL DEFAULT 'opcua' CHECK (protocol='opcua'),
        object_name TEXT,
        FOREIGN KEY (device_id, protocol) REFERENCES devices(id, protocol) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE mqtt_device_configs (
        device_id TEXT PRIMARY KEY, protocol TEXT NOT NULL DEFAULT 'mqtt' CHECK (protocol='mqtt'),
        topic TEXT NOT NULL, qos INTEGER NOT NULL CHECK (qos IN (0,1,2)),
        retain INTEGER NOT NULL CHECK (retain IN (0,1)),
        publish_interval_ms INTEGER CHECK (publish_interval_ms IS NULL OR publish_interval_ms >= 1000),
        payload_mode TEXT NOT NULL CHECK (payload_mode IN ('nested','flat','values')),
        FOREIGN KEY (device_id, protocol) REFERENCES devices(id, protocol) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE modbus_device_configs (
        device_id TEXT PRIMARY KEY, protocol TEXT NOT NULL DEFAULT 'modbus' CHECK (protocol='modbus'),
        unit_id INTEGER NOT NULL UNIQUE CHECK (unit_id BETWEEN 1 AND 247),
        FOREIGN KEY (device_id, protocol) REFERENCES devices(id, protocol) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE signals (
        device_id TEXT NOT NULL, id TEXT NOT NULL, sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
        name TEXT NOT NULL, unit TEXT, value_type TEXT NOT NULL CHECK (
          value_type IN ('bool','int16','uint16','int32','uint32','float32','float64')),
        interval_ms INTEGER CHECK (interval_ms IS NULL OR interval_ms >= 1000),
        generator_type TEXT NOT NULL CHECK (generator_type IN ('increment','random_range')),
        mapping_type TEXT NOT NULL CHECK (mapping_type IN ('opcua','mqtt','modbus')),
        PRIMARY KEY (device_id,id), UNIQUE (device_id,sort_order),
        UNIQUE (device_id,id,generator_type), UNIQUE (device_id,id,mapping_type),
        FOREIGN KEY (device_id,mapping_type) REFERENCES devices(id,protocol) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE increment_generators (
        device_id TEXT NOT NULL, signal_id TEXT NOT NULL,
        generator_type TEXT NOT NULL DEFAULT 'increment' CHECK (generator_type='increment'),
        initial_value_token TEXT NOT NULL, value_min_token TEXT NOT NULL,
        value_max_token TEXT NOT NULL, step_min_token TEXT NOT NULL, step_max_token TEXT NOT NULL,
        boundary TEXT NOT NULL CHECK (boundary IN ('wrap','clamp')),
        PRIMARY KEY (device_id,signal_id),
        FOREIGN KEY (device_id,signal_id,generator_type)
          REFERENCES signals(device_id,id,generator_type) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE random_range_generators (
        device_id TEXT NOT NULL, signal_id TEXT NOT NULL,
        generator_type TEXT NOT NULL DEFAULT 'random_range' CHECK (generator_type='random_range'),
        min_value_token TEXT NOT NULL, max_value_token TEXT NOT NULL,
        precision INTEGER NOT NULL CHECK (precision BETWEEN 0 AND 15),
        PRIMARY KEY (device_id,signal_id),
        FOREIGN KEY (device_id,signal_id,generator_type)
          REFERENCES signals(device_id,id,generator_type) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE opcua_signal_mappings (
        device_id TEXT NOT NULL, signal_id TEXT NOT NULL,
        mapping_type TEXT NOT NULL DEFAULT 'opcua' CHECK (mapping_type='opcua'),
        node_id TEXT NOT NULL UNIQUE, browse_name TEXT, display_name TEXT, data_type TEXT,
        PRIMARY KEY (device_id,signal_id),
        FOREIGN KEY (device_id,signal_id,mapping_type)
          REFERENCES signals(device_id,id,mapping_type) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE mqtt_signal_mappings (
        device_id TEXT NOT NULL, signal_id TEXT NOT NULL,
        mapping_type TEXT NOT NULL DEFAULT 'mqtt' CHECK (mapping_type='mqtt'),
        field_name TEXT NOT NULL, PRIMARY KEY (device_id,signal_id),
        UNIQUE (device_id,field_name),
        FOREIGN KEY (device_id,signal_id,mapping_type)
          REFERENCES signals(device_id,id,mapping_type) ON DELETE CASCADE
    ) STRICT""",
    """CREATE TABLE modbus_signal_mappings (
        device_id TEXT NOT NULL, signal_id TEXT NOT NULL,
        mapping_type TEXT NOT NULL DEFAULT 'modbus' CHECK (mapping_type='modbus'),
        table_name TEXT NOT NULL CHECK (table_name IN ('coil','discrete_input','input_register','holding_register')),
        address INTEGER NOT NULL CHECK (address BETWEEN 0 AND 65535),
        wire_type TEXT NOT NULL CHECK (wire_type IN ('bool','int16','uint16','int32','uint32','float32','float64')),
        register_length INTEGER NOT NULL CHECK (register_length BETWEEN 1 AND 4),
        signed INTEGER CHECK (signed IS NULL OR signed IN (0,1)),
        scale_token TEXT NOT NULL, offset_token TEXT NOT NULL,
        byte_order TEXT NOT NULL CHECK (byte_order IN ('big','little')),
        word_order TEXT NOT NULL CHECK (word_order IN ('big','little')),
        CHECK (address + register_length <= 65536),
        PRIMARY KEY (device_id,signal_id),
        FOREIGN KEY (device_id,signal_id,mapping_type)
          REFERENCES signals(device_id,id,mapping_type) ON DELETE CASCADE
    ) STRICT""",
    "CREATE INDEX idx_devices_protocol_state ON devices(protocol,enabled,desired_state)",
    "CREATE INDEX idx_signals_device_order ON signals(device_id,sort_order)",
    "CREATE INDEX idx_modbus_mapping_ranges ON modbus_signal_mappings(device_id,table_name,address,register_length)",
    """CREATE TRIGGER modbus_mapping_no_overlap_insert BEFORE INSERT ON modbus_signal_mappings
    WHEN EXISTS (SELECT 1 FROM modbus_signal_mappings existing
      WHERE existing.device_id=NEW.device_id AND existing.table_name=NEW.table_name
        AND NEW.address < existing.address + existing.register_length
        AND existing.address < NEW.address + NEW.register_length)
    BEGIN SELECT RAISE(ABORT, 'overlapping Modbus mappings'); END""",
    """CREATE TRIGGER modbus_mapping_no_overlap_update BEFORE UPDATE ON modbus_signal_mappings
    WHEN EXISTS (SELECT 1 FROM modbus_signal_mappings existing
      WHERE existing.device_id=NEW.device_id AND existing.table_name=NEW.table_name
        AND NOT (existing.device_id=OLD.device_id AND existing.signal_id=OLD.signal_id)
        AND NEW.address < existing.address + existing.register_length
        AND existing.address < NEW.address + NEW.register_length)
    BEGIN SELECT RAISE(ABORT, 'overlapping Modbus mappings'); END""",
)

V2_TABLES = {
    "app_metadata", "web_settings", "opcua_settings", "mqtt_settings", "modbus_settings",
    "devices", "opcua_device_configs", "mqtt_device_configs", "modbus_device_configs",
    "signals", "increment_generators", "random_range_generators",
    "opcua_signal_mappings", "mqtt_signal_mappings", "modbus_signal_mappings",
}

V2_REQUIRED_COLUMNS = {
    "app_metadata": {"id", "config_version", "revision", "random_seed_token", "updated_at"},
    "web_settings": {"app_id", "host", "port"},
    "opcua_settings": {"app_id", "enabled", "host", "port", "endpoint_path", "server_name", "application_uri", "namespace_uri"},
    "mqtt_settings": {"app_id", "enabled", "host", "port", "protocol_version", "client_id", "keepalive", "username", "password_env", "reconnect_min_seconds", "reconnect_max_seconds", "max_inflight_messages", "max_queued_messages"},
    "modbus_settings": {"app_id", "enabled", "host", "port"},
    "devices": {"id", "app_id", "sort_order", "name", "enabled", "desired_state", "default_interval_ms", "protocol"},
    "opcua_device_configs": {"device_id", "protocol", "object_name"},
    "mqtt_device_configs": {"device_id", "protocol", "topic", "qos", "retain", "publish_interval_ms", "payload_mode"},
    "modbus_device_configs": {"device_id", "protocol", "unit_id"},
    "signals": {"device_id", "id", "sort_order", "name", "unit", "value_type", "interval_ms", "generator_type", "mapping_type"},
    "increment_generators": {"device_id", "signal_id", "generator_type", "initial_value_token", "value_min_token", "value_max_token", "step_min_token", "step_max_token", "boundary"},
    "random_range_generators": {"device_id", "signal_id", "generator_type", "min_value_token", "max_value_token", "precision"},
    "opcua_signal_mappings": {"device_id", "signal_id", "mapping_type", "node_id", "browse_name", "display_name", "data_type"},
    "mqtt_signal_mappings": {"device_id", "signal_id", "mapping_type", "field_name"},
    "modbus_signal_mappings": {"device_id", "signal_id", "mapping_type", "table_name", "address", "wire_type", "register_length", "signed", "scale_token", "offset_token", "byte_order", "word_order"},
}

V2_TRIGGERS = {"modbus_mapping_no_overlap_insert", "modbus_mapping_no_overlap_update"}
V2_INDEXES = {
    "idx_devices_protocol_state",
    "idx_signals_device_order",
    "idx_modbus_mapping_ranges",
}

# Schema v3 keeps the v2 normalized graph and adds named MQTT connections.
# ``mqtt_settings`` remains the backwards-compatible default connection row.
V3_MQTT_CONNECTIONS_TABLE = """CREATE TABLE mqtt_connections (
    id TEXT PRIMARY KEY,
    app_id INTEGER NOT NULL DEFAULT 1 CHECK (app_id = 1),
    sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    host TEXT NOT NULL,
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    protocol_version TEXT NOT NULL CHECK (protocol_version IN ('3.1.1','5.0')),
    client_id TEXT NOT NULL,
    keepalive INTEGER NOT NULL CHECK (keepalive BETWEEN 1 AND 65535),
    username TEXT,
    password TEXT,
    password_env TEXT,
    transport TEXT NOT NULL CHECK (transport = 'tcp'),
    tls_enabled INTEGER NOT NULL CHECK (tls_enabled IN (0,1)),
    reconnect_min_seconds INTEGER NOT NULL CHECK (reconnect_min_seconds >= 1),
    reconnect_max_seconds INTEGER NOT NULL CHECK (reconnect_max_seconds >= reconnect_min_seconds),
    max_inflight_messages INTEGER NOT NULL CHECK (max_inflight_messages >= 1),
    max_queued_messages INTEGER NOT NULL CHECK (max_queued_messages >= 1),
    UNIQUE (app_id, sort_order),
    FOREIGN KEY (app_id) REFERENCES app_metadata(id) ON DELETE CASCADE
) STRICT"""

V3_MQTT_DEVICE_CONFIGS_TABLE = """CREATE TABLE mqtt_device_configs (
    device_id TEXT PRIMARY KEY,
    protocol TEXT NOT NULL DEFAULT 'mqtt' CHECK (protocol='mqtt'),
    connection_id TEXT NOT NULL DEFAULT 'default',
    topic TEXT NOT NULL,
    qos INTEGER NOT NULL CHECK (qos IN (0,1,2)),
    retain INTEGER NOT NULL CHECK (retain IN (0,1)),
    publish_interval_ms INTEGER CHECK (publish_interval_ms IS NULL OR publish_interval_ms >= 1000),
    payload_mode TEXT NOT NULL CHECK (payload_mode IN ('nested','flat','values')),
    FOREIGN KEY (device_id, protocol) REFERENCES devices(id, protocol) ON DELETE CASCADE,
    FOREIGN KEY (connection_id) REFERENCES mqtt_connections(id)
) STRICT"""

V3_TABLES = V2_TABLES | {"mqtt_connections"}
V3_REQUIRED_COLUMNS = dict(V2_REQUIRED_COLUMNS)
V3_REQUIRED_COLUMNS.update({
    "mqtt_settings": V2_REQUIRED_COLUMNS["mqtt_settings"]
        | {"password", "transport", "tls_enabled"},
    "mqtt_connections": {
        "id", "app_id", "sort_order", "name", "enabled", "host", "port",
        "protocol_version", "client_id", "keepalive", "username", "password",
        "password_env", "transport", "tls_enabled", "reconnect_min_seconds",
        "reconnect_max_seconds", "max_inflight_messages", "max_queued_messages",
    },
    "mqtt_device_configs": V2_REQUIRED_COLUMNS["mqtt_device_configs"] | {"connection_id"},
})
V3_TRIGGERS = V2_TRIGGERS
V3_INDEXES = V2_INDEXES | {"idx_mqtt_connections_order", "idx_mqtt_devices_connection"}
