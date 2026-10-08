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
reopened or automatically completed. Open reminders refresh only their managed
follow-up note section, preserving staff-authored text and routing. A new claim is checked again
inside the insert transaction, as is the continued presence of the dispense
event. Recipient identities are checked each run.

## Appointment context and native patient display

Gus extended the monitor on October 8 to include missed appointments after the
dispense, future scheduling, and the office where the last biologic was recorded.
Live schedule procedures are 21 XOLAIR, 53 FASENRA, and 55 TEZSPIRE. Detection
checks primary, secondary, and tertiary procedure slots, with appointment dates
on or after the dispense. Native status M indicates missed; future M rows are
not counted as missed. Canceled timestamps or status C exclude active future
bookings. Future rows can carry status D, so D is not interpreted as completed.

Context includes missed dates/times/offices and count, matching booking count,
next appointment for the same biologic, and next appointment of any type. The
last biologic office/date comes from the latest same-patient, same-drug J-code
service record, joined through billing_header.office_id to office_master. All
offices on a tied latest date are retained. This is recorded service evidence;
scheduling alone does not establish administration or receipt. Missing service
records are explicitly unknown. Full matching appointment history is reported.

The [BIOLOGIC FOLLOW-UP] section is replaced idempotently on open reminders.
Updates guard the previous full note and pending state. Staff text outside the
section remains intact; malformed markers or notes that would exceed the native
2,000-character limit are preserved and reported as skipped. Full native notes
are read through XML export to avoid console output truncation. Completed
reminders are not updated. Titles, recipients, priority, dates, and assignment
rows are not changed by context refresh.

Native patient reminders use forwhom_name `Last, First  (patient_no)` and
reference_flag `1`. New inserts now use both. All 28 existing generated
reminders were corrected to this native display on October 8, after comparing
manually created patient reminders. Saved native fields were verified; the
minimized IMS window prevented a visual icon check.

## Initial scope and schedule

The monitor includes all recorded history for the exact target codes. These
codes first appear in June/July 2025. After the broader one-time audit found just
four additional older patient findings, the original unrestricted request was
applied to those too. There is no rolling cutoff. An explicit --start-date
override is available for bounded dry-run investigations.

Runtime files:

- /opt/ims_router/biologic_missing_claims.py
- /opt/ims_router/biologic_followup.py
- /opt/ims_router/run-biologic-missing-claims.sh
- /opt/ims_router/output/biologic_missing_claims/status.json
- /opt/ims_router/output/biologic_missing_claims/missing-current.csv
- /opt/ims_router/output/biologic_missing_claims/late-claims.csv
- /opt/ims_router/output/biologic_missing_claims/open-reminder-context.csv
- /opt/ims_router/output/biologic_missing_claims/appointment-history.csv
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

- Nineteen focused tests passed, including day 30/31, prior/same-day claims,
  wrong drug/patient, unclaimed charges, late claims, mixed doses, all mappings,
  future service dates, native ID/recipient/dedupe transaction structure,
  all three appointment procedure slots, missed/canceled/future statuses,
  next any-type appointment, service-office ties, and guarded staff-note preservation.
- Pilot reminder 940092 for patient 19731 was read back with pending detail,
  show date 2026-10-08, and recipients ,1,24,.
- Initial production apply created and verified 24 reminders for 16 patients,
  including that pilot. Full-history completion added four older reminders,
  for 28 reminders across 20 patients. Repeat applies created zero reminders.
- The full target-code history audit returned 28 still-missing events for 20
  patients, including 19731. All 28 findings have verified reminders.
- Fifty historical events had a later matching claim beyond 30 days; these are
  in late-claims.csv. Thirty-six fall within the initial reminder boundary.
- All 28 parent/child pairs were independently counted with correct recipients,
  pending status and visible show dates after creation.
- Follow-up apply enriched all 28 open reminders and corrected patient display
  and reference flag. Full-row comparison of all 121 parent fields allowed only
  note, changed_date, forwhom_name, and reference_flag; all other fields and all
  28 child rows were unchanged. Private snapshots are followup-before-20261008T134236.json
  and followup-after-20261008T134236.json under the runtime output directory,
  with mode 0600. A full XML snapshot also precedes the repair.
- Repeat apply at 13:46 Eastern created zero reminders, updated zero contexts,
  and skipped zero notes. Latest missing source count remains 27 events / 19
  patients, with 50 late-claim events. Open-reminder-context.csv retains all 28
  reminders, including the earlier patient 19731 finding.

During installation, the June 10, 2026 XO150 dispense for patient 19731
(billing_detail 706953/2) disappeared from IMS. Final source readback found no
such row and no new subsequent J2357 claim. The latest monitor snapshot therefore
has 27 unresolved events across 19 patients. The 28th reminder records the
earlier valid finding and remains pending for staff disposition. Do not describe
this as a new claim or have the monitor automatically remove the reminder.

An early pilot waiting for an exclusive table lock was canceled before any
insert. Native autoincrement was then verified live and used instead. No other
database session was interrupted and no database schema was changed.

Private one-time reports are under reports/ locally and under
/opt/ims_router/output/biologic_missing_claims/one-time-20261008 remotely.
