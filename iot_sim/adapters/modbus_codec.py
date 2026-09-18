"""Pure Modbus register codec with explicit byte/word order and scaling."""

from __future__ import annotations

import math
import struct
from typing import Any, Sequence


_FORMATS: dict[str, str] = {
    "int16": "h",
    "uint16": "H",
    "int32": "i",
    "uint32": "I",
    "float32": "f",
    "float64": "d",
}


def register_count(wire_type: str) -> int:
    normalized = wire_type.lower()
    if normalized == "bool":
        return 1
    try:
        return struct.calcsize(_FORMATS[normalized]) // 2
    except KeyError as exc:
        raise ValueError(f"unsupported Modbus wire type: {wire_type}") from exc


def _orders(byte_order: str, word_order: str) -> tuple[str, str]:
    byte_order = byte_order.lower()
    word_order = word_order.lower()
    if byte_order not in {"big", "little"}:
        raise ValueError("byte_order must be 'big' or 'little'")
    if word_order not in {"big", "little"}:
        raise ValueError("word_order must be 'big' or 'little'")
    return byte_order, word_order


def _reorder(data: bytes, byte_order: str, word_order: str) -> bytes:
    words = [data[i : i + 2] for i in range(0, len(data), 2)]
    if byte_order == "little":
        words = [word[::-1] for word in words]
    if word_order == "little":
        words.reverse()
    return b"".join(words)


def encode_modbus_value(
    value: Any,
    wire_type: str,
    *,
    byte_order: str = "big",
    word_order: str = "big",
    scale: float = 1.0,
    offset: float = 0.0,
) -> list[int]:
    """Encode engineering value as unsigned 16-bit registers.

    Scaling follows ``engineering = raw * scale + offset``.
    """
    normalized = wire_type.lower()
    byte_order, word_order = _orders(byte_order, word_order)
    if normalized == "bool":
        return [1 if bool(value) else 0]
    if scale == 0:
        raise ValueError("scale cannot be zero")
    try:
        fmt = _FORMATS[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported Modbus wire type: {wire_type}") from exc
    raw = (float(value) - float(offset)) / float(scale)
    if fmt in {"h", "H", "i", "I"}:
        raw = round(raw)
    elif not math.isfinite(raw):
        raise ValueError("floating-point Modbus value must be finite")
    try:
        canonical = struct.pack(">" + fmt, raw)
    except (struct.error, OverflowError) as exc:
        raise ValueError(f"value {value!r} is out of range for {wire_type}") from exc
    data = _reorder(canonical, byte_order, word_order)
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


def decode_modbus_value(
    registers: Sequence[int],
    wire_type: str,
    *,
    byte_order: str = "big",
    word_order: str = "big",
    scale: float = 1.0,
    offset: float = 0.0,
) -> bool | int | float:
    normalized = wire_type.lower()
    byte_order, word_order = _orders(byte_order, word_order)
    expected = register_count(normalized)
    if len(registers) != expected:
        raise ValueError(f"{wire_type} requires {expected} register(s), got {len(registers)}")
    if any(not isinstance(register, int) or not 0 <= register <= 0xFFFF for register in registers):
        raise ValueError("register values must be integers between 0 and 65535")
    if normalized == "bool":
        return bool(registers[0])
    data = b"".join(register.to_bytes(2, "big") for register in registers)
    # Applying the same byte/word swaps again reverses the wire transformation.
    canonical = _reorder(data, byte_order, word_order)
    raw = struct.unpack(">" + _FORMATS[normalized], canonical)[0]
    engineering = raw * float(scale) + float(offset)
    return int(engineering) if normalized.startswith(("int", "uint")) and scale == 1 and offset == 0 else engineering

