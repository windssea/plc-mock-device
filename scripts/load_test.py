"""Long-running scheduler acceptance test (defaults to the planned 30 minutes)."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import tempfile
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path

import yaml

from iot_sim.config_store import ConfigStore
from iot_sim.models import AppConfig, BootstrapConfig, ProtocolType
from iot_sim.runtime import RuntimeManager


class ProbeAdapter:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str], list[float]] = defaultdict(list)

    async def start(self, *_args, **_kwargs) -> None: pass
    async def stop(self) -> None: pass
    async def reconfigure(self, *_args, **_kwargs) -> None: pass

    async def update(self, sample) -> None:
        self.events[(sample.device_id, sample.signal_id)].append(time.monotonic())

    def set_device_active(self, *_args) -> None: pass

    def status_dict(self) -> dict:
        return {
            "name": "probe", "running": True, "connected": True,
            "last_error": None, "last_transition_at": None, "dropped_messages": 0,
        }


def build_config(device_count: int, signals_per_device: int) -> dict:
    devices = []
    for device_index in range(device_count):
        device_id = f"load-{device_index:03d}"
        devices.append({
            "id": device_id,
            "name": device_id,
            "enabled": True,
            "desired_state": "running",
            "default_interval_ms": 1000,
            "protocol": "mqtt",
            "protocol_config": {"topic": f"load/{device_id}", "qos": 0, "retain": False},
            "signals": [{
                "id": f"point-{signal_index:02d}",
                "name": f"Point {signal_index}",
                "value_type": "uint32",
                "generator": {
                    "type": "increment", "initial_value": 0,
                    "value_min": 0, "value_max": 4_000_000_000,
                    "step_min": 1, "step_max": 1, "boundary": "wrap",
                },
                "binding": {"type": "mqtt", "field_name": f"point{signal_index}"},
            } for signal_index in range(signals_per_device)],
        })
    return {
        "version": 1, "config_revision": 0, "random_seed": 42,
        "web": {"host": "127.0.0.1", "port": 8080},
        "opcua": {"enabled": False}, "mqtt": {"enabled": False},
        "modbus": {"enabled": False}, "devices": devices,
    }


async def run(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / "load.yaml"
        database_path = Path(temp_dir) / "load.db"
        config_path.write_text(
            yaml.safe_dump(BootstrapConfig().model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
        ConfigStore(database_path).save_sync(
            AppConfig.model_validate(build_config(args.devices, args.signals)),
            expected_revision=0,
            increment_revision=False,
        )
        manager = RuntimeManager(str(config_path), str(database_path))
        probe = ProbeAdapter()
        manager.adapters[ProtocolType.MQTT] = probe  # type: ignore[assignment]
        tracemalloc.start()
        await manager.start()
        started = time.monotonic()
        midpoint_memory = 0
        while time.monotonic() - started < args.duration:
            await asyncio.sleep(min(0.5, args.duration / 2))
            if not midpoint_memory and time.monotonic() - started >= args.duration / 2:
                midpoint_memory = tracemalloc.get_traced_memory()[0]
        end_memory, peak_memory = tracemalloc.get_traced_memory()
        await manager.stop()
        tracemalloc.stop()

    drifts = []
    for timestamps in probe.events.values():
        drifts.extend(abs((right - left) - 1.0) for left, right in zip(timestamps, timestamps[1:]))
    p95 = statistics.quantiles(drifts, n=20)[18] if len(drifts) >= 20 else max(drifts, default=0.0)
    expected_points = args.devices * args.signals
    memory_growth = max(0, end_memory - midpoint_memory)
    print(
        f"points={len(probe.events)}/{expected_points} duration={args.duration:.1f}s "
        f"p95_drift_ms={p95 * 1000:.2f} memory_growth_mb={memory_growth / 1024 / 1024:.2f} "
        f"peak_mb={peak_memory / 1024 / 1024:.2f}"
    )
    return 0 if len(probe.events) == expected_points and p95 <= 0.1 and memory_growth <= 10 * 1024 * 1024 else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=1800)
    parser.add_argument("--devices", type=int, default=100)
    parser.add_argument("--signals", type=int, default=10)
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
