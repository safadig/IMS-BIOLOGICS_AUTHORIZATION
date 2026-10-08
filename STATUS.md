# Status - October 8, 2026

The claim window is now **60 complete days**, with reminders first eligible on
day 61. The same 11:40 p.m. Eastern cron and rolling nine-calendar-month scope
remain active. October 8 apply at 15:28 Eastern updated 18 pending reminders
to the new wording and policy context, and deferred five premature reminders
using native parent/detail due dates and detail show dates. Current queue:
13 due reminders for eight patients, plus five pending reminders scheduled for
October 10, October 12 (two), and November 2 (two). Staff had independently
completed two reminders since the prior 20-open snapshot; those were preserved.

A pending generated reminder is completed with a distinct matching-claim reason
when its same-patient, same-drug linked claim appears. This prevents a deferred
reminder becoming due after its claim requirement was satisfied. Full return
and outside-window completion keep their existing reasons and precedence;
returned zero-dollar dispense visit cleanup continues independently. Source
keys retain BIO_NO_CLAIM30 as a stable identifier for duplicate suppression.

Forty-six tests passed. Full native before/after readback verified 18 parent
updates and five pending detail date updates, with only intended note/title/date
and native change-audit fields affected. All staff text outside the generated
paragraph and managed section, recipients, patient linkage, and Done records
were preserved. Windows CR/CRLF staff notes are now preserved exactly through
XML retrieval and SQL quoting; the first guarded attempt changed zero rows
because its old-note guard correctly rejected normalized line endings.
Private full snapshots and previous deployed source copies are under
output/biologic_missing_claims/window60-20261008. Repeat apply at 15:29 Eastern
created, updated, deferred, completed, and deleted zero records. The live
60-day report has 13 still-missing dispense events for eight patients and three
late-claim events. Local latest reports are refreshed and remain Git-ignored.

The following paragraphs retain earlier October 8 verification records; their
counts and 30-day language describe the policy before the 60-day extension.

Nightly biologic missing-claim monitoring is deployed and active at 11:40 p.m.
Eastern on ims-referrals. Current scope: the last nine calendar months of exact
target-code dispenses. On October 8 the inclusive cutoff is January 8, 2026.
The cutoff rolls forward daily; the complete day-30 claim window still applies.

Current native queue: 20 open reminders for 13 patients, with zero dispense
dates older than the cutoff. Following Gus's instructions, five verified
full-return reminders and three additional outside-window reminders are Done.
Their native completion audit reasons distinguish returns from scope closure.
Nightly runs apply these same guarded completion rules to pending generated
reminders. Partial returns and uncertain records remain for review.

Thirty-nine tests passed. Full native before/after snapshots verified that only
managed note context and the intended Done/audit fields changed. Routing,
patient linkage, all other native fields, and staff text remained intact. Final
repeat apply at 14:14 Eastern made zero inserts, updates, or completions. There
are 22 late-claim events within the current nine-month scope. Private return and
outside-window reports retain the five return and three scope dispositions.

Returned zero-dollar dispense visit cleanup is deployed in the same nightly
runner. The full exact-code inventory history has 34 verified full-return
events and one partial return. Eight eligible dispense-only billing visits
were deleted, with native header/detail deletion history independently verified
and affected inventory/return/D-and-1 ledger rows unchanged. The three screenshot
examples had already been deleted earlier today; their native history was read
back. Partial returns and unsafe mixed/claimed/paid/clinical-linked visits are
excluded from deletion. Full private before/after snapshots and a durable
deletion journal are under visit-cleanup-evidence; the dated local CSV remains
Git-ignored. Repeat apply at 15:02 Eastern found zero remaining fully returned
visits and performed zero deletions or reminder changes. Queue remains 20 / 13.

The following records describe the earlier installation and context-only checks.

28 IMS reminders for 20 patients were created and verified, assigned to Ghassan
Safadi and Rachel Clark. All 28 now include appointment follow-up and the last
matching biologic service office/date, with native patient name/number display
and patient reference flag. Nineteen focused tests passed. Repeat apply created
zero duplicates and made zero further context updates. Existing cron entries
were preserved and the new entry read back once.

October 8 follow-up repair verified all 28 full parent rows and all 28 child
rows: only patient display/reference flag, managed note context, and change
timestamp were altered. Recipients, priority, status, assignment details, and
staff-authored note content were preserved. Open reminders refresh the managed
context nightly; completed reminders are not reopened or updated.

Return-to-stock checking is also deployed. The exact inventory dispense,
dispense-linked return adjustment, native adjusted quantity, and stock ledger
must agree before a full return suppresses a new missing-claim reminder.
Partial or unknown returns remain actionable. All 28 open reminder notes now
include return status; five show verified full returns. All other native fields,
all child rows, and staff-authored note text were preserved. Twenty-nine tests
passed, live SQL guards matched all 28 classifications, and repeat apply made
zero inserts or updates.

The all-history one-time check found 20 patients / 28 still-missing events,
including 19731. All findings have verified reminders. Fifty other
historical events had late matching claims and are separately reported.

Before return checking: 27 source events for 19 patients. Patient 19731's
June 10 dispense row disappeared during installation; no subsequent J2357 claim
was found. Its original reminder remains for staff review/disposition. Thus 28
verified reminders exist. Inventory records now verify that this earlier event
was fully returned to stock. Four other still-present source events also have
verified full returns. Latest actionable report: 23 events for 16 patients.
The five return dispositions were subsequently marked Done as authorized above.
Private returned-to-stock.csv retains them, including the earlier finding whose
billing dispense row disappeared. Outside-window.csv retains the three scope
closures. Historical findings and source keys are retained; completed reminders
are never reopened or rewritten.

See docs/biologic-missing-claims-20261008.md for exact semantics, runtime paths,
verification and private report locations. Existing insurance-change monitoring
continues separately under BIO_INS_CHANGE_PA.
