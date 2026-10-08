# Nightly biologic dispense / missing claim check

Gus commissioned this monitor and an immediate check on October 8, 2026.
The owning repo is IMS-Biologics_Authorization. The worker runs on ims-referrals
under the existing /opt/ims_router runtime and uses its IMS database helpers.

## Rule and routing

Watch the live codes XOL75, XO150, XO300 (J2357), FAS30/FASDI (J0517), and
TEZ (J2356). These are the actual IMS names for the requested 75/150/300 mg
Xolair, Fasenra, and 210 mg Tezspire dispense markers. Do not confuse these
with First Allergy Shot appointments. Other generic/legacy dispense codes
are outside this monitor.

Group same-patient, same-drug, same-service-date dispense lines into one event,
including mixed Xolair doses. Give the complete dispense-date through day-30
service window, inclusive. A reminder becomes eligible on day 31. A qualifying
claim has the same patient and drug J-code, linked from billing_detail using
tran_id/sr_id through claim_tracker to claim_detail. Payment, payer acceptance,
and transmission are not required by this existence check. A bare J-code charge
without this claim linkage remains a finding. claim_tracker.billing_id and
service_from can be NULL on real paid claims; do not require either field.

The check asks whether any subsequent matching claim exists; it is not a
dose/quantity reconciliation or a one-to-one stock allocation. Same-day claims
qualify because service dates cannot establish within-day order. Earlier claims
never qualify. Late matching claims are retained in a separate report and do
not create a stale current-missing reminder. Staff review determines actual
administration, supply source, billing, or other documented disposition.

Reminders are high priority, patient linked, assigned directly to verified IMS
employee 1 Ghassan Safadi and employee 24 Rachel Clark. Both todo and pending
tobe_done_detail rows are inserted in one transaction, visible today. Native
detail autoincrement assigns its ID. No table-wide lock or MAX+1 allocator is
used. The reminder is editable (is_auto=N), with source
BIO_NO_CLAIM30:<patient_id>:<J-code>:<YYYYMMDD>. All existing source keys suppress
duplicates, including reminders staff have completed. No existing reminder is
reopened, rewritten, or automatically completed. A new claim is checked again
inside the insert transaction. Recipient identities are checked each run.

## Initial scope and schedule

The initial reminder boundary is fixed at 2025-10-08 (past 12 months at setup),
and does not roll forward. Gus was offered a backlog preference during work;
no answer had arrived at installation, so this default was stated before apply.
The broader one-time audit is read-only. Change the fixed boundary only when
requested; do not silently write all historical reminders.

Runtime files:

- /opt/ims_router/biologic_missing_claims.py
- /opt/ims_router/run-biologic-missing-claims.sh
- /opt/ims_router/output/biologic_missing_claims/status.json
- /opt/ims_router/output/biologic_missing_claims/missing-current.csv
- /opt/ims_router/output/biologic_missing_claims/late-claims.csv
- /opt/ims_router/logs/biologic_missing_claims.log

The existing user's crontab has exactly one new entry at 23:40 daily. Runtime
timezone was verified as Eastern (EDT on installation). A flock prevents overlap.
The previous crontab is privately backed up inside the output directory, and
readback proved all previous cron text preserved. The first scheduled run is
October 8 at 11:40 p.m. Eastern. Manual setup/verification runs are separate.

No email or SMS is sent; delivery is the native IMS My Tasks recipient assignment.
Reports contain patient information and are Git-ignored. Runtime reports use
umask 077 and mode 0600; scheduled logs contain aggregate counts only.

## Verified production result

- Ten focused rule tests passed, including day 30/31, prior/same-day claims,
  wrong drug/patient, unclaimed charges, late claims, mixed doses, all mappings,
  future service dates, and native ID/recipient/dedupe transaction structure.
- Pilot reminder 940092 for patient 19731 was read back with pending detail,
  show date 2026-10-08, and recipients ,1,24,.
- Initial production apply created and verified 24 reminders for 16 patients,
  including that pilot. A repeat apply created zero reminders.
- The full target-code history audit returned 28 still-missing events for 20
  patients, including 19731. Four older patient findings are report-only.
- Fifty historical events had a later matching claim beyond 30 days; these are
  in late-claims.csv. Thirty-six fall within the initial reminder boundary.
- All 24 parent/child pairs were independently counted with correct recipients,
  pending status and visible show dates after creation.

An early pilot waiting for an exclusive table lock was canceled before any
insert. Native autoincrement was then verified live and used instead. No other
database session was interrupted and no database schema was changed.

Private one-time reports are under reports/ locally and under
/opt/ims_router/output/biologic_missing_claims/one-time-20261008 remotely.
