from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from iot_sim.device_types import (
    CATALOG_VERSION,
    DEVICE_TYPES,
    DeviceFromTypeRequest,
    build_device_from_type,
    describe_device_type,
    suggest,
)
from iot_sim.models import (
    AppConfig,
    DeviceConfig,
    IncrementGeneratorConfig,
    ModbusDeviceProtocolConfig,
    ModbusSignalMapping,
    MqttDeviceProtocolConfig,
    MqttSignalMapping,
    OpcUaSignalMapping,
    ProtocolType,
    RandomRangeGeneratorConfig,
)


def _request(type_id: str, **overrides) -> DeviceFromTypeRequest:
    return DeviceFromTypeRequest(type_id=type_id, **overrides)


def _with_devices(config: AppConfig, *devices: DeviceConfig) -> AppConfig:
    payload = config.model_dump(mode="python")
    payload["devices"] = [
        *payload["devices"],
        *[device.model_dump(mode="python") for device in devices],
    ]
    return AppConfig.model_validate(payload)


def _type(type_id: str):
    return next(item for item in DEVICE_TYPES if item.id == type_id)


def _mqtt(device_id: str, topic: str) -> dict:
    return {
        "id": device_id,
        "name": device_id,
        "protocol": "mqtt",
        "protocol_config": {"topic": topic},
        "signals": [],
    }


def _modbus(device_id: str, unit_id: int) -> dict:
    return {
        "id": device_id,
        "name": device_id,
        "protocol": "modbus",
        "protocol_config": {"unit_id": unit_id},
        "signals": [],
    }


def _opcua(device_id: str, node_id: str, unit: str | None = "°C") -> dict:
    return {
        "id": device_id,
        "name": device_id,
        "protocol": "opcua",
        "protocol_config": {},
        "signals": [{
            "id": "temperature",
            "name": "温度",
            "unit": unit,
            "value_type": "float32",
            "generator": {"type": "random_range", "min": 0, "max": 10, "precision": 1},
            "binding": {
                "type": "opcua",
                "node_id": node_id,
                "browse_name": "Temperature",
                "display_name": "Temperature",
            },
        }],
    }


def _raise_value(message: str, func) -> None:
    with pytest.raises(ValueError) as caught:
        func()
    assert type(caught.value) is ValueError
    assert str(caught.value) == message


def test_catalog_version_and_order() -> None:
    assert CATALOG_VERSION == 1
    assert [item.id for item in DEVICE_TYPES] == [
        "opcua.generic",
        "mqtt.counter",
        "modbus.pressure",
        "mqtt.temperature",
        "mqtt.power_meter",
        "mqtt.tank",
    ]


def test_six_types_validate_on_an_empty_configuration() -> None:
    config = AppConfig()
    for device_type in DEVICE_TYPES:
        device = build_device_from_type(config, _request(device_type.id))
        assert device.protocol is device_type.protocol
        assert device.enabled is True
        assert device.desired_state == "running"
        assert device.default_interval_ms == 1000
        assert all(signal.interval_ms is None for signal in device.signals)
        AppConfig(devices=[device])


def test_increment_steps_and_mqtt_field_names() -> None:
    config = AppConfig()
    counter = build_device_from_type(config, _request("mqtt.counter")).signals[0]
    energy = next(
        signal for signal in build_device_from_type(config, _request("mqtt.power_meter")).signals
        if signal.id == "energy"
    )
    level = next(
        signal for signal in build_device_from_type(config, _request("mqtt.tank")).signals
        if signal.id == "level"
    )
    for signal, initial, low, high, step_min, step_max in (
        (counter, 0, 0, 10000, 1, 3),
        (energy, 1000, 1000, 9999, 0.1, 0.5),
        (level, 20, 0, 100, 0.5, 2),
    ):
        generator = signal.generator
        assert isinstance(generator, IncrementGeneratorConfig)
        assert generator.initial_value == initial
        assert generator.value_min == low
        assert generator.value_max == high
        assert generator.step_min == step_min
        assert generator.step_max == step_max
        assert generator.boundary.value == "wrap"
        assert isinstance(signal.binding, MqttSignalMapping)
        dumped = signal.binding.model_dump(mode="json")
        assert "field_name" in dumped
        assert "json_field" not in dumped

    assert counter.binding.field_name == "counter"
    assert energy.binding.field_name == "energy"
    assert level.binding.field_name == "level"
    assert energy.value_type.value == "float64"
    assert level.value_type.value == "float32"


