import importlib.util
import unittest
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "monitor", Path(__file__).parents[1] / "scripts/biologic_missing_claims.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def row(day, code="XO150", claim="N", patient="5"):
    return ["DATA", patient, "100", "Test, Patient", day, code, "10", "2", claim]


class Rules(unittest.TestCase):
    def check(self, rows, today=date(2026, 2, 2)):
        return monitor.evaluate(rows, today, date(2026, 1, 1))

    def test_full_thirtieth_day_is_allowed(self):
        self.assertEqual(self.check([row("2026-01-01")], date(2026, 1, 31)), ([], []))
        self.assertEqual(len(self.check([row("2026-01-01")])[0]), 1)

    def test_day_thirty_claim_satisfies(self):
        self.assertEqual(self.check([row("2026-01-01"), row("2026-01-31", "J2357", "Y")]), ([], []))

    def test_prior_claim_does_not_satisfy(self):
        self.assertEqual(len(self.check([row("2026-01-01"), row("2025-12-31", "J2357", "Y")])[0]), 1)

    def test_same_day_claim_satisfies(self):
        self.assertEqual(self.check([row("2026-01-01"), row("2026-01-01", "J2357", "Y")]), ([], []))

    def test_wrong_patient_or_drug_does_not_satisfy(self):
        for j in [row("2026-01-02", "J2356", "Y"), row("2026-01-02", "J2357", "Y", "6")]:
            self.assertEqual(len(self.check([row("2026-01-01"), j])[0]), 1)

    def test_charge_without_claim_stays_missing(self):
        missing, _ = self.check([row("2026-01-01"), row("2026-01-02", "J2357")])
        self.assertEqual(missing[0]["later_posted_j_date"], "2026-01-02")

    def test_late_claim_separate_from_current_missing(self):
        missing, late = self.check([row("2026-01-01"), row("2026-02-01", "J2357", "Y")])
        self.assertEqual(len(missing), 0)
        self.assertEqual(len(late), 1)

    def test_mixed_doses_one_event_and_all_drug_mappings(self):
        missing, _ = self.check([row("2026-01-01"), row("2026-01-01", "XOL75")])
        self.assertEqual(len(missing), 1)
        self.assertEqual(set(missing[0]["codes"]), {"XO150", "XOL75"})
        for code, (_, j, _) in monitor.DRUGS.items():
            self.assertEqual(self.check([row("2026-01-01", code), row("2026-01-02", j, "Y")]), ([], []))

    def test_fixed_start_and_future_claim(self):
        self.assertEqual(self.check([row("2025-12-31")]), ([], []))
        self.assertEqual(len(self.check([row("2026-01-01"), row("2026-02-03", "J2357", "Y")])[0]), 1)

    def test_sql_routes_both_and_dedupes_done(self):
        event = self.check([row("2026-01-01")])[0][0]
        sql = monitor.insert_sql(event)
        self.assertIn("',1,24,'", sql)
        self.assertIn("INSERT INTO tobe_done_detail", sql)
        self.assertIn("WHERE NOT EXISTS(SELECT 1 FROM todo WHERE source=", sql)
        self.assertNotIn("LOCK TABLE", sql)
        self.assertNotIn("MAX(tran_id)", sql)
        self.assertEqual(sql.count("COMMIT;"), 1)


if __name__ == "__main__":
    unittest.main()
