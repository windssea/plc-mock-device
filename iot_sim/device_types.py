"""Code-defined mock device catalog and address allocation.

Templates are copied into ordinary ``DeviceConfig`` values. Nothing in this
module is stored, and later edits to a template do not change devices that
were already created.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from iot_sim.models import (
    AppConfig,
    ByteOrder,
    DeviceConfig,
    IncrementGeneratorConfig,
    ModbusDeviceProtocolConfig,
    ModbusSignalMapping,
    ModbusTable,
    MqttDeviceProtocolConfig,
    MqttSignalMapping,
    OpcUaDeviceProtocolConfig,
    OpcUaSignalMapping,
    ProtocolType,
    RandomRangeGeneratorConfig,
    SignalConfig,
    StrictModel,
    ValueType,
    WordOrder,
    _WIRE_REGISTER_LENGTH,
)

CATALOG_VERSION: int = 1

_MAX_DEVICE_NUMBER = 9999
_MIN_MODBUS_UNIT_ID = 1
_MAX_MODBUS_UNIT_ID = 247
_MQTT_ONLY_FIELDS = (
    "connection_id",
    "topic",
    "qos",
    "retain",
    "payload_mode",
    "publish_interval_ms",
)
_OPCUA_ONLY_FIELDS = ("node_ids",)
_MODBUS_ONLY_FIELDS = ("unit_id", "address_base", "addresses")
# OPCUAAdapter.start also creates these NodeIds beside the configured signal nodes.
_OPCUA_DEVICES_FOLDER = "Devices"

_SUGGESTION_KEYS = (
    "device_id",
    "name",
    "topic",
    "node_ids",
    "unit_id",
    "addresses",
    "allocatable",
    "error",
)


class DeviceFromTypeRequest(StrictModel):
    """Create-time overrides. Protocol applicability is checked while building."""

    type_id: str = Field(min_length=1, max_length=128)
    device_id: str | None = None
    name: str | None = None
    enabled: bool = True
    desired_state: Literal["running", "stopped"] = "running"
    connection_id: str | None = None
    topic: str | None = None
    qos: Literal[0, 1, 2] | None = None
    retain: bool | None = None
    payload_mode: Literal["nested", "flat", "values"] | None = None
    publish_interval_ms: int | None = Field(default=None, ge=1000)
    unit_id: int | None = Field(default=None, ge=1, le=247)
    address_base: int | None = Field(default=None, ge=0, le=65535)
    addresses: dict[str, int] | None = None
    node_ids: dict[str, str] | None = None


class _CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SignalTemplate(_CatalogModel):
    id: str
    name: str
    unit: str | None
    value_type: ValueType
    generator: IncrementGeneratorConfig | RandomRangeGeneratorConfig
    browse_name: str | None = None
    display_name: str | None = None
    field_name: str | None = None
    table: ModbusTable | None = None
    wire_type: ValueType | None = None


class DeviceType(_CatalogModel):
    id: str
    name: str
    description: str
    protocol: ProtocolType
    id_prefix: str
    default_interval_ms: int = 1000
    signals: tuple[SignalTemplate, ...]
    topic_pattern: str | None = None
    connection_id: str | None = None
    qos: Literal[0, 1, 2] | None = None
    retain: bool | None = None
    payload_mode: Literal["nested", "flat", "values"] | None = None
    publish_interval_ms: int | None = None

    @model_validator(mode="after")
    def check_protocol_shape(self) -> DeviceType:
        if self.protocol is ProtocolType.MQTT:
            if (
                self.topic_pattern is None
                or self.connection_id is None
                or self.qos is None
                or self.retain is None
                or self.payload_mode is None
            ):
                raise ValueError(f"{self.id} is missing MQTT defaults")
            if any(signal.field_name is None for signal in self.signals):
                raise ValueError(f"{self.id} is missing a MQTT field name")
            return self
        if self.topic_pattern is not None or self.connection_id is not None:
            raise ValueError(f"{self.id} carries MQTT defaults")
        if self.protocol is ProtocolType.OPCUA:
            if any(signal.browse_name is None or signal.display_name is None for signal in self.signals):
                raise ValueError(f"{self.id} is missing OPC UA browse metadata")
            return self
        if any(signal.table is None or signal.wire_type is None for signal in self.signals):
            raise ValueError(f"{self.id} is missing Modbus address metadata")
        return self


def _range(minimum: int | float, maximum: int | float, precision: int) -> RandomRangeGeneratorConfig:
    return RandomRangeGeneratorConfig(min=minimum, max=maximum, precision=precision)


def _increment(
    initial_value: int | float,
    value_min: int | float,
    value_max: int | float,
    step_min: int | float,
    step_max: int | float,
) -> IncrementGeneratorConfig:
    return IncrementGeneratorConfig(
        initial_value=initial_value,
        value_min=value_min,
        value_max=value_max,
        step_min=step_min,
        step_max=step_max,
        boundary="wrap",
    )


def _mqtt_signal(
    signal_id: str,
    name: str,
    unit: str,
    value_type: ValueType,
    generator: IncrementGeneratorConfig | RandomRangeGeneratorConfig,
    field_name: str,
) -> SignalTemplate:
    return SignalTemplate(
        id=signal_id,
        name=name,
        unit=unit,
        value_type=value_type,
        generator=generator,
        field_name=field_name,
    )


DEVICE_TYPES: tuple[DeviceType, ...] = (
    DeviceType(
        id="opcua.generic",
        name="OPC UA 示例设备",
        description="OPC UA 只读温度点，NodeId 为 {device_id}.temperature",
        protocol=ProtocolType.OPCUA,
        id_prefix="opcua",
        signals=(
            SignalTemplate(
                id="temperature",
                name="温度",
                unit="°C",
                value_type=ValueType.FLOAT32,
                generator=_range(18, 35, 2),
                browse_name="Temperature",
                display_name="Temperature",
            ),
        ),
    ),
    DeviceType(
        id="mqtt.counter",
        name="MQTT 计数设备",
        description="MQTT 计数器，nested 载荷，topic 前缀 devices/",
        protocol=ProtocolType.MQTT,
        id_prefix="mqtt",
        topic_pattern="devices/{device_id}/telemetry",
        connection_id="default",
        qos=0,
        retain=False,
        payload_mode="nested",
        publish_interval_ms=None,
        signals=(
            _mqtt_signal(
                "counter",
                "计数器",
                "count",
                ValueType.UINT32,
                _increment(0, 0, 10000, 1, 3),
                "counter",
            ),
        ),
    ),
    DeviceType(
        id="modbus.pressure",
        name="Modbus 压力设备",
        description="Modbus TCP 压力点，Input Register，float32 占两个寄存器",
        protocol=ProtocolType.MODBUS,
        id_prefix="modbus",
        signals=(
            SignalTemplate(
                id="pressure",
                name="压力",
                unit="bar",
                value_type=ValueType.FLOAT32,
                generator=_range(1, 10, 2),
                table=ModbusTable.INPUT_REGISTER,
                wire_type=ValueType.FLOAT32,
            ),
        ),
    ),
    DeviceType(
        id="mqtt.temperature",
        name="温度传感器",
        description="MQTT 温度与湿度，nested 载荷，topic 前缀 iot-simulator/test/",
        protocol=ProtocolType.MQTT,
        id_prefix="mqtt-temp",
        topic_pattern="iot-simulator/test/{device_id}/telemetry",
        connection_id="default",
        qos=1,
        retain=False,
        payload_mode="nested",
        publish_interval_ms=1000,
        signals=(
            _mqtt_signal(
                "temperature",
                "Temperature",
                "C",
                ValueType.FLOAT32,
                _range(18, 32, 2),
                "temperature",
            ),
            _mqtt_signal(
                "humidity",
                "Humidity",
                "%",
                ValueType.UINT16,
                _range(35, 85, 0),
                "humidity",
            ),
        ),
    ),
    DeviceType(
        id="mqtt.power_meter",
        name="电能表",
        description="MQTT 电压、电流与电能，flat 载荷，发布周期 2000 ms",
        protocol=ProtocolType.MQTT,
        id_prefix="mqtt-power",
        topic_pattern="iot-simulator/test/{device_id}/telemetry",
        connection_id="default",
        qos=1,
        retain=False,
        payload_mode="flat",
        publish_interval_ms=2000,
        signals=(
            _mqtt_signal("voltage", "Voltage", "V", ValueType.FLOAT32, _range(218, 242, 2), "voltage"),
            _mqtt_signal("current", "Current", "A", ValueType.FLOAT32, _range(1, 15, 2), "current"),
            _mqtt_signal(
                "energy",
                "Energy",
                "kWh",
                ValueType.FLOAT64,
                _increment(1000, 1000, 9999, 0.1, 0.5),
                "energy",
            ),
        ),
    ),
    DeviceType(
        id="mqtt.tank",
        name="储罐传感器",
        description="MQTT 液位与压力，values 载荷，topic 前缀 iot-simulator/test/",
        protocol=ProtocolType.MQTT,
        id_prefix="mqtt-tank",
        topic_pattern="iot-simulator/test/{device_id}/telemetry",
        connection_id="default",
        qos=0,
        retain=False,
        payload_mode="values",
        publish_interval_ms=1000,
        signals=(
            _mqtt_signal(
                "level",
                "Liquid Level",
                "%",
                ValueType.FLOAT32,
                _increment(20, 0, 100, 0.5, 2),
                "level",
            ),
            _mqtt_signal(
                "pressure",
                "Pressure",
                "kPa",
                ValueType.FLOAT32,
                _range(95, 110, 2),
                "pressure",
            ),
        ),
    ),
)

_TYPES_BY_ID = {item.id: item for item in DEVICE_TYPES}


def get_device_type(type_id: str) -> DeviceType:
    try:
        return _TYPES_BY_ID[type_id]
    except KeyError:
        raise ValueError(f"unknown device type: {type_id}") from None


def describe_device_type(device_type: DeviceType) -> dict[str, Any]:
    """Public catalog fields. Address suggestions are added by ``suggest``."""

    return {
        "id": device_type.id,
        "name": device_type.name,
        "description": device_type.description,
        "protocol": device_type.protocol.value,
        "id_prefix": device_type.id_prefix,
        "topic_pattern": device_type.topic_pattern,
        "default_interval_ms": device_type.default_interval_ms,
        "signals": [
            {
                "id": signal.id,
                "name": signal.name,
                "unit": signal.unit,
                "value_type": signal.value_type.value,
                "generator": signal.generator.model_dump(mode="json"),
            }
            for signal in device_type.signals
        ],
        "defaults": _public_defaults(device_type),
    }


def suggest(config: AppConfig, device_type: DeviceType) -> dict[str, Any]:
    """Return the next id and addresses. Allocation failure stays in ``error``."""

    try:
        device_id = _allocate_device_id(config, device_type.id_prefix, None)
    except ValueError as exc:
        return _suggestion(error=str(exc))
    name = _auto_name(device_type, device_id)
    topic: str | None = None
    node_ids: dict[str, str] = {}
    unit_id: int | None = None
    addresses: dict[str, int] = {}
    try:
        if device_type.protocol is ProtocolType.MQTT:
            topic = _auto_topic(device_type, device_id)
            _ensure_topic_available(config, topic)
        elif device_type.protocol is ProtocolType.OPCUA:
            node_ids = {signal.id: f"{device_id}.{signal.id}" for signal in device_type.signals}
        else:
            addresses = _pack_addresses(device_type, 0)
            unit_id = _allocate_unit_id(config, None)
    except ValueError as exc:
        return _suggestion(
            device_id=device_id,
            name=name,
            topic=topic,
            node_ids=node_ids,
            unit_id=unit_id,
            addresses=addresses,
            error=str(exc),
        )
    return _suggestion(
        device_id=device_id,
        name=name,
        topic=topic,
        node_ids=node_ids,
        unit_id=unit_id,
        addresses=addresses,
    )


def build_device_from_type(config: AppConfig, request: DeviceFromTypeRequest) -> DeviceConfig:
    """Expand one catalog type into a validated device. Does not write it."""

    device_type = get_device_type(request.type_id)
    _reject_inapplicable_fields(device_type, request)
    if request.address_base is not None and request.addresses is not None:
        raise ValueError("address_base and addresses are mutually exclusive")
    device_id = _allocate_device_id(config, device_type.id_prefix, request.device_id)
    name = request.name if request.name is not None else _auto_name(device_type, device_id)
    device = _assemble_device(config, device_type, request, device_id, name)
    _reject_runtime_node_ids(config, device)
    _ensure_candidate_accepts(config, device)
    return device


def _public_defaults(device_type: DeviceType) -> dict[str, Any]:
    if device_type.protocol is not ProtocolType.MQTT:
        return {}
    return {
        "connection_id": device_type.connection_id,
        "qos": device_type.qos,
        "retain": device_type.retain,
        "payload_mode": device_type.payload_mode,
        "publish_interval_ms": device_type.publish_interval_ms,
    }


def _suggestion(
    *,
    device_id: str | None = None,
    name: str | None = None,
    topic: str | None = None,
    node_ids: dict[str, str] | None = None,
    unit_id: int | None = None,
    addresses: dict[str, int] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    suggestion = {
        "device_id": device_id,
        "name": name,
        "topic": topic,
        "node_ids": {} if node_ids is None else node_ids,
        "unit_id": unit_id,
        "addresses": {} if addresses is None else addresses,
        "allocatable": error is None,
        "error": error,
    }
    assert tuple(suggestion) == _SUGGESTION_KEYS
    return suggestion


def _reject_inapplicable_fields(device_type: DeviceType, request: DeviceFromTypeRequest) -> None:
    fields: list[str] = []
    if device_type.protocol is not ProtocolType.MQTT:
        fields.extend(_MQTT_ONLY_FIELDS)
    if device_type.protocol is not ProtocolType.OPCUA:
        fields.extend(_OPCUA_ONLY_FIELDS)
    if device_type.protocol is not ProtocolType.MODBUS:
        fields.extend(_MODBUS_ONLY_FIELDS)
    for field in fields:
        if getattr(request, field) is not None:
            raise ValueError(f"{field} does not apply to protocol {device_type.protocol.value}")


def _allocate_device_id(config: AppConfig, prefix: str, requested: str | None) -> str:
    taken = {device.id for device in config.devices}
    if requested is not None:
        if requested in taken:
            raise ValueError(f"device already exists: {requested}")
        return requested
    for number in range(1, _MAX_DEVICE_NUMBER + 1):
        candidate = f"{prefix}-{number:03d}"
        if candidate not in taken:
            return candidate
    raise ValueError(f"device id space exhausted for prefix {prefix}")


def _canonical_number(prefix: str, device_id: str) -> int | None:
    marker = f"{prefix}-"
    if not device_id.startswith(marker):
        return None
    suffix = device_id[len(marker):]
    if not suffix.isdigit():
        return None
    number = int(suffix)
    if number < 1 or number > _MAX_DEVICE_NUMBER:
        return None
    if f"{prefix}-{number:03d}" != device_id:
        return None
    return number


def _auto_name(device_type: DeviceType, device_id: str) -> str:
    number = _canonical_number(device_type.id_prefix, device_id)
    if number is None:
        return f"{device_type.name} {device_id}"
    return f"{device_type.name} {number:03d}"


def _auto_topic(device_type: DeviceType, device_id: str) -> str:
    if device_type.topic_pattern is None:
        raise ValueError(f"{device_type.id} is missing MQTT defaults")
    return device_type.topic_pattern.format(device_id=device_id)


def _ensure_topic_available(config: AppConfig, topic: str) -> None:
    for device in config.devices:
        protocol_config = device.protocol_config
        if isinstance(protocol_config, MqttDeviceProtocolConfig) and protocol_config.topic == topic:
            raise ValueError(f"MQTT topic already in use: {topic}")


def _allocate_unit_id(config: AppConfig, requested: int | None) -> int:
    if requested is not None:
        return requested
    used = {
        device.protocol_config.unit_id
        for device in config.devices
        if isinstance(device.protocol_config, ModbusDeviceProtocolConfig)
    }
    for unit_id in range(_MIN_MODBUS_UNIT_ID, _MAX_MODBUS_UNIT_ID + 1):
        if unit_id not in used:
            return unit_id
    raise ValueError("Modbus unit_id space exhausted (1-247)")


def _modbus_width(signal: SignalTemplate) -> int:
    if signal.table in {ModbusTable.COIL, ModbusTable.DISCRETE_INPUT}:
        return 1
    if signal.wire_type is None:
        raise ValueError(f"{signal.id} is missing Modbus address metadata")
    return _WIRE_REGISTER_LENGTH[signal.wire_type]


def _pack_addresses(device_type: DeviceType, address_base: int) -> dict[str, int]:
    cursors: dict[ModbusTable, int] = {}
    packed: dict[str, int] = {}
    for signal in device_type.signals:
        if signal.table is None:
            raise ValueError(f"{signal.id} is missing Modbus address metadata")
        start = cursors.get(signal.table, address_base)
        packed[signal.id] = start
        cursors[signal.table] = start + _modbus_width(signal)
    return packed


def _resolve_addresses(
    device_type: DeviceType,
    address_base: int | None,
    addresses: dict[str, int] | None,
) -> dict[str, int]:
    if addresses is None:
        return _pack_addresses(device_type, 0 if address_base is None else address_base)
    expected = [signal.id for signal in device_type.signals]
    unknown = [key for key in addresses if key not in expected]
    if unknown:
        raise ValueError(f"unknown signal in addresses: {unknown[0]}")
    missing = [signal_id for signal_id in expected if signal_id not in addresses]
    if missing:
        raise ValueError(f"addresses must include signal: {missing[0]}")
    return dict(addresses)


def _assemble_device(
    config: AppConfig,
    device_type: DeviceType,
    request: DeviceFromTypeRequest,
    device_id: str,
    name: str,
) -> DeviceConfig:
    node_ids = request.node_ids or {}
    if device_type.protocol is ProtocolType.OPCUA:
        known = {signal.id for signal in device_type.signals}
        unknown = [key for key in node_ids if key not in known]
        if unknown:
            raise ValueError(f"unknown signal in node_ids: {unknown[0]}")
    addresses: dict[str, int] = {}
    if device_type.protocol is ProtocolType.MODBUS:
        addresses = _resolve_addresses(device_type, request.address_base, request.addresses)
    protocol_config = _protocol_config(config, device_type, request, device_id)
    signals = [
        _signal(device_type.protocol, signal, device_id, node_ids, addresses)
        for signal in device_type.signals
    ]
    return DeviceConfig(
        id=device_id,
        name=name,
        enabled=request.enabled,
        desired_state=request.desired_state,
        default_interval_ms=device_type.default_interval_ms,
        protocol=device_type.protocol,
        protocol_config=protocol_config,
        signals=signals,
    )


def _protocol_config(
    config: AppConfig,
    device_type: DeviceType,
    request: DeviceFromTypeRequest,
    device_id: str,
) -> OpcUaDeviceProtocolConfig | MqttDeviceProtocolConfig | ModbusDeviceProtocolConfig:
    if device_type.protocol is ProtocolType.MQTT:
        if request.topic is None:
            topic = _auto_topic(device_type, device_id)
            _ensure_topic_available(config, topic)
        else:
            topic = request.topic
        return MqttDeviceProtocolConfig(
            connection_id=device_type.connection_id if request.connection_id is None else request.connection_id,
            topic=topic,
            qos=device_type.qos if request.qos is None else request.qos,
            retain=device_type.retain if request.retain is None else request.retain,
            publish_interval_ms=(
                device_type.publish_interval_ms
                if request.publish_interval_ms is None
                else request.publish_interval_ms
            ),
            payload_mode=device_type.payload_mode if request.payload_mode is None else request.payload_mode,
        )
    if device_type.protocol is ProtocolType.OPCUA:
        return OpcUaDeviceProtocolConfig()
    return ModbusDeviceProtocolConfig(unit_id=_allocate_unit_id(config, request.unit_id))


def _signal(
    protocol: ProtocolType,
    template: SignalTemplate,
    device_id: str,
    node_ids: dict[str, str],
    addresses: dict[str, int],
) -> SignalConfig:
    if protocol is ProtocolType.MQTT:
        if template.field_name is None:
            raise ValueError(f"{template.id} is missing a MQTT field name")
        binding: OpcUaSignalMapping | MqttSignalMapping | ModbusSignalMapping = MqttSignalMapping(
            field_name=template.field_name
        )
    elif protocol is ProtocolType.OPCUA:
        binding = OpcUaSignalMapping(
            node_id=node_ids.get(template.id, f"{device_id}.{template.id}"),
            browse_name=template.browse_name,
            display_name=template.display_name,
        )
    else:
        if template.table is None or template.wire_type is None:
            raise ValueError(f"{template.id} is missing Modbus address metadata")
        binding = ModbusSignalMapping(
            table=template.table,
            address=addresses[template.id],
            wire_type=template.wire_type,
            scale=1.0,
            offset=0.0,
            byte_order=ByteOrder.BIG,
            word_order=WordOrder.BIG,
        )
    return SignalConfig(
        id=template.id,
        name=template.name,
        unit=template.unit,
        value_type=template.value_type,
        interval_ms=None,
        generator=template.generator.model_copy(deep=True),
        binding=binding,
    )


def _reject_runtime_node_ids(config: AppConfig, device: DeviceConfig) -> None:
    if device.protocol is not ProtocolType.OPCUA:
        return
    reserved = _runtime_node_ids([*config.devices, device])
    for signal in device.signals:
        mapping = signal.mapping
        if isinstance(mapping, OpcUaSignalMapping) and mapping.node_id in reserved:
            raise ValueError(f"OPC UA node_id {mapping.node_id} collides with a runtime node")


def _runtime_node_ids(devices: list[DeviceConfig]) -> set[str]:
    reserved = {_OPCUA_DEVICES_FOLDER}
    for device in devices:
        if device.protocol is not ProtocolType.OPCUA:
            continue
        reserved.add(f"device:{device.id}")
        for signal in device.signals:
            mapping = signal.mapping
            if signal.unit and isinstance(mapping, OpcUaSignalMapping):
                reserved.add(f"{mapping.node_id}.Unit")
    return reserved


def _ensure_candidate_accepts(config: AppConfig, device: DeviceConfig) -> None:
    payload = config.model_dump(mode="python")
    payload["devices"] = [*payload["devices"], device.model_dump(mode="python")]
    AppConfig.model_validate(payload)