def test_template_shapes_match_the_inventory_samples() -> None:
    temperature = build_device_from_type(AppConfig(), _request("mqtt.temperature"))
    assert temperature.protocol_config.qos == 1
    assert temperature.protocol_config.retain is False
    assert temperature.protocol_config.payload_mode == "nested"
    assert temperature.protocol_config.publish_interval_ms == 1000
    assert temperature.protocol_config.topic == "iot-simulator/test/mqtt-temp-001/telemetry"
    assert [signal.unit for signal in temperature.signals] == ["C", "%"]
    humidity = temperature.signals[1].generator
    assert isinstance(humidity, RandomRangeGeneratorConfig)
    assert (humidity.min, humidity.max, humidity.precision) == (35, 85, 0)

    power = build_device_from_type(AppConfig(), _request("mqtt.power_meter"))
    assert power.protocol_config.payload_mode == "flat"
    assert power.protocol_config.publish_interval_ms == 2000

    tank = build_device_from_type(AppConfig(), _request("mqtt.tank"))
    assert tank.protocol_config.payload_mode == "values"
    assert tank.protocol_config.qos == 0

    opcua = build_device_from_type(AppConfig(), _request("opcua.generic"))
    mapping = opcua.signals[0].binding
    assert isinstance(mapping, OpcUaSignalMapping)
    assert mapping.node_id == "opcua-001.temperature"
    assert mapping.browse_name == "Temperature"
    assert mapping.display_name == "Temperature"
    assert mapping.data_type is None
    assert opcua.protocol_config.object_name is None
    assert opcua.signals[0].unit == "°C"

    pressure = build_device_from_type(AppConfig(), _request("modbus.pressure"))
    assert pressure.protocol_config.unit_id == 1
    modbus_mapping = pressure.signals[0].binding
    assert isinstance(modbus_mapping, ModbusSignalMapping)
    assert modbus_mapping.table.value == "input_register"
    assert modbus_mapping.address == 0
    assert modbus_mapping.wire_type.value == "float32"
    assert modbus_mapping.register_length == 2
    assert modbus_mapping.signed is None
    assert modbus_mapping.scale == 1.0
    assert modbus_mapping.offset == 0.0
    assert modbus_mapping.byte_order.value == "big"
    assert modbus_mapping.word_order.value == "big"


def test_successive_builds_fill_exact_ids_and_restart_modbus_addresses() -> None:
    config = AppConfig()
    first = build_device_from_type(config, _request("modbus.pressure"))
    second = build_device_from_type(_with_devices(config, first), _request("modbus.pressure"))
    assert (first.id, second.id) == ("modbus-001", "modbus-002")
    assert (first.protocol_config.unit_id, second.protocol_config.unit_id) == (1, 2)
    assert first.signals[0].binding.address == 0
    assert second.signals[0].binding.address == 0

    occupied = _with_devices(
        config,
        build_device_from_type(config, _request("mqtt.counter", device_id="mqtt-001")),
        build_device_from_type(config, _request("mqtt.counter", device_id="mqtt-003")),
    )
    assert build_device_from_type(occupied, _request("mqtt.counter")).id == "mqtt-002"


def test_unpadded_and_other_prefixes_do_not_consume_a_slot() -> None:
    config = AppConfig.model_validate({"devices": [
        _mqtt("mqtt-1", "devices/mqtt-1/telemetry"),
        _mqtt("mqtt-temp-001", "iot-simulator/test/mqtt-temp-001/telemetry"),
    ]})
    suggestion = suggest(config, _type("mqtt.counter"))
    device = build_device_from_type(config, _request("mqtt.counter"))
    assert suggestion["device_id"] == device.id == "mqtt-001"
    assert suggestion["allocatable"] is True
    assert device.name == "MQTT 计数设备 001"


def test_explicit_unpadded_id_keeps_its_own_name() -> None:
    device = build_device_from_type(
        AppConfig(), _request("mqtt.counter", device_id="mqtt-1")
    )
    assert device.id == "mqtt-1"
    assert device.name == "MQTT 计数设备 mqtt-1"

    numbered = build_device_from_type(
        AppConfig(), _request("mqtt.counter", device_id="mqtt-1000")
    )
    assert numbered.name == "MQTT 计数设备 1000"


