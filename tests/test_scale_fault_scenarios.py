"""Unit tests for offline scale & fault-injection scenario generators."""
import importlib.util
from pathlib import Path
import sys
import unittest

try:
    from iot_sim.scale_fault_scenarios import (
        FaultInjectionScenarioBuilder,
        MultiMeterStormGenerator,
        deterministic_scenario_digest,
    )
except ModuleNotFoundError:
    _mod_path = Path(__file__).resolve().parents[1] / "iot_sim" / "scale_fault_scenarios.py"
    _spec = importlib.util.spec_from_file_location("scale_fault_scenarios", _mod_path)
    _mod = importlib.util.module_from_spec(_spec)
    assert _spec is not None and _spec.loader is not None
    sys.modules["scale_fault_scenarios"] = _mod
    _spec.loader.exec_module(_mod)
    FaultInjectionScenarioBuilder = _mod.FaultInjectionScenarioBuilder
    MultiMeterStormGenerator = _mod.MultiMeterStormGenerator
    deterministic_scenario_digest = _mod.deterministic_scenario_digest


class ScaleFaultScenarioTests(unittest.TestCase):
    def test_multi_meter_storm_interleaves_quiet_gateway_heartbeats_deterministically(self):
        gen1 = MultiMeterStormGenerator(seed=42)
        gen2 = MultiMeterStormGenerator(seed=42)
        storm1 = gen1.build_storm(meter_count=100, rounds=3, heartbeat_every=30)
        storm2 = gen2.build_storm(meter_count=100, rounds=3, heartbeat_every=30)

        self.assertEqual(storm1, storm2)
        device_msgs = [e for e in storm1 if e.topic.endswith("/device/status")]
        hb_msgs = [e for e in storm1 if e.topic.endswith("/heartbeat")]
        self.assertEqual(len(device_msgs), 300)
        self.assertEqual(len(hb_msgs), 10)
        self.assertTrue(all(e.payload["gatewayId"] == "NG-HUB-1252-01" for e in hb_msgs))

    def test_equal_ms_conflict_and_out_of_order_point_revisions(self):
        bundle = FaultInjectionScenarioBuilder.equal_ms_conflict_and_out_of_order_points()
        timestamps = {ev["timestamp"] for ev in bundle["state_events"]}
        self.assertEqual(len(timestamps), 1)
        self.assertEqual([ev["device"]["status"] for ev in bundle["state_events"]], ["ONLINE", "OFFLINE", "ONLINE"])
        revs = [(p["pointId"], p["pointDataRevision"]) for p in bundle["point_deltas"]]
        self.assertEqual(revs, [("pB", 11), ("pA", 10), ("pA", 9)])

    def test_bad_quality_conservation_and_reenable_backfill_scenarios(self):
        seq = FaultInjectionScenarioBuilder.bad_quality_conservation_sequence()
        self.assertEqual([x["quality"] for x in seq], ["GOOD", "BAD_TIMEOUT", "BAD_DECODE", "GOOD"])
        self.assertEqual([x["expectedBadCount"] for x in seq], [0, 1, 1, 0])

        reenable = FaultInjectionScenarioBuilder.re_enable_dual_outbox_and_backfill_sequence()
        self.assertEqual(reenable["new_good_before_enable_event"][1]["kind"], "POINT_DATA")
        self.assertEqual(reenable["new_good_before_enable_event"][2]["kind"], "DEVICE_STATE")
        backfill_pts = [x for x in reenable["same_version_v1_cached_backfill"] if x["kind"] == "POINT_DATA"]
        self.assertFalse(backfill_pts[0]["mustUnlockWaiting"])
        self.assertTrue(backfill_pts[1]["mustUnlockWaiting"])

    def test_clock_rollback_v2_revisions_remain_strictly_monotonic(self):
        v2 = FaultInjectionScenarioBuilder.clock_rollback_v2_sequence()
        self.assertLess(v2[1]["timestamp"], v2[0]["timestamp"])  # Wall clock moved backward
        self.assertEqual([m["device"]["stateRevision"] for m in v2], [1, 2, 3])
        self.assertEqual(v2[1]["clockRevision"], 1)
        self.assertEqual(v2[1]["timeQuality"], "STEPPED")
        self.assertEqual(v2[2]["device"]["collectionEpoch"], 2)
        self.assertEqual(len(deterministic_scenario_digest(v2)), 64)


if __name__ == "__main__":
    unittest.main()
