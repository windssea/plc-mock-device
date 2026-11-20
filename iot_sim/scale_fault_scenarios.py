"""Offline scale & fault-injection scenario generators for Hub/Gateway single-gateway scale testing.

Covers Section 8 acceptance scenarios without connecting to live brokers, databases, or PLCs:
- Multi-meter device/status storm + interleaved gateway heartbeats (noisy-neighbor isolation).
- Equal-millisecond state conflict, out-of-order point revisions, and duplicate/backfill injection.
- BAD_* quality transition conservation streams (GOOD -> BAD_TIMEOUT -> BAD_DECODE -> GOOD).
- COLLECTION_OFF / re-enable dual-Outbox race & pre-enable cached backfill scenarios.
- Wall-clock rollback / step with v2 monotonic revision & clockRevision generation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import random


@dataclass(frozen=True)
class SimulatedEnvelope:
    topic: str
    payload: dict
    delivery_order_ms: int


class MultiMeterStormGenerator:
    """Generates deterministic multi-meter device/status floods interleaved with gateway heartbeats."""

    def __init__(self, *, noisy_gateway: str = "GW-NOISY-08", quiet_gateway: str = "NG-HUB-1252-01", seed: int = 20261120) -> None:
        self.noisy_gateway = noisy_gateway
        self.quiet_gateway = quiet_gateway
        self._rng = random.Random(seed)

    def build_storm(
        self,
        *,
        meter_count: int = 100,
        rounds: int = 10,
        start_ms: int = 1763600000000,
        heartbeat_every: int = 25,
    ) -> list[SimulatedEnvelope]:
        envelopes: list[SimulatedEnvelope] = []
        seq = 0
        for r in range(rounds):
            base_ts = start_ms + r * 1000
            for m in range(1, meter_count + 1):
                seq += 1
                status = "OFFLINE" if (m % 29 == 0 and r % 2 == 1) else ("DEGRADED" if m % 17 == 0 else "ONLINE")
                envelopes.append(
                    SimulatedEnvelope(
                        topic=f"iot/v1/gateway/{self.noisy_gateway}/device/status",
                        payload={
                            "schemaVersion": 1,
                            "messageType": "device_status",
                            "gatewayId": self.noisy_gateway,
                            "sessionId": "s-noisy-1",
                            "configVersion": 10,
                            "timestamp": base_ts + (m % 50),
                            "device": {
                                "deviceId": f"meter-{m:03d}",
                                "status": status,
                                "lastError": "BAD_TIMEOUT" if status == "OFFLINE" else None,
                                "lastGoodTimestamp": base_ts if status != "OFFLINE" else base_ts - 5000,
                            },
                        },
                        delivery_order_ms=base_ts + (m % 50),
                    )
                )
                if seq % heartbeat_every == 0:
                    envelopes.append(
                        SimulatedEnvelope(
                            topic=f"iot/v1/gateway/{self.quiet_gateway}/heartbeat",
                            payload={
                                "schemaVersion": 1,
                                "gatewayId": self.quiet_gateway,
                                "sessionId": "s-1252-1",
                                "timestamp": base_ts + (m % 50),
                                "agentVersion": "native-0.8.1",
                                "agentState": "RUNNING",
                                "activeConfigVersion": 3,
                            },
                            delivery_order_ms=base_ts + (m % 50),
                        )
                    )
        return envelopes


class FaultInjectionScenarioBuilder:
    """Builds deterministic fault-injection sequences matching Section 8 verification cases."""

    @staticmethod
    def equal_ms_conflict_and_out_of_order_points(
        *, gateway_id: str = "GW-TEST", device_id: str = "meter-001", ts_ms: int = 1763600000000
    ) -> dict[str, list[dict]]:
        # Equal-ms ONLINE vs OFFLINE -> DB/coalescer must keep OFFLINE (conservative priority).
        state_events = [
            {
                "gatewayId": gateway_id,
                "sessionId": "s1",
                "configVersion": 5,
                "timestamp": ts_ms,
                "device": {"deviceId": device_id, "status": "ONLINE", "lastError": None},
            },
            {
                "gatewayId": gateway_id,
                "sessionId": "s1",
                "configVersion": 5,
                "timestamp": ts_ms,
                "device": {"deviceId": device_id, "status": "OFFLINE", "lastError": "BAD_TIMEOUT"},
            },
            {
                "gatewayId": gateway_id,
                "sessionId": "s1",
                "configVersion": 5,
                "timestamp": ts_ms,
                "device": {"deviceId": device_id, "status": "ONLINE", "lastError": None},
            },
        ]
        # Out-of-order point deltas: pB rev 11 arrives BEFORE pA rev 10, followed by stale pA rev 9 backfill.
        point_deltas = [
            {"deviceId": device_id, "pointId": "pB", "pointDataRevision": 11, "quality": "GOOD", "sampleAtMs": ts_ms + 1000},
            {"deviceId": device_id, "pointId": "pA", "pointDataRevision": 10, "quality": "GOOD", "sampleAtMs": ts_ms + 500},
            {"deviceId": device_id, "pointId": "pA", "pointDataRevision": 9, "quality": "BAD_TIMEOUT", "sampleAtMs": ts_ms - 1000},
        ]
        return {"state_events": state_events, "point_deltas": point_deltas}

    @staticmethod
    def bad_quality_conservation_sequence(*, device_id: str = "meter-001", point_id: str = "p1", start_ms: int = 1763600000000) -> list[dict]:
        """GOOD -> BAD_TIMEOUT -> BAD_DECODE -> GOOD sequence verifying exact count conservation (0->1->1->0)."""
        return [
            {"deviceId": device_id, "pointId": point_id, "pointDataRevision": 1, "quality": "GOOD", "sampleAtMs": start_ms, "expectedBadCount": 0},
            {"deviceId": device_id, "pointId": point_id, "pointDataRevision": 2, "quality": "BAD_TIMEOUT", "sampleAtMs": start_ms + 1000, "expectedBadCount": 1},
            {"deviceId": device_id, "pointId": point_id, "pointDataRevision": 3, "quality": "BAD_DECODE", "sampleAtMs": start_ms + 2000, "expectedBadCount": 1},
            {"deviceId": device_id, "pointId": point_id, "pointDataRevision": 4, "quality": "GOOD", "sampleAtMs": start_ms + 3000, "expectedBadCount": 0},
        ]

    @staticmethod
    def re_enable_dual_outbox_and_backfill_sequence(*, device_id: str = "meter-001", re_enable_ms: int = 1763600010000) -> dict[str, list[dict]]:
        """Generates both out-of-order re-enable delivery and pre-enable cached backfill cases."""
        return {
            "new_good_before_enable_event": [
                {"kind": "DEVICE_STATE", "deviceId": device_id, "collectionOff": True, "configVersion": 5, "tsMs": re_enable_ms - 5000},
                {"kind": "POINT_DATA", "deviceId": device_id, "pointId": "p1", "configVersion": 6, "quality": "GOOD", "sampleAtMs": re_enable_ms + 500, "receivedAtMs": re_enable_ms + 500},
                {"kind": "DEVICE_STATE", "deviceId": device_id, "collectionOff": False, "configVersion": 6, "prevOffConfigVersion": 5, "tsMs": re_enable_ms + 800},
            ],
            "same_version_v1_cached_backfill": [
                {"kind": "DEVICE_STATE", "deviceId": device_id, "collectionOff": True, "configVersion": 5, "tsMs": re_enable_ms - 5000},
                {"kind": "DEVICE_STATE", "deviceId": device_id, "collectionOff": False, "configVersion": 5, "prevOffConfigVersion": 5, "reEnableSourceAtMs": re_enable_ms, "tsMs": re_enable_ms},
                # Pre-enable cached sample arrives AFTER re-enable (receivedAt > reEnableAt, sampleAt < reEnableSourceAt): must NOT unlock WAITING_DATA!
                {"kind": "POINT_DATA", "deviceId": device_id, "pointId": "p1", "configVersion": 5, "quality": "GOOD", "sampleAtMs": re_enable_ms - 2000, "receivedAtMs": re_enable_ms + 500, "mustUnlockWaiting": False},
                # Genuine post-enable sample: unlocks NORMAL!
                {"kind": "POINT_DATA", "deviceId": device_id, "pointId": "p1", "configVersion": 5, "quality": "GOOD", "sampleAtMs": re_enable_ms + 2000, "receivedAtMs": re_enable_ms + 2000, "mustUnlockWaiting": True},
            ],
        }

    @staticmethod
    def clock_rollback_v2_sequence(*, gateway_id: str = "GW-V2", device_id: str = "meter-001", start_ms: int = 1763600000000) -> list[dict]:
        """Simulates NTP backward clock step while v2 revisions & collectionEpoch remain strictly monotonic."""
        return [
            {
                "schemaVersion": 2,
                "gatewayId": gateway_id,
                "sessionId": "s-v2",
                "configVersion": 7,
                "timestamp": start_ms,
                "clockRevision": 0,
                "timeQuality": "SYNCED",
                "device": {"deviceId": device_id, "status": "ONLINE", "stateRevision": 1, "collectionEpoch": 1},
            },
            {
                "schemaVersion": 2,
                "gatewayId": gateway_id,
                "sessionId": "s-v2",
                "configVersion": 7,
                "timestamp": start_ms - 60000,  # Wall clock rolled back 60 seconds!
                "clockRevision": 1,
                "timeQuality": "STEPPED",
                "device": {"deviceId": device_id, "status": "OFFLINE", "stateRevision": 2, "collectionEpoch": 1},
            },
            {
                "schemaVersion": 2,
                "gatewayId": gateway_id,
                "sessionId": "s-v2",
                "configVersion": 7,
                "timestamp": start_ms - 59000,
                "clockRevision": 1,
                "timeQuality": "HOLDING",
                "device": {"deviceId": device_id, "status": "ONLINE", "stateRevision": 3, "collectionEpoch": 2},
            },
        ]


def deterministic_scenario_digest(payloads: list[dict]) -> str:
    """Stable SHA-256 fingerprint for regression checking generated fault streams."""
    import json
    canonical = json.dumps(payloads, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