def test_operator_name_and_run_state_override_the_template() -> None:
    device = build_device_from_type(
        AppConfig(),
        _request("opcua.generic", name="自定义", enabled=False, desired_state="stopped"),
    )
    assert device.name == "自定义"
    assert device.enabled is False
    assert device.desired_state == "stopped"


def test_unit_and_id_exhaustion_are_value_errors_but_suggest_does_not_raise() -> None:
    full_units = AppConfig.model_validate({
        "devices": [_modbus(f"unit-{unit}", unit) for unit in range(1, 248)]
    })
    message = "Modbus unit_id space exhausted (1-247)"
    _raise_value(message, lambda: build_device_from_type(full_units, _request("modbus.pressure")))
    suggestion = suggest(full_units, _type("modbus.pressure"))
    assert suggestion["allocatable"] is False
    assert suggestion["error"] == message
    assert suggestion["device_id"] == "modbus-001"
    assert suggestion["name"] == "Modbus 压力设备 001"
    assert suggestion["unit_id"] is None
    assert suggestion["addresses"] == {"pressure": 0}
    for device_type in DEVICE_TYPES:
        if device_type.id == "modbus.pressure":
            continue
        other = suggest(full_units, device_type)
        assert other["allocatable"] is True
        assert other["error"] is None

    shells = [
        DeviceConfig.model_construct(
            id=f"mqtt-{number:03d}",
            name="x",
            protocol=ProtocolType.MQTT,
            protocol_config=MqttDeviceProtocolConfig.model_construct(
                connection_id="default",
                topic=f"taken/{number}",
                qos=0,
                retain=False,
                payload_mode="nested",
            ),
            signals=[],
        )
        for number in range(1, 10000)
    ]
    exhausted = AppConfig.model_construct(devices=shells)
    id_message = "device id space exhausted for prefix mqtt"
    _raise_value(id_message, lambda: build_device_from_type(exhausted, _request("mqtt.counter")))
    exhausted_suggestion = suggest(exhausted, _type("mqtt.counter"))
    assert exhausted_suggestion == {
        "device_id": None,
        "name": None,
        "topic": None,
        "node_ids": {},
        "unit_id": None,
        "addresses": {},
        "allocatable": False,
        "error": id_message,
    }
    assert suggest(exhausted, _type("mqtt.temperature"))["device_id"] == "mqtt-temp-001"


def test_auto_topic_conflict_fails_and_an_explicit_shared_topic_is_kept() -> None:
    config = AppConfig.model_validate({"devices": [
        _mqtt("legacy", "devices/mqtt-001/telemetry"),
    ]})
    message = "MQTT topic already in use: devices/mqtt-001/telemetry"
    _raise_value(message, lambda: build_device_from_type(config, _request("mqtt.counter")))
    suggestion = suggest(config, _type("mqtt.counter"))
    assert suggestion["allocatable"] is False
    assert suggestion["error"] == message
    assert suggestion["device_id"] == "mqtt-001"
    assert suggestion["topic"] == "devices/mqtt-001/telemetry"

    shared = build_device_from_type(
        config,
        _request("mqtt.counter", device_id="mqtt-009", topic="devices/mqtt-001/telemetry"),
    )
    assert shared.protocol_config.topic == "devices/mqtt-001/telemetry"


def test_duplicate_configured_node_stays_a_validation_error() -> None:
    config = AppConfig.model_validate({"devices": [
        _opcua("plant", "opcua-009.temperature"),
    ]})
    with pytest.raises(ValidationError, match="duplicate OPC UA node_id opcua-009.temperature"):
        build_device_from_type(config, _request("opcua.generic", device_id="opcua-009"))


def test_runtime_node_overrides_fail_before_the_candidate_is_returned() -> None:
    _raise_value(
        "OPC UA node_id Devices collides with a runtime node",
        lambda: build_device_from_type(
            AppConfig(),
            _request("opcua.generic", node_ids={"temperature": "Devices"}),
        ),
    )
    _raise_value(
        "OPC UA node_id device:opcua-009 collides with a runtime node",
        lambda: build_device_from_type(
            AppConfig(),
            _request(
                "opcua.generic",
                device_id="opcua-009",
                node_ids={"temperature": "device:opcua-009"},
            ),
        ),
    )
    existing = AppConfig.model_validate({"devices": [
        _opcua("other", "plant.temperature"),
    ]})
    _raise_value(
        "OPC UA node_id device:other collides with a runtime node",
        lambda: build_device_from_type(
            existing,
            _request("opcua.generic", node_ids={"temperature": "device:other"}),
        ),
    )
    _raise_value(
        "OPC UA node_id plant.temperature.Unit collides with a runtime node",
        lambda: build_device_from_type(
            existing,
            _request("opcua.generic", node_ids={"temperature": "plant.temperature.Unit"}),
        ),
    )


