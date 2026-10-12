"""Independent review checks for transactional claims and ambiguous submissions."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from kmw_autotrade import OrderBlocked, OrderLedger, active_reservation, reservation_window, submit_plan
from meritz_api import AmbiguousSubmission


def order(round_num):
    return {
        "ticker": "SOXL", "side": "SELL", "quantity": 1,
        "price": "20.00", "order_type": "LOC", "round": round_num,
        "position_key": f"{round_num}_2026-10-05",
    }


def plan(*rounds):
    return {"preset": "KMW", "order_date": "2026-10-12",
            "orders": [order(round_num) for round_num in rounds]}


class IndependentOrderLedgerReviewTests(unittest.TestCase):
    def test_periodic_reservation_remains_active_after_a_previous_day_fill(self):
        reservation = {
            "ovrs_stck_symb_iscd": "SOXL", "oder_rsev_cls_code": "3",
            "rsev_cncl_yn": "", "cncl_able_yn": "N",
            "oder_fin_date": "20261030", "odqt": 1, "acml_cntg_qty": 5,
        }
        self.assertTrue(active_reservation(reservation, "20261012"))

    def test_claim_collision_rolls_back_new_rows_from_the_whole_batch(self):
        with TemporaryDirectory() as temp:
            ledger = OrderLedger(Path(temp) / "orders.sqlite3")
            try:
                ledger.claim("account", plan(2))
                with self.assertRaises(OrderBlocked):
                    ledger.claim("account", plan(1, 2))
                rows = ledger.rows("account")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["order"]["round"], 2)
            finally:
                ledger.db.close()

    def test_two_connections_can_only_claim_the_same_orders_once(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "orders.sqlite3"
            # Initialize schema before concurrent workers start.
            initial = OrderLedger(path)
            initial.db.close()
            barrier = Barrier(2)

            def claim():
                ledger = OrderLedger(path)
                try:
                    barrier.wait(timeout=5)
                    try:
                        ledger.claim("account", plan(1))
                        return "claimed"
                    except OrderBlocked:
                        return "blocked"
                finally:
                    ledger.db.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: claim(), range(2)))
            self.assertCountEqual(outcomes, ["claimed", "blocked"])
            final = OrderLedger(path)
            try:
                self.assertEqual(len(final.rows("account")), 1)
            finally:
                final.db.close()

    def test_timeout_stops_the_batch_and_blocks_orders_on_another_day(self):
        with TemporaryDirectory() as temp:
            ledger = OrderLedger(Path(temp) / "orders.sqlite3")
            client = Mock()
            client.submit_reserved_order.side_effect = [
                {"data": {"oder_rsev_mst_no": 10, "warn_cls_code": "0"}},
                AmbiguousSubmission("Interrupted submission"),
            ]
            try:
                with redirect_stdout(io.StringIO()), self.assertRaises(AmbiguousSubmission):
                    submit_plan(client, plan(1, 2, 3), ledger, "account", "0066")
                self.assertEqual(client.submit_reserved_order.call_count, 2)
                rows = {row["order"]["round"]: row for row in ledger.rows("account")}
                self.assertEqual(rows[1]["state"], "RESERVED")
                self.assertEqual(rows[2]["state"], "UNKNOWN")
                self.assertEqual(rows[3]["state"], "NOT_SENT")
                tomorrow = plan(4)
                tomorrow["order_date"] = "2026-10-13"
                with self.assertRaises(OrderBlocked):
                    submit_plan(client, tomorrow, ledger, "account", "0066")
                self.assertEqual(client.submit_reserved_order.call_count, 2)
            finally:
                ledger.db.close()

    def test_acceptance_is_recorded_as_reserved_without_mutating_the_plan(self):
        with TemporaryDirectory() as temp:
            ledger = OrderLedger(Path(temp) / "orders.sqlite3")
            client = Mock()
            client.submit_reserved_order.return_value = {"data": {"oder_rsev_mst_no": 10}}
            original = plan(1)
            expected = deepcopy(original)
            try:
                results = submit_plan(client, original, ledger, "account", "0066")
                self.assertEqual(original, expected)
                self.assertEqual(results[0]["state"], "RESERVED")
                self.assertEqual(ledger.rows("account")[0]["state"], "RESERVED")
            finally:
                ledger.db.close()

    def test_1701_window_handles_both_us_dst_states(self):
        kst = ZoneInfo("Asia/Seoul")
        for day in ("2026-10-12", "2026-11-02"):
            with self.subTest(day=day):
                reservation_window(datetime.fromisoformat(day + "T17:01:00").replace(tzinfo=kst), day)
        with self.assertRaises(OrderBlocked):
            reservation_window(datetime(2026, 10, 12, 21, 30, tzinfo=kst), "2026-10-12")
        with self.assertRaises(OrderBlocked):
            reservation_window(datetime(2026, 10, 12, 8, 59, tzinfo=kst), "2026-10-12")


if __name__ == "__main__":
    unittest.main()
