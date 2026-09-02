import json
import unittest
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

    def test_confirmed_positions_and_cash_are_preserved(self):
        expected = {
            "KMW": {
                "positions": {
                    "3_2026-07-15": 119,
                    "4_2026-07-22": 148,
                    "5_2026-07-23": 15,
                    "7_2026-08-31": 119,
                    "7_2026-09-01": 117,
                },
                "cash": 32518.0,
                "total": 518,
            },
            "JEH": {
                "positions": {
                    "3_2026-07-15": 40,
                    "4_2026-07-22": 50,
                    "5_2026-07-23": 5,
                    "8_2026-08-28": 4,
                    "7_2026-08-31": 36,
                    "7_2026-09-01": 35,
                },
                "cash": 14363.0,
                "total": 170,
            },
            "JEH2": {
                "positions": {
                    "3_2026-07-15": 7,
                    "4_2026-07-22": 9,
                    "5_2026-07-23": 1,
                    "8_2026-08-28": 1,
                    "7_2026-08-31": 6,
                    "7_2026-09-01": 6,
                },
                "cash": 3180.0,
                "total": 30,
            },
            "KMW2": {
                "positions": {
                    "3_2026-07-15": 110,
                    "4_2026-07-22": 137,
                    "5_2026-07-23": 14,
                    "7_2026-08-31": 102,
                    "7_2026-09-01": 100,
                },
                "cash": 30546.0,
                "total": 463,
            },
            "KHW": {
                "positions": {
                    "4_2026-08-28": 24,
                    "4_2026-08-31": 47,
                    "4_2026-09-01": 46,
                },
                "cash": 7809.859970092773,
                "total": 117,
            },
        }

        for preset_name, values in expected.items():
            with self.subTest(preset=preset_name):
                positions = self._position_shares(preset_name)
                self.assertEqual(positions, values["positions"])
                self.assertEqual(sum(positions.values()), values["total"])
                self.assertAlmostEqual(
                    float(self.data[preset_name]["available_cash"]),
                    values["cash"],
                )

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