def test_duplicate_unit_id_stays_a_validation_error() -> None:
    config = AppConfig.model_validate({"devices": [_modbus("taken", 2)]})
    with pytest.raises(ValidationError, match="duplicate Modbus unit_id 2"):
        build_device_from_type(
            config, _request("modbus.pressure", device_id="modbus-009", unit_id=2)
        )
    device = build_device_from_type(config, _request("modbus.pressure"))
    assert device.protocol_config.unit_id == 1


def test_modbus_address_overrides() -> None:
    moved = build_device_from_type(
        AppConfig(), _request("modbus.pressure", unit_id=2, address_base=10)
    )
    assert moved.protocol_config.unit_id == 2
    assert moved.signals[0].binding.address == 10
    explicit = build_device_from_type(
        AppConfig(), _request("modbus.pressure", addresses={"pressure": 20})
    )
    assert explicit.signals[0].binding.address == 20
    _raise_value(
        "address_base and addresses are mutually exclusive",
        lambda: build_device_from_type(
            AppConfig(),
            _request("modbus.pressure", address_base=10, addresses={"pressure": 4}),
        ),
    )
    _raise_value(
        "unknown signal in addresses: other",
        lambda: build_device_from_type(
            AppConfig(),
            _request("modbus.pressure", addresses={"pressure": 0, "other": 4}),
        ),
    )
    _raise_value(
        "addresses must include signal: pressure",
        lambda: build_device_from_type(AppConfig(), _request("modbus.pressure", addresses={})),
    )


def test_unknown_type_and_fields_from_another_protocol() -> None:
    _raise_value(
        "unknown device type: missing.type",
        lambda: build_device_from_type(AppConfig(), _request("missing.type")),
    )
    _raise_value(
        "unit_id does not apply to protocol opcua",
        lambda: build_device_from_type(AppConfig(), _request("opcua.generic", unit_id=2)),
    )
    _raise_value(
        "topic does not apply to protocol modbus",
        lambda: build_device_from_type(AppConfig(), _request("modbus.pressure", topic="a/b")),
    )
    _raise_value(
        "node_ids does not apply to protocol mqtt",
        lambda: build_device_from_type(
            AppConfig(), _request("mqtt.counter", node_ids={"counter": "x"})
        ),
    )
    _raise_value(
        "unknown signal in node_ids: missing",
        lambda: build_device_from_type(
            AppConfig(), _request("opcua.generic", node_ids={"missing": "x"})
        ),
    )
    _raise_value(
        "device already exists: opcua-001",
        lambda: build_device_from_type(
            _with_devices(AppConfig(), build_device_from_type(AppConfig(), _request("opcua.generic"))),
            _request("opcua.generic", device_id="opcua-001"),
        ),
    )


def test_request_constraints_reject_unknown_and_out_of_range_fields() -> None:
    with pytest.raises(ValidationError):
        DeviceFromTypeRequest(type_id="modbus.pressure", unit_id=0)
    with pytest.raises(ValidationError):
        DeviceFromTypeRequest(type_id="mqtt.counter", publish_interval_ms=999)
    with pytest.raises(ValidationError):
        DeviceFromTypeRequest(type_id="mqtt.counter", unexpected=True)


def test_mqtt_overrides_do_not_clear_a_template_interval_when_omitted() -> None:
    device = build_device_from_type(
        AppConfig(),
        _request(
            "mqtt.power_meter",
            connection_id="default",
            qos=2,
            retain=True,
            payload_mode="nested",
            publish_interval_ms=3000,
        ),
    )
    assert device.protocol_config.connection_id == "default"
    assert device.protocol_config.qos == 2
    assert device.protocol_config.retain is True
    assert device.protocol_config.payload_mode == "nested"
    assert device.protocol_config.publish_interval_ms == 3000

    omitted = build_device_from_type(AppConfig(), _request("mqtt.power_meter"))
    assert omitted.protocol_config.publish_interval_ms == 2000
    counter = build_device_from_type(AppConfig(), _request("mqtt.counter"))
    assert counter.protocol_config.publish_interval_ms is None


