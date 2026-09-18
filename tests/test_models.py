from __future__ import annotations

import pytest
from pydantic import ValidationError

from iot_sim.models import AppConfig


def mqtt_device(device_id: str = "d1") -> dict:
    return {
        "device_id": device_id,
        "name": device_id,
        "protocol": "mqtt",
        "protocol_config": {"topic": f"devices/{device_id}"},
        "signals": [
            {
                "signal_id": "temperature",
                "name": "Temperature",
                "value_type": "float32",
                "generator": {
                    "type": "random_range",
                    "min": -10,
                    "max": 50,
                    "precision": 1,
                },
                "binding": {"type": "mqtt", "field_name": "temperature"},
            }
        ],
    }


def test_discriminated_unions_and_json_dump() -> None:
    config = AppConfig(devices=[mqtt_device()])
    dumped = config.model_dump(mode="json")
    assert "type" not in dumped["devices"][0]["protocol_config"]
    assert dumped["devices"][0]["signals"][0]["generator"]["type"] == "random_range"
    assert dumped["devices"][0]["protocol"] == "mqtt"
    assert dumped["devices"][0]["id"] == "d1"
    assert dumped["devices"][0]["default_interval_ms"] == 1000
    assert dumped["devices"][0]["signals"][0]["id"] == "temperature"
    assert dumped["devices"][0]["signals"][0]["binding"]["field_name"] == "temperature"


def test_mqtt_named_connections_and_device_reference() -> None:
    device = mqtt_device()
    device["protocol_config"]["connection_id"] = "factory-b"
    config = AppConfig.model_validate({
        "mqtt": {"host": "localhost", "username": "mqtt", "password": "secret"},
        "mqtt_connections": [{
            "id": "factory-b", "name": "Factory B", "host": "10.0.0.2",
            "port": 1883, "client_id": "factory-b-gateway", "transport": "tcp",
            "tls_enabled": False,
        }],
        "devices": [device],
    })
    assert [item.id for item in config.mqtt_connections] == ["default", "factory-b"]
    assert config.mqtt_connections[0].password == "secret"
    assert config.devices[0].protocol_config.connection_id == "factory-b"

    device["protocol_config"]["connection_id"] = "missing"
    with pytest.raises(ValidationError, match="unknown connection_id"):
        AppConfig.model_validate({"devices": [device]})


def test_mqtt_password_sources_are_exclusive_and_disabled_reference_is_rejected() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        AppConfig.model_validate({
            "mqtt": {"password": "secret", "password_env": "MQTT_PASSWORD"}
        })

    device = mqtt_device()
    with pytest.raises(ValidationError, match="disabled connection_id default"):
        AppConfig.model_validate({
            "mqtt": {"enabled": False},
            "devices": [device],
        })


@pytest.mark.parametrize(
    "change",
    [
        lambda devices: devices.append(mqtt_device("d1")),
        lambda devices: devices[0]["protocol_config"].update(topic="devices/+/data"),
        lambda devices: devices[0]["signals"][0].update(interval_ms=999),
        lambda devices: devices[0]["signals"][0]["generator"].update(max=4e39),
    ],
)
def test_rejects_invalid_global_and_numeric_configuration(change) -> None:
    devices = [mqtt_device()]
    change(devices)
    with pytest.raises(ValidationError):
        AppConfig(devices=devices)


def test_rejects_duplicate_opcua_node_ids() -> None:
    signal = {
        "signal_id": "s1",
        "name": "S1",
        "value_type": "int16",
        "generator": {
            "type": "increment",
            "initial_value": 0,
            "value_min": 0,
            "value_max": 100,
        },
        "binding": {"type": "opcua", "node_id": "ns=2;s=shared"},
    }
    devices = [
        {
            "device_id": device_id,
            "name": device_id,
            "protocol": "opcua",
            "protocol_config": {},
            "signals": [signal | {"signal_id": f"s{index}"}],
        }
        for index, device_id in enumerate(("one", "two"), start=1)
    ]
    with pytest.raises(ValidationError, match="duplicate OPC UA node_id"):
        AppConfig(devices=devices)


def test_rejects_modbus_unit_and_address_collisions() -> None:
    def device(device_id: str, unit_id: int, addresses: list[int]) -> dict:
        return {
            "device_id": device_id,
            "name": device_id,
            "protocol": "modbus",
            "protocol_config": {"unit_id": unit_id},
            "signals": [
                {
                    "signal_id": f"s{address}",
                    "name": f"S{address}",
                    "value_type": "uint32",
                    "generator": {
                        "type": "random_range",
                        "min": 0,
                        "max": 100,
                    },
                    "binding": {
                        "type": "modbus",
                        "table": "holding_register",
                        "address": address,
                        "wire_type": "uint32",
                    },
                }
                for address in addresses
            ],
        }

    with pytest.raises(ValidationError, match="overlapping Modbus"):
        AppConfig(devices=[device("one", 1, [0, 1])])
    with pytest.raises(ValidationError, match="duplicate Modbus unit_id"):
        AppConfig(devices=[device("one", 1, [0]), device("two", 1, [10])])


def test_rejects_modbus_scaled_range_that_overflows_wire_type() -> None:
    device = {
        "id": "modbus-overflow",
        "name": "Overflow",
        "protocol": "modbus",
        "protocol_config": {"unit_id": 1},
        "signals": [{
            "id": "large",
            "name": "Large",
            "value_type": "uint32",
            "generator": {"type": "random_range", "min": 0, "max": 100_000},
            "binding": {
                "type": "modbus", "table": "input_register", "address": 0,
                "wire_type": "uint16", "scale": 1, "offset": 0,
            },
        }],
    }
    with pytest.raises(ValidationError, match="exceeds Modbus wire_type"):
        AppConfig(devices=[device])

    device["signals"][0]["binding"]["scale"] = 2
    AppConfig(devices=[device])
