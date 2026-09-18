"""Deterministic signal value generators."""

from __future__ import annotations

import hashlib
import random
from typing import Protocol

from .models import (
    BoundaryMode,
    IncrementGeneratorConfig,
    RandomRangeGeneratorConfig,
    SignalConfig,
    ValueType,
)


INTEGER_TYPES = {
    ValueType.BOOL,
    ValueType.INT16,
    ValueType.UINT16,
    ValueType.INT32,
    ValueType.UINT32,
}


def derive_signal_seed(global_seed: int, device_id: str, signal_id: str) -> int:
    """Derive a stable seed without relying on process-randomized ``hash``."""

    material = f"{global_seed}\0{device_id}\0{signal_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


class Generator(Protocol):
    def next_value(self) -> bool | int | float: ...


class IncrementGenerator:
    def __init__(
        self,
        config: IncrementGeneratorConfig,
        value_type: ValueType,
        seed: int,
    ) -> None:
        self.config = config
        self.value_type = value_type
        self.random = random.Random(seed)
        self.current: int | float = self._coerce(config.initial_value)

    def _coerce(self, value: int | float) -> int | float:
        if self.value_type in INTEGER_TYPES:
            return int(value)
        return float(value)

    def next_value(self) -> int | float:
        if self.value_type in INTEGER_TYPES:
            step = self.random.randint(int(self.config.step_min), int(self.config.step_max))
        else:
            step = self.random.uniform(
                float(self.config.step_min), float(self.config.step_max)
            )
        value = self.current + step
        low = self.config.value_min
        high = self.config.value_max
        if self.config.boundary is BoundaryMode.CLAMP:
            value = min(high, max(low, value))
        else:
            width = high - low
            if value > high or value < low:
                value = low + ((value - low) % width)
        self.current = self._coerce(value)
        return self.current


class RandomRangeGenerator:
    def __init__(
        self,
        config: RandomRangeGeneratorConfig,
        value_type: ValueType,
        seed: int,
    ) -> None:
        self.config = config
        self.value_type = value_type
        self.random = random.Random(seed)

    def next_value(self) -> bool | int | float:
        if self.value_type is ValueType.BOOL:
            return bool(self.random.randint(int(self.config.min), int(self.config.max)))
        if self.value_type in INTEGER_TYPES:
            return self.random.randint(int(self.config.min), int(self.config.max))
        value = self.random.uniform(float(self.config.min), float(self.config.max))
        rounded = round(value, self.config.precision)
        return min(float(self.config.max), max(float(self.config.min), rounded))


def create_generator(
    signal: SignalConfig, global_seed: int, device_id: str
) -> IncrementGenerator | RandomRangeGenerator:
    seed = derive_signal_seed(global_seed, device_id, signal.id)
    if isinstance(signal.generator, IncrementGeneratorConfig):
        return IncrementGenerator(signal.generator, signal.value_type, seed)
    return RandomRangeGenerator(signal.generator, signal.value_type, seed)