def test_disabled_connection_is_a_validation_error_and_suggest_still_allocates() -> None:
    config = AppConfig.model_validate({"mqtt": {"enabled": False}})
    suggestion = suggest(config, _type("mqtt.counter"))
    assert suggestion["allocatable"] is True
    assert suggestion["topic"] == "devices/mqtt-001/telemetry"
    with pytest.raises(ValidationError, match="disabled connection_id"):
        build_device_from_type(config, _request("mqtt.counter"))


def test_expanded_generators_are_deep_copies() -> None:
    opcua_template = _type("opcua.generic").signals[0].generator
    counter_template = _type("mqtt.counter").signals[0].generator
    assert isinstance(opcua_template, RandomRangeGeneratorConfig)
    assert isinstance(counter_template, IncrementGeneratorConfig)
    opcua = build_device_from_type(AppConfig(), _request("opcua.generic"))
    counter = build_device_from_type(AppConfig(), _request("mqtt.counter"))
    expanded_range = opcua.signals[0].generator
    expanded_increment = counter.signals[0].generator
    assert expanded_range is not opcua_template
    assert expanded_increment is not counter_template
    assert isinstance(expanded_range, RandomRangeGeneratorConfig)
    assert isinstance(expanded_increment, IncrementGeneratorConfig)
    expanded_range.min = 0
    expanded_increment.step_max = 2
    assert opcua_template.min == 18
    assert counter_template.step_max == 3
    again = build_device_from_type(AppConfig(), _request("opcua.generic"))
    assert again.signals[0].generator.min == 18


def test_suggestions_are_deterministic_and_protocol_fields_stay_present() -> None:
    config = AppConfig()
    expected = {
        "opcua.generic": {
            "device_id": "opcua-001",
            "name": "OPC UA 示例设备 001",
            "topic": None,
            "node_ids": {"temperature": "opcua-001.temperature"},
            "unit_id": None,
            "addresses": {},
            "allocatable": True,
            "error": None,
        },
        "mqtt.counter": {
            "device_id": "mqtt-001",
            "name": "MQTT 计数设备 001",
            "topic": "devices/mqtt-001/telemetry",
            "node_ids": {},
            "unit_id": None,
            "addresses": {},
            "allocatable": True,
            "error": None,
        },
        "modbus.pressure": {
            "device_id": "modbus-001",
            "name": "Modbus 压力设备 001",
            "topic": None,
            "node_ids": {},
            "unit_id": 1,
            "addresses": {"pressure": 0},
            "allocatable": True,
            "error": None,
        },
    }
    for type_id, suggestion in expected.items():
        device_type = _type(type_id)
        assert suggest(config, device_type) == suggestion
        assert suggest(config, device_type) == suggestion


def test_catalog_source_and_public_description_omit_credentials() -> None:
    source = (Path(__file__).resolve().parents[1] / "iot_sim" / "device_types.py").read_text(
        encoding="utf-8"
    )
    public = [
        {**describe_device_type(device_type), "suggestion": suggest(AppConfig(), device_type)}
        for device_type in DEVICE_TYPES
    ]
    rendered = source + json.dumps(public, ensure_ascii=False)
    for word in ("password", "password_env", "secret", "token"):
        assert word not in rendered
    temperature = next(item for item in public if item["id"] == "mqtt.temperature")
    assert temperature["topic_pattern"] == "iot-simulator/test/{device_id}/telemetry"
    assert temperature["defaults"] == {
        "connection_id": "default",
        "qos": 1,
        "retain": False,
        "payload_mode": "nested",
        "publish_interval_ms": 1000,
    }
    assert describe_device_type(_type("opcua.generic"))["defaults"] == {}
    assert describe_device_type(_type("modbus.pressure"))["defaults"] == {}
    assert describe_device_type(_type("mqtt.counter"))["defaults"]["publish_interval_ms"] is None


def test_six_types_can_be_added_to_one_configuration() -> None:
    config = AppConfig()
    for device_type in DEVICE_TYPES:
        device = build_device_from_type(config, _request(device_type.id))
        config = _with_devices(config, device)
    assert [device.id for device in config.devices] == [
        "opcua-001",
        "mqtt-001",
        "modbus-001",
        "mqtt-temp-001",
        "mqtt-power-001",
        "mqtt-tank-001",
    ]
    assert isinstance(config.devices[2].protocol_config, ModbusDeviceProtocolConfig)
    assert config.devices[2].protocol_config.unit_id == 1
