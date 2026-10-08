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
reopened. Open reminders refresh only their managed
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
rows are not changed by context refresh. User-authorized full-return and
outside-window completion separately changes native child Done/audit fields.

Native patient reminders use forwhom_name `Last, First  (patient_no)` and
reference_flag `1`. New inserts now use both. All 28 existing generated
reminders were corrected to this native display on October 8, after comparing
manually created patient reminders. Saved native fields were verified; the
minimized IMS window prevented a visual icon check.

## Return-to-stock verification

Gus also requested return checking on October 8. The native record is
drug_adjustment.adjust_for='1', linked by its deliberately misspelled
despense_id to prescription_dispense.tran_id. Live records include unused-drug
notes and the corresponding item_ledger.ref_type='1' transactions. Other
adjustment types do not count as dispense returns. Returns can be entered by
any staff member; entered_by joins emp_master to display the actual person.

Match inventory dispenses to the same patient, dispense date, and drug family,
including every mixed-dose line. Each accepted return must match the exact
dispense ID, patient, item, and internal lot ID, with a return date from dispense
through today. Its positive quantity must agree with the linked stock ledger
quantity for that adjustment, patient, item, lot, and office. Summed accepted
returns must also agree with prescription_dispense.adj_qty. Quantities use
Decimal and inventory units, not J-code billing units or milligrams.

All matching inventory lines must be fully accounted for, with all billed
dispense codes covered, before status is FULLY RETURNED TO STOCK. Partial
returns remain actionable. Missing inventory records, wrong identities,
quantity/ledger discrepancies, zero/invalid quantities, excess returns, or
other linked adjustment types remain UNKNOWN / REVIEW. Neither a removed
billing charge nor a missed appointment proves a stock return.

New reminder inserts recheck the full-return predicate inside the statement.
Verified full returns are excluded from missing-current.csv and retained in
returned-to-stock.csv. Existing open reminders receive return status,
quantities, return dates, office, staff, and record references in their managed
note section. Gus subsequently authorized marking these full returns Done.
Only pending detail rows belonging to the exact generated source are completed;
the full-return predicate and exact parent note are rechecked in the write.
Native task_status becomes D, with current done_date/time, system ID -1, and
done_by='Biologic stock return monitor'. The parent and source key are retained,
matching native My Tasks completion. Other routing and assignment fields remain
intact. Partial and unknown returns do not qualify for completion.

Return extension verification on October 8:

- Initial return tests passed, covering full/partial/fractional/mixed-dose quantities,
  identity/date mismatches, counter/ledger disagreement, and missing records.
- Live SQL suppression predicates matched Python classifications for all 28
  open reminders. Five were fully returned, all entered by Rachel Clark.
- Four fully returned events still appeared in the billing source; these are
  now excluded. The fifth return accounts for the earlier reminder whose
  billing dispense disappeared. Latest actionable count is 23 events for 16
  patients. Fifty late-claim events remain separately reported.
- Apply refreshed 28 notes, with no new reminders or skipped notes. Full-row
  readback verified that only note and changed_date changed; all 28 child rows
  and staff text outside the managed section were preserved.
- The initial context-only repeat at 14:06 Eastern made zero inserts, updates,
  or skips. After Gus's completion instruction, five verified full-return
  reminders were marked Done and independently read back.
- Native before/after snapshots and prior deployed source copies are private
  under output/biologic_missing_claims/stock-return-20261008. JSON snapshots use
  mode 0600. Local latest-returned-to-stock.csv is Git-ignored with other reports.

## Returned dispense visit cleanup

Gus explicitly authorized deletion of the zero-dollar, unclaimed dispense
visits for returned drugs, including the examples in Rachel's return reminders.
Nightly apply now runs this cleanup before claim evaluation. It scans linked
returns for the exact target-code inventory history, independently of the
nine-month open-reminder limit. There is no 30-day waiting period for a fully
returned dispense. Partial/uncertain returns never qualify.

Each candidate must match the exact patient, dispense date, drug codes, and
inventory superbill reference. Every billing line in the visit must be a
matching zero-dollar dispense line. Header/line charges, payments, writeoffs,
balances, transfers and open credit must all be zero. Claims, claim dates,
receipts, rejections, packages, clinical links, attached documents,
immunizations, other services, nonzero aging entries, and charge-origin stock
ledger rows protect the visit. Exact line count, quantities, update timestamps,
and current full-return evidence are rechecked in the delete statement.

The implementation deletes only billing_header, leaving native cascade,
balance, and history triggers enabled. No progress note, appointment, inventory
dispense, or return record is directly deleted. Native deletion history must be
written for the header and every line, no billing detail may remain, and the
full return must still verify before COMMIT; validation failure rolls back.
Full affected billing/ancillary and inventory/return/ledger snapshots are saved
before mutation, with after snapshots and an append-only deletion journal.
Affected inventory, return and D/1 stock ledger rows must compare unchanged.
Evidence is private under output/biologic_missing_claims/visit-cleanup-evidence,
with mode 0600 files. Unchanged or newly unsafe candidates remain for review.

