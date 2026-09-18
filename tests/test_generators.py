from __future__ import annotations

from iot_sim.generators import create_generator, derive_signal_seed
from iot_sim.models import SignalConfig


def signal(generator: dict, value_type: str = "int16") -> SignalConfig:
    return SignalConfig.model_validate(
        {
            "signal_id": "value",
            "name": "Value",
            "value_type": value_type,
            "generator": generator,
            "binding": {"type": "mqtt", "field_name": "value"},
        }
    )


def test_stable_seed_and_independent_streams() -> None:
    config = signal({"type": "random_range", "min": 0, "max": 100})
    left = create_generator(config, 42, "device-1")
    right = create_generator(config, 42, "device-1")
    assert [left.next_value() for _ in range(10)] == [
        right.next_value() for _ in range(10)
    ]
    assert derive_signal_seed(42, "device-1", "value") != derive_signal_seed(
        42, "device-2", "value"
    )


def test_increment_wrap_preserves_overshoot() -> None:
    config = signal(
        {
            "type": "increment",
            "initial_value": 9,
            "value_min": 0,
            "value_max": 10,
            "step_min": 3,
            "step_max": 3,
            "boundary": "wrap",
        }
    )
    generator = create_generator(config, 1, "device")
    assert generator.next_value() == 2


def test_increment_clamps_at_boundary() -> None:
    config = signal(
        {
            "type": "increment",
            "initial_value": 9,
            "value_min": 0,
            "value_max": 10,
            "step_min": 3,
            "step_max": 3,
            "boundary": "clamp",
        }
    )
    generator = create_generator(config, 1, "device")
    assert generator.next_value() == 10
    assert generator.next_value() == 10


def test_float_random_precision_and_range() -> None:
    config = signal(
        {"type": "random_range", "min": 0.125, "max": 0.875, "precision": 3},
        "float32",
    )
    generator = create_generator(config, 123, "device")
    values = [generator.next_value() for _ in range(100)]
    assert all(0.125 <= value <= 0.875 for value in values)
    assert all(value == round(value, 3) for value in values)


def test_rounding_never_leaves_configured_range() -> None:
    config = signal(
        {"type": "random_range", "min": 0.125, "max": 0.126, "precision": 2},
        "float32",
    )
    generator = create_generator(config, 123, "device")
    assert all(0.125 <= generator.next_value() <= 0.126 for _ in range(100))
