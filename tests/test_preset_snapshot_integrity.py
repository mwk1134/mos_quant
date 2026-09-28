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

    def test_filled_september_24_lot_is_removed_from_every_preset(self):
        for preset_name in ("KMW", "JEH", "JEH2", "KMW2", "KHW"):
            with self.subTest(preset=preset_name):
                self.assertNotIn("2_2026-09-24", self.data[preset_name])

    def test_september_25_sales_keep_pending_compounding_settlements(self):
        expected_pnl = {
            "KMW": 197.59999999999945 + 440.3198425292958,
            "JEH": 57.19999999999982 + 133.11995239257794,
            "KMW2": 171.59999999999945 + 378.8798645019524,
            "JEH2": 10.399999999999977 + 20.479992675781205,
            "KHW": 41.59999999999991 + 97.27996520996066,
        }
        for preset_name, pnl in expected_pnl.items():
            with self.subTest(preset=preset_name):
                settlements = self.data[preset_name]["compound_settlements"]
                self.assertEqual(len(settlements), 2)
                self.assertTrue(all(
                    item["trade_date"] == "2026-09-25"
                    and item["settlement_date"] == "2026-10-02"
                    for item in settlements
                ))
                self.assertAlmostEqual(sum(item["pnl"] for item in settlements), pnl)


if __name__ == "__main__":
    unittest.main()
