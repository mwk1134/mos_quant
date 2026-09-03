import json
import unittest
from datetime import datetime
from pathlib import Path


SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "data" / "positions_snapshots.json"


class PresetSnapshotIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with SNAPSHOT_PATH.open("r", encoding="utf-8") as f:
            cls.data = json.load(f)

    def _position_shares(self, preset_name):
        snapshot = self.data[preset_name]
        return {
            key: int(value["shares"])
            for key, value in snapshot.items()
            if isinstance(value, dict) and "shares" in value
        }

    def test_unused_jsd_preset_is_removed(self):
        self.assertNotIn("JSD", self.data)
        self.assertNotIn("JSD", self.data.get("_preset_configs", {}))

    def test_dynamic_snapshot_runtime_fields_are_consistent(self):
        # Positions legitimately change after each completed market day, so this
        # test validates invariants instead of freezing one day's account state.
        for preset_name in ("KMW", "JEH", "JEH2", "KMW2", "KHW"):
            with self.subTest(preset=preset_name):
                snapshot = self.data[preset_name]
                positions = self._position_shares(preset_name)
                self.assertTrue(positions)
                self.assertTrue(all(shares > 0 for shares in positions.values()))
                self.assertGreaterEqual(float(snapshot["available_cash"]), 0.0)

                as_of_date = datetime.strptime(snapshot["as_of_date"], "%Y-%m-%d").date()
                position_dates = [
                    datetime.strptime(key.split("_", 1)[1], "%Y-%m-%d").date()
                    for key in positions
                ]
                self.assertLessEqual(max(position_dates), as_of_date)

                pending = snapshot.get("pending_buy")
                if pending:
                    self.assertGreater(int(pending["round"]), 0)
                    self.assertGreater(int(pending["quantity"]), 0)
                    self.assertGreater(float(pending["target_price"]), 0.0)
                    basis_date = datetime.strptime(pending["basis_date"], "%Y-%m-%d").date()
                    order_date = datetime.strptime(pending["order_date"], "%Y-%m-%d").date()
                    self.assertLessEqual(basis_date, as_of_date)
                    self.assertGreater(order_date, basis_date)

    def test_position_amounts_match_confirmed_shares_and_prices(self):
        for preset_name in ("KMW", "JEH", "JEH2", "KMW2", "KHW"):
            for key, value in self.data[preset_name].items():
                if not isinstance(value, dict) or "shares" not in value:
                    continue
                with self.subTest(preset=preset_name, position=key):
                    self.assertAlmostEqual(
                        float(value["amount"]),
                        int(value["shares"]) * float(value["buy_price"]),
                    )

    def test_kmw_required_seed_increase_is_persisted(self):
        seeds = self.data["_preset_configs"]["KMW"]["seed_increases"]
        self.assertIn(
            {"date": "2026-06-21", "amount": 11293.0},
            seeds,
        )


if __name__ == "__main__":
    unittest.main()
