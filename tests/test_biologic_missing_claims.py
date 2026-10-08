import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

spec = importlib.util.spec_from_file_location(
    "monitor", Path(__file__).parents[1] / "scripts/biologic_missing_claims.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def row(day, code="XO150", claim="N", patient="5"):
    return ["DATA", patient, "100", "Test, Patient", day, code, "10", "2", claim]


class Rules(unittest.TestCase):
    def check(self, rows, today=date(2026, 3, 3)):
        return monitor.evaluate(rows, today, date(2026, 1, 1))

    def test_full_sixtieth_day_is_allowed(self):
        self.assertEqual(self.check([row("2026-01-01")], date(2026, 3, 2)), ([], []))
        self.assertEqual(len(self.check([row("2026-01-01")])[0]), 1)

    def test_day_sixty_claim_satisfies(self):
        self.assertEqual(self.check([row("2026-01-01"), row("2026-03-02", "J2357", "Y")]), ([], []))

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
        missing, late = self.check([row("2026-01-01"), row("2026-03-03", "J2357", "Y")])
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
        self.assertEqual(len(self.check([row("2026-01-01"), row("2026-03-04", "J2357", "Y")])[0]), 1)

    def test_nine_calendar_month_cutoff(self):
        self.assertEqual(monitor.monitoring_cutoff(date(2026, 10, 8)), date(2026, 1, 8))
        self.assertEqual(monitor.monitoring_cutoff(date(2026, 11, 30)), date(2026, 2, 28))
        self.assertEqual(monitor.monitoring_cutoff(date(2024, 11, 30)), date(2024, 2, 29))

    def test_cutoff_is_inclusive_and_old_dispense_excluded(self):
        today = date(2026, 10, 8)
        missing, _ = monitor.evaluate([row('2026-01-07'), row('2026-01-08')], today,
                                       monitor.monitoring_cutoff(today))
        self.assertEqual([e['dispense_date'] for e in missing], ['2026-01-08'])

    def test_sql_routes_both_and_dedupes_done(self):
        event = self.check([row("2026-01-01")])[0][0]
        sql = monitor.insert_sql(event)
        self.assertIn("',1,24,'", sql)
        self.assertIn("'Test, Patient  (100)'", sql)
        self.assertIn("'N','1',", sql)
        self.assertIn("INSERT INTO tobe_done_detail", sql)
        self.assertIn("WHERE NOT EXISTS(SELECT 1 FROM todo WHERE source=", sql)
        self.assertNotIn("LOCK TABLE", sql)
        self.assertNotIn("MAX(tran_id)", sql)
        self.assertIn("AND EXISTS(SELECT 1 FROM billing_detail", sql)
        self.assertIn("TODAY()>DATEADD(day,60,'2026-01-01')", sql)
        self.assertIn('no J-code claim after 60 days', sql)
        self.assertIn('BIO_NO_CLAIM30:', sql)
        self.assertEqual(sql.count("COMMIT;"), 1)

    def test_pending_reminder_waits_through_day_sixty_then_becomes_due(self):
        event = {'patient_id': '5', 'drug': 'XOLAIR', 'j_code': 'J2357',
                 'dispense_date': '2026-01-01', 'reminder_task_state': 'OPEN',
                 'show_date_min': '2026-02-01', 'show_date_max': '2026-02-01'}
        monitor.enrich_window([event], [], date(2026, 3, 2))
        self.assertEqual(event['defer_date'], '2026-03-03')
        self.assertEqual(event['claim_window_status'], 'WAITING FOR FULL WINDOW')
        event.pop('defer_date')
        monitor.enrich_window([event], [], date(2026, 3, 3))
        self.assertNotIn('defer_date', event)
        self.assertEqual(event['claim_window_status'], 'WINDOW ELAPSED; NO MATCHING CLAIM')

    def test_extended_window_linked_claim_closes_instead_of_deferred_alert(self):
        event = {'patient_id': '5', 'drug': 'XOLAIR', 'j_code': 'J2357',
                 'dispense_date': '2026-01-01', 'reminder_task_state': 'OPEN',
                 'todo_id': '1', 'source': 'BIO_NO_CLAIM30:5:J2357:20260101'}
        monitor.enrich_window([event], [row('2026-02-15', 'J2357', 'Y')], date(2026, 3, 2))
        self.assertNotIn('defer_date', event)
        self.assertEqual(event['qualifying_claim_date'], '2026-02-15')
        sql = monitor.complete_claim_sql(event, 'expected note')
        self.assertIn('JOIN claim_detail', sql)
        self.assertIn("bd.billing_id='J2357'", sql)
        self.assertIn("COALESCE(t.note,'')='expected note'", sql)
        self.assertIn("Biologic matching claim monitor", sql)
        event['qualifying_claim_date'] = ''
        with self.assertRaises(ValueError):
            monitor.complete_claim_sql(event, 'expected note')

    def test_unclaimed_charge_prior_and_wrong_claim_do_not_cancel_deferral(self):
        event = {'patient_id': '5', 'drug': 'XOLAIR', 'j_code': 'J2357',
                 'dispense_date': '2026-01-01', 'reminder_task_state': 'OPEN'}
        monitor.enrich_window([event], [row('2026-02-15', 'J2357'),
            row('2025-12-31', 'J2357', 'Y'), row('2026-02-15', 'J2356', 'Y'),
            row('2026-02-15', 'J2357', 'Y', '6')], date(2026, 3, 2))
        self.assertEqual(event['claim_window_status'], 'WAITING FOR FULL WINDOW')
        self.assertEqual(event['qualifying_claim_date'], '')

    def test_done_or_already_scheduled_reminders_are_not_deferred_again(self):
        for state in ['DONE', 'OPEN']:
            event = {'patient_id': '5', 'drug': 'XOLAIR', 'j_code': 'J2357',
                     'dispense_date': '2026-01-01', 'reminder_task_state': state,
                     'show_date_min': '2026-03-03', 'show_date_max': '2026-03-03'}
            monitor.enrich_window([event], [], date(2026, 3, 2))
            self.assertNotIn('defer_date', event)


if __name__ == "__main__":
    unittest.main()
