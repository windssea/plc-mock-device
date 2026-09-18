"""Versioned configuration models for the IoT simulator."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


REDACTED_PASSWORD = "********"


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", validate_assignment=True, populate_by_name=True
    )


class ProtocolType(StrEnum):
    OPCUA = "opcua"
    MQTT = "mqtt"
    MODBUS = "modbus"


class ValueType(StrEnum):
    BOOL = "bool"
    INT16 = "int16"
    UINT16 = "uint16"
    INT32 = "int32"
    UINT32 = "uint32"
    FLOAT32 = "float32"
    FLOAT64 = "float64"


class BoundaryMode(StrEnum):
    WRAP = "wrap"
    CLAMP = "clamp"


class ModbusTable(StrEnum):
    COIL = "coil"
    DISCRETE_INPUT = "discrete_input"
    INPUT_REGISTER = "input_register"
    HOLDING_REGISTER = "holding_register"


class ByteOrder(StrEnum):
    BIG = "big"
    LITTLE = "little"


class WordOrder(StrEnum):
    BIG = "big"
    LITTLE = "little"


class IncrementGeneratorConfig(StrictModel):
    type: Literal["increment"] = "increment"
    initial_value: int | float
    value_min: int | float
    value_max: int | float
    step_min: int | float = 1
    step_max: int | float = 1
    boundary: BoundaryMode = BoundaryMode.WRAP

    @model_validator(mode="after")
    def validate_range(self) -> "IncrementGeneratorConfig":
        values = (
            self.initial_value,
            self.value_min,
            self.value_max,
            self.step_min,
            self.step_max,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("increment values must be finite")
        if self.value_min >= self.value_max:
            raise ValueError("value_min must be less than value_max")
        if not self.value_min <= self.initial_value <= self.value_max:
            raise ValueError("initial_value must be within value_min/value_max")
        if self.step_min > self.step_max:
            raise ValueError("step_min must not exceed step_max")
        if self.step_min == 0 and self.step_max == 0:
            raise ValueError("increment step range cannot be only zero")
        return self


class RandomRangeGeneratorConfig(StrictModel):
    type: Literal["random_range"] = "random_range"
    min: int | float
    max: int | float
    precision: int = Field(default=2, ge=0, le=15)

    @model_validator(mode="after")
    def validate_range(self) -> "RandomRangeGeneratorConfig":
        if not math.isfinite(float(self.min)) or not math.isfinite(float(self.max)):
            raise ValueError("random range values must be finite")
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


GeneratorConfig = Annotated[
    IncrementGeneratorConfig | RandomRangeGeneratorConfig,
    Field(discriminator="type"),
]


class OpcUaSignalMapping(StrictModel):
    type: Literal["opcua"] = "opcua"
    node_id: str = Field(min_length=1)
    browse_name: str | None = None
    display_name: str | None = None
    data_type: ValueType | None = None


class MqttSignalMapping(StrictModel):
    type: Literal["mqtt"] = "mqtt"
    field_name: str = Field(
        min_length=1, validation_alias=AliasChoices("field_name", "json_field")
    )

    @property
    def json_field(self) -> str:
        return self.field_name


_WIRE_REGISTER_LENGTH: dict[ValueType, int] = {
    ValueType.INT16: 1,
    ValueType.UINT16: 1,
    ValueType.INT32: 2,
    ValueType.UINT32: 2,
    ValueType.FLOAT32: 2,
    ValueType.FLOAT64: 4,
}


class ModbusSignalMapping(StrictModel):
    type: Literal["modbus"] = "modbus"
    table: ModbusTable
    address: int = Field(ge=0, le=65535)
    wire_type: ValueType
    register_length: int | None = Field(default=None, ge=1, le=4)
    signed: bool | None = None
    scale: float = 1.0
    offset: float = 0.0
    byte_order: ByteOrder = ByteOrder.BIG
    word_order: WordOrder = WordOrder.BIG

    @model_validator(mode="after")
    def validate_mapping(self) -> "ModbusSignalMapping":
        bit_table = self.table in {ModbusTable.COIL, ModbusTable.DISCRETE_INPUT}
        if bit_table:
            if self.wire_type is not ValueType.BOOL:
                raise ValueError("coil/discrete_input mappings require bool wire_type")
            if self.register_length not in (None, 1):
                raise ValueError("bit mappings occupy exactly one address")
            object.__setattr__(self, "register_length", 1)
        else:
            if self.wire_type is ValueType.BOOL:
                raise ValueError("register mappings require a numeric wire_type")
            expected = _WIRE_REGISTER_LENGTH[self.wire_type]
            if self.register_length not in (None, expected):
                raise ValueError(
                    f"{self.wire_type.value} requires register_length={expected}"
                )
            object.__setattr__(self, "register_length", expected)
        if not math.isfinite(self.scale) or not math.isfinite(self.offset):
            raise ValueError("scale and offset must be finite")
        if self.scale == 0:
            raise ValueError("scale must not be zero")
        if self.address + (self.register_length or 1) > 65536:
            raise ValueError("mapping extends beyond Modbus address 65535")
        return self


SignalMapping = Annotated[
    OpcUaSignalMapping | MqttSignalMapping | ModbusSignalMapping,
    Field(discriminator="type"),
]


_TYPE_BOUNDS: dict[ValueType, tuple[int | float, int | float]] = {
    ValueType.BOOL: (0, 1),
    ValueType.INT16: (-32768, 32767),
    ValueType.UINT16: (0, 65535),
    ValueType.INT32: (-2147483648, 2147483647),
    ValueType.UINT32: (0, 4294967295),
    ValueType.FLOAT32: (-3.4028235e38, 3.4028235e38),
    ValueType.FLOAT64: (-1.7976931348623157e308, 1.7976931348623157e308),
}


class SignalConfig(StrictModel):
    id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
        validation_alias=AliasChoices("id", "signal_id"),
    )
    name: str = Field(min_length=1, max_length=256)
    unit: str | None = Field(default=None, max_length=64)
    value_type: ValueType
    interval_ms: int | None = Field(default=None, ge=1000)
    generator: GeneratorConfig
    binding: SignalMapping = Field(validation_alias=AliasChoices("binding", "mapping"))

    @property
    def signal_id(self) -> str:
        return self.id

    @property
    def mapping(self) -> SignalMapping:
        return self.binding

    @model_validator(mode="after")
    def validate_value_domain(self) -> "SignalConfig":
        generator_values: list[int | float]
        if isinstance(self.generator, IncrementGeneratorConfig):
            generator_values = [
                self.generator.initial_value,
                self.generator.value_min,
                self.generator.value_max,
            ]
            if self.value_type is ValueType.BOOL:
                raise ValueError("bool signals do not support increment generators")
        else:
            generator_values = [self.generator.min, self.generator.max]

        low, high = _TYPE_BOUNDS[self.value_type]
        if any(value < low or value > high for value in generator_values):
            raise ValueError(f"generator values exceed {self.value_type.value} bounds")

        integer_types = {
            ValueType.BOOL,
            ValueType.INT16,
            ValueType.UINT16,
            ValueType.INT32,
            ValueType.UINT32,
        }
        if self.value_type in integer_types:
            all_values = list(generator_values)
            if isinstance(self.generator, IncrementGeneratorConfig):
                all_values.extend([self.generator.step_min, self.generator.step_max])
            if any(not float(value).is_integer() for value in all_values):
                raise ValueError(f"{self.value_type.value} generator values must be integers")
            if isinstance(self.generator, RandomRangeGeneratorConfig) and self.value_type is ValueType.BOOL:
                if self.generator.min not in (0, 1) or self.generator.max not in (0, 1):
                    raise ValueError("bool random range must use bounds 0 or 1")

        if isinstance(self.mapping, OpcUaSignalMapping):
            if self.mapping.data_type is not None and self.mapping.data_type is not self.value_type:
                raise ValueError("OPC UA data_type must match signal value_type")
        elif isinstance(self.mapping, ModbusSignalMapping):
            bit_table = self.mapping.table in {
                ModbusTable.COIL,
                ModbusTable.DISCRETE_INPUT,
            }
            if bit_table and self.value_type is not ValueType.BOOL:
                raise ValueError("bit-table signals must have bool value_type")
            if not bit_table and self.value_type is ValueType.BOOL:
                raise ValueError("bool signals must use a Modbus bit table")
            if not bit_table:
                wire_low, wire_high = _TYPE_BOUNDS[self.mapping.wire_type]
                raw_values = [
                    (float(value) - self.mapping.offset) / self.mapping.scale
                    for value in generator_values
                ]
                if self.mapping.wire_type in {
                    ValueType.INT16,
                    ValueType.UINT16,
                    ValueType.INT32,
                    ValueType.UINT32,
                }:
                    raw_values = [round(value) for value in raw_values]
                if any(
                    not math.isfinite(value) or value < wire_low or value > wire_high
                    for value in raw_values
                ):
                    raise ValueError(
                        "generator range exceeds Modbus wire_type after scale/offset"
                    )
        return self


class OpcUaDeviceProtocolConfig(StrictModel):
    type: Literal["opcua"] = Field(default="opcua", exclude=True)
    object_name: str | None = None


class MqttDeviceProtocolConfig(StrictModel):
    type: Literal["mqtt"] = Field(default="mqtt", exclude=True)
    connection_id: str = Field(default="default", min_length=1, max_length=128,
                               pattern=r"^[A-Za-z0-9_.-]+$")
    topic: str = Field(min_length=1, max_length=65535)
    qos: Literal[0, 1, 2] = 0
    retain: bool = False
    publish_interval_ms: int | None = Field(default=None, ge=1000)
    payload_mode: Literal["nested", "flat", "values"] = "nested"

    @model_validator(mode="after")
    def validate_topic(self) -> "MqttDeviceProtocolConfig":
        if "+" in self.topic or "#" in self.topic or "\x00" in self.topic:
            raise ValueError("MQTT publish topic cannot contain wildcards or NUL")
        return self


class ModbusDeviceProtocolConfig(StrictModel):
    type: Literal["modbus"] = Field(default="modbus", exclude=True)
    unit_id: int = Field(ge=1, le=247)


DeviceProtocolConfig = Annotated[
    OpcUaDeviceProtocolConfig | MqttDeviceProtocolConfig | ModbusDeviceProtocolConfig,
    Field(discriminator="type"),
]


class DeviceConfig(StrictModel):
    id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
        validation_alias=AliasChoices("id", "device_id"),
    )
    name: str = Field(min_length=1, max_length=256)
    enabled: bool = True
    desired_state: Literal["running", "stopped"] = "running"
    default_interval_ms: int = Field(
        default=1000,
        ge=1000,
        validation_alias=AliasChoices("default_interval_ms", "interval_ms"),
    )
    protocol: ProtocolType
    protocol_config: DeviceProtocolConfig
    signals: list[SignalConfig] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def add_protocol_discriminator(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        protocol = data.get("protocol")
        protocol_config = data.get("protocol_config")
        if protocol is not None and isinstance(protocol_config, dict):
            data = dict(data)
            data["protocol_config"] = {"type": protocol, **protocol_config}
        return data

    @property
    def device_id(self) -> str:
        return self.id

    @property
    def interval_ms(self) -> int:
        return self.default_interval_ms

    @model_validator(mode="after")
    def validate_protocol_and_signals(self) -> "DeviceConfig":
        if self.protocol_config.type != self.protocol.value:
            raise ValueError("protocol_config kind must match device protocol")
        signal_ids = [signal.signal_id for signal in self.signals]
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError(f"duplicate signal_id in device {self.device_id}")
        mapping_kinds = {signal.binding.type for signal in self.signals}
        if mapping_kinds and mapping_kinds != {self.protocol.value}:
            raise ValueError("all signal mappings must match device protocol")
        mqtt_fields = [
            signal.binding.field_name
            for signal in self.signals
            if isinstance(signal.mapping, MqttSignalMapping)
        ]
        if len(mqtt_fields) != len(set(mqtt_fields)):
            raise ValueError(f"duplicate MQTT json_field in device {self.device_id}")
        return self


class WebConfig(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)


class OpcUaEndpointConfig(StrictModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=4840, ge=1, le=65535)
    endpoint_path: str = "/iot-simulator/"
    server_name: str = "IoT Simulator"
    application_uri: str = "urn:iot-simulator:server"
    namespace_uri: str = "urn:iot-simulator:devices"


class MqttEndpointConfig(StrictModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = Field(default=1883, ge=1, le=65535)
    protocol_version: Literal["3.1.1", "5.0"] = Field(
        default="3.1.1", validation_alias=AliasChoices("protocol_version", "version")
    )
    client_id: str = "iot-simulator"
    keepalive: int = Field(default=60, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    password_env: str | None = None
    transport: Literal["tcp"] = "tcp"
    tls_enabled: bool = False
    reconnect_min_seconds: int = Field(default=1, ge=1)
    reconnect_max_seconds: int = Field(default=60, ge=1)
    max_inflight_messages: int = Field(
        default=20,
        ge=1,
        validation_alias=AliasChoices("max_inflight_messages", "max_inflight"),
    )
    max_queued_messages: int = Field(
        default=1000,
        ge=1,
        validation_alias=AliasChoices("max_queued_messages", "max_queue"),
    )

    @property
    def version(self) -> str:
        return self.protocol_version

    @property
    def max_inflight(self) -> int:
        return self.max_inflight_messages

    @property
    def max_queue(self) -> int:
        return self.max_queued_messages

    @model_validator(mode="after")
    def validate_reconnect(self) -> "MqttEndpointConfig":
        if self.password is not None and self.password_env is not None:
            raise ValueError("password and password_env are mutually exclusive")
        if self.reconnect_min_seconds > self.reconnect_max_seconds:
            raise ValueError("reconnect_min_seconds must not exceed reconnect_max_seconds")
        return self


class MqttConnectionConfig(MqttEndpointConfig):
    """A named MQTT broker connection persisted in SQLite."""

    id: str = Field(default="default", min_length=1, max_length=128,
                    pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(default="Default MQTT", min_length=1, max_length=256)

    @classmethod
    def from_endpoint(
        cls, endpoint: MqttEndpointConfig, *, connection_id: str = "default",
        name: str = "Default MQTT",
    ) -> "MqttConnectionConfig":
        return cls(id=connection_id, name=name, **endpoint.model_dump(mode="python"))

    def to_endpoint(self) -> MqttEndpointConfig:
        values = self.model_dump(mode="python")
        values.pop("id", None)
        values.pop("name", None)
        return MqttEndpointConfig.model_validate(values)


class ModbusEndpointConfig(StrictModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=1502, ge=1, le=65535)


class AppConfig(StrictModel):
    version: Literal[1] = 1
    config_revision: int = Field(default=0, ge=0)
    random_seed: int = 0
    web: WebConfig = Field(default_factory=WebConfig)
    opcua: OpcUaEndpointConfig = Field(default_factory=OpcUaEndpointConfig)
    mqtt: MqttEndpointConfig = Field(default_factory=MqttEndpointConfig)
    mqtt_connections: list[MqttConnectionConfig] = Field(default_factory=list)
    modbus: ModbusEndpointConfig = Field(default_factory=ModbusEndpointConfig)
    devices: list[DeviceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_global_uniqueness(self) -> "AppConfig":
        # ``mqtt`` remains the backwards-compatible representation of the default
        # connection.  Existing API clients can keep updating it; named profiles
        # are the canonical extension used by devices and the MQTT client pool.
        connections = list(self.mqtt_connections)
        default = MqttConnectionConfig.from_endpoint(self.mqtt)
        default_index = next(
            (index for index, item in enumerate(connections) if item.id == "default"),
            None,
        )
        if default_index is None:
            connections.insert(0, default)
        else:
            connections[default_index] = default.model_copy(
                update={"name": connections[default_index].name}
            )
        connection_ids = [item.id for item in connections]
        if len(connection_ids) != len(set(connection_ids)):
            raise ValueError("MQTT connection id values must be globally unique")
        object.__setattr__(self, "mqtt_connections", connections)

        device_ids = [device.device_id for device in self.devices]
        if len(device_ids) != len(set(device_ids)):
            raise ValueError("device_id values must be globally unique")

        node_ids: set[str] = set()
        unit_ids: set[int] = set()
        occupied: dict[tuple[int, ModbusTable], list[tuple[int, int, str]]] = {}
        for device in self.devices:
            if isinstance(device.protocol_config, MqttDeviceProtocolConfig):
                connection = next(
                    (
                        item for item in connections
                        if item.id == device.protocol_config.connection_id
                    ),
                    None,
                )
                if connection is None:
                    raise ValueError(
                        f"MQTT device {device.id} references unknown connection_id "
                        f"{device.protocol_config.connection_id}"
                    )
                if not connection.enabled:
                    raise ValueError(
                        f"MQTT device {device.id} references disabled connection_id "
                        f"{device.protocol_config.connection_id}"
                    )
            if isinstance(device.protocol_config, ModbusDeviceProtocolConfig):
                unit_id = device.protocol_config.unit_id
                if unit_id in unit_ids:
                    raise ValueError(f"duplicate Modbus unit_id {unit_id}")
                unit_ids.add(unit_id)
            for signal in device.signals:
                mapping = signal.mapping
                if isinstance(mapping, OpcUaSignalMapping):
                    if mapping.node_id in node_ids:
                        raise ValueError(f"duplicate OPC UA node_id {mapping.node_id}")
                    node_ids.add(mapping.node_id)
                elif isinstance(mapping, ModbusSignalMapping):
                    assert isinstance(device.protocol_config, ModbusDeviceProtocolConfig)
                    key = (device.protocol_config.unit_id, mapping.table)
                    start = mapping.address
                    end = start + (mapping.register_length or 1)
                    ranges = occupied.setdefault(key, [])
                    for other_start, other_end, other_signal in ranges:
                        if start < other_end and other_start < end:
                            raise ValueError(
                                "overlapping Modbus mappings for "
                                f"unit {key[0]} {key[1].value}: "
                                f"{other_signal} and {signal.signal_id}"
                            )
                    ranges.append((start, end, signal.signal_id))
        return self


class BootstrapConfig(StrictModel):
    """One-time startup defaults; runtime devices never belong in this model."""

    version: Literal[1] = 1
    random_seed: int = 0
    web: WebConfig = Field(default_factory=WebConfig)
    opcua: OpcUaEndpointConfig = Field(default_factory=OpcUaEndpointConfig)
    mqtt: MqttEndpointConfig = Field(default_factory=MqttEndpointConfig)
    modbus: ModbusEndpointConfig = Field(default_factory=ModbusEndpointConfig)

    def to_app_config(self) -> AppConfig:
        return AppConfig(
            version=self.version,
            config_revision=0,
            random_seed=self.random_seed,
            web=self.web.model_copy(deep=True),
            opcua=self.opcua.model_copy(deep=True),
            mqtt=self.mqtt.model_copy(deep=True),
            modbus=self.modbus.model_copy(deep=True),
            devices=[],
        )


def value_type_bounds(value_type: ValueType) -> tuple[int | float, int | float]:
    """Return the inclusive domain represented by a configured value type."""

    return _TYPE_BOUNDS[value_type]