The initial inventory audit found 34 fully returned events and one partial
return. Eight matching billing visits remained; all eight passed the guarded
preflight. The three screenshot examples had already-absent original visits.
The guarded transaction was syntax-tested with an impossible delete condition
and changed zero rows. Thirty-nine tests passed, including the deletion guards
and rollback/audit structure. The cleanup report is returned-visit-cleanup.csv;
the durable deletion journal remains available across repeat runs.

Production apply deleted all eight eligible billing headers and charge lines.
Independent native history queries verified each deletion. Full before/after
inventory, return, and D/1 stock ledger snapshots compared unchanged. The three
screenshot examples had native deletion records earlier on October 8 and were
not deleted again. Final repeat apply at 15:02 Eastern found zero remaining
matching full-return visits, deleted zero visits, and changed zero reminders.
The open queue remains 20 reminders / 13 patients. The dated local private
report is reports/returned-visit-deletions-20261008.csv; its corresponding
JSONL journal records the backup names and exact native billing references.

## Current scope and schedule

The original one-time check covered all recorded history of the exact target
codes, which first appear in June/July 2025. Gus subsequently limited open
reminders to the last nine calendar months. The current cutoff is January 8,
2026, inclusive, for October 8's run. It is recomputed daily by calendar-month
subtraction, clamping the day only when the target month is shorter. It is not
a 270-day approximation. The complete dispense-through-day-30 claim window
still applies within that scope.

Pending generated reminders older than the cutoff receive a managed note
explaining the scope closure and native Done details with
done_by='Biologic monitoring window'. The write rechecks the exact source,
patient, prior note, pending detail status, and encoded dispense date against
the cutoff. An old unresolved claim is not mislabeled as a stock return. Done
reminders are retained and never reopened. Explicit --start-date overrides can
produce broader historical dry-run reports; apply is always constrained to at
least the rolling nine-month cutoff.

Current verification: 35 tests passed, including calendar/leap/month-end
cutoffs, inclusive boundary, completion refusal for partial/unknown returns,
and separate guarded window completion. Five full returns and three additional
outside-window reminders are now Done. Native readback confirmed 20 open
reminders for 13 patients and zero older open events. Twenty-two late-claim
events lie within the current monitoring window. Final repeat apply at 14:14
Eastern created, updated, and completed zero reminders.

The full-row completion checks allowed only the native child task_status,
done_date/time/by/by_id and change audit fields, plus managed parent note and
changed_date. Every other field and staff note content was preserved. Private
completion-before/after and window-before/after JSON snapshots are in the
stock-return-20261008 output subdirectory, mode 0600.

Runtime files:

- /opt/ims_router/biologic_missing_claims.py
- /opt/ims_router/biologic_followup.py
- /opt/ims_router/biologic_stock_returns.py
- /opt/ims_router/biologic_return_visit_cleanup.py
- /opt/ims_router/run-biologic-missing-claims.sh
- /opt/ims_router/output/biologic_missing_claims/status.json
- /opt/ims_router/output/biologic_missing_claims/missing-current.csv
- /opt/ims_router/output/biologic_missing_claims/late-claims.csv
- /opt/ims_router/output/biologic_missing_claims/open-reminder-context.csv
- /opt/ims_router/output/biologic_missing_claims/appointment-history.csv
- /opt/ims_router/output/biologic_missing_claims/returned-to-stock.csv
- /opt/ims_router/output/biologic_missing_claims/outside-window.csv
- /opt/ims_router/output/biologic_missing_claims/returned-visit-cleanup.csv
- /opt/ims_router/logs/biologic_missing_claims.log

The existing user's crontab has exactly one new entry at 23:40 daily. Runtime
timezone was verified as Eastern (EDT on installation). A flock prevents overlap.
The previous crontab is privately backed up inside the output directory, and
readback proved all previous cron text preserved. The first scheduled run is
October 8 at 11:40 p.m. Eastern. Manual setup/verification runs are separate.

No email or SMS is sent; delivery is the native IMS My Tasks recipient assignment.
Reports contain patient information and are Git-ignored. Runtime reports use
umask 077 and mode 0600; scheduled logs contain aggregate counts only.

## Initial production result before return and window dispositions

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
earlier valid finding. Subsequent inventory investigation verified a full
return to stock; following Gus's instruction the reminder now records that
disposition and is Done. Do not describe this as a new claim. The history and
source key are retained.

An early pilot waiting for an exclusive table lock was canceled before any
insert. Native autoincrement was then verified live and used instead. No other
database session was interrupted and no database schema was changed.

Private one-time reports are under reports/ locally and under
/opt/ims_router/output/biologic_missing_claims/one-time-20261008 remotely.
