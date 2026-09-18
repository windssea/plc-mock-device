import math

import pytest

from iot_sim.adapters.modbus_codec import (
    decode_modbus_value,
    encode_modbus_value,
    register_count,
)


@pytest.mark.parametrize(
    ("wire_type", "value"),
    [
        ("int16", -1234),
        ("uint16", 65000),
        ("int32", -12345678),
        ("uint32", 4_000_000_000),
        ("float32", 12.5),
        ("float64", -123.456789),
    ],
)
@pytest.mark.parametrize("byte_order", ["big", "little"])
@pytest.mark.parametrize("word_order", ["big", "little"])
def test_round_trip_all_orders(wire_type, value, byte_order, word_order):
    registers = encode_modbus_value(
        value, wire_type, byte_order=byte_order, word_order=word_order
    )
    decoded = decode_modbus_value(
        registers, wire_type, byte_order=byte_order, word_order=word_order
    )
    if wire_type.startswith("float"):
        assert decoded == pytest.approx(value, rel=1e-6)
    else:
        assert decoded == value


def test_known_float32_register_orders():
    # IEEE754 1.0 is 3f 80 00 00 in canonical big-endian representation.
    assert encode_modbus_value(1.0, "float32") == [0x3F80, 0x0000]
    assert encode_modbus_value(1.0, "float32", byte_order="little") == [0x803F, 0x0000]
    assert encode_modbus_value(1.0, "float32", word_order="little") == [0x0000, 0x3F80]
    assert encode_modbus_value(
        1.0, "float32", byte_order="little", word_order="little"
    ) == [0x0000, 0x803F]


def test_scaling_and_offset():
    assert encode_modbus_value(25.0, "int16", scale=0.1, offset=-10.0) == [350]
    assert decode_modbus_value([350], "int16", scale=0.1, offset=-10.0) == pytest.approx(25.0)


def test_bool_codec():
    assert encode_modbus_value(True, "bool") == [1]
    assert decode_modbus_value([0], "bool") is False
    assert decode_modbus_value([1], "bool") is True


def test_register_counts():
    assert register_count("uint16") == 1
    assert register_count("uint32") == 2
    assert register_count("float64") == 4


@pytest.mark.parametrize(
    "call",
    [
        lambda: encode_modbus_value(1, "uint16", scale=0),
        lambda: encode_modbus_value(-1, "uint16"),
        lambda: encode_modbus_value(math.inf, "float32"),
        lambda: encode_modbus_value(1, "not-a-type"),
        lambda: decode_modbus_value([1], "uint32"),
        lambda: decode_modbus_value([65536], "uint16"),
        lambda: encode_modbus_value(1, "uint16", byte_order="middle"),
    ],
)
def test_invalid_codec_inputs(call):
    with pytest.raises(ValueError):
        call()
