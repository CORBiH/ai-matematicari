# Student name-only backfill

`matbot.student_name_backfill` fills a missing `students.display_name` from the
Thinkific columns `First Name`, `Last Name`, and `Email`. It matches only an
existing `student_accounts` row whose provider is `thinkific_email` and whose
`external_user_id` equals the canonically normalized email.

The command is intentionally separate from the Thinkific progress importer.
It does not accept a report month and cannot write progress data.

## Safety guarantees

- Preview is the default and performs only `SELECT` queries.
- Apply requires both `--apply` and the exact `plan_id` from preview.
- Apply re-reads the CSV and current database state, then recomputes the plan.
- Every update is guarded by student ID, exact old name, provider, and email.
- All updates are one transaction; any failed guard rolls back the whole batch.
- Only `students.display_name` is in the `UPDATE` statement.
- Existing non-empty names are never overwritten.
- Unmatched rows never create students or accounts.
- `updated_at`, `last_seen_at`, grade, activity, assessments, sessions, monthly
  reports, progress imports, snapshots, and section progress are untouched.
- No migration or schema change is part of this operation.

Required headers are exactly:

```text
First Name,Last Name,Email
```

Unrelated extra columns are ignored. Invalid emails, blank names, control
characters, names longer than 120 characters, duplicate normalized emails,
malformed CSV, and multiple input identities resolving to one student block
apply.

## Preview

From the production application directory, with the CSV on a protected host
path:

```bash
docker compose exec -T matbot \
  python -m matbot.student_name_backfill --csv - \
  < /secure/path/thinkific-names.csv
```

`--csv -` streams the bytes through standard input, so the CSV is not copied
into or retained by the container. A direct file path is also supported for
local testing:

```bash
python -m matbot.student_name_backfill --csv FILE.csv
```

Example preview:

```text
NAME-ONLY BACKFILL PREVIEW - NO DATABASE WRITES
row | classification | student_id | email | current | proposed | reason
2 | WOULD_UPDATE | 35 | a***@example.com | <NULL> | "Ana Anić" | -
3 | SKIP_EXISTING_NAME | 12 | e***@example.com | "Existing" | "Emir" | name_already_present
4 | UNMATCHED | - | n***@example.com | <NULL> | "New Student" | account_not_found

total rows: 3
matched: 2
would update: 1
already named: 1
unmatched: 1
errors: 0
duplicates: 0
apply allowed: YES
plan_id: name-backfill-v1-...
```

Do not continue if the student IDs, existing names, proposed names, unmatched
rows, or totals are unexpected. `ERROR` and `DUPLICATE` rows produce
`apply allowed: NO` and no `plan_id`.

## Apply

Review the preview, then run the same CSV again with both apply arguments:

```bash
docker compose exec -T matbot \
  python -m matbot.student_name_backfill --csv - \
  --apply --confirm-plan 'EXACT_PLAN_ID_FROM_PREVIEW' \
  < /secure/path/thinkific-names.csv
```

The command refuses before writing if the file, identity mapping, current name,
proposed name, classification, database target, or any other plan input differs
from preview. A successful result ends with:

```text
apply result: SUCCESS
updated: 5
```

## Recovery

- `plan_mismatch`: run a new preview and review the changed plan. Never reuse
  or bypass the old confirmation.
- `name_backfill_guard_failed`: another writer changed a target after the plan
  was computed. The entire batch was rolled back; run preview again.
- Database/network failure: the command reports only a structural error code.
  Run preview again to establish the current state before any further apply.
- Input error: correct the CSV; do not remove validation or edit the plan ID.

The operation is idempotent. After success, a new preview classifies the filled
names as `SKIP_EXISTING_NAME` and reports `would update: 0`.

## Production verification

After apply:

1. Run the same preview again and require `would update: 0` for the applied rows.
2. Run the approved read-only production verification query for the relevant
   student IDs.
3. Confirm `display_name` is filled while grade, timestamps, account linkage,
   activity/assessment/session counts, snapshot counts, and monthly reports are
   unchanged.
4. Inspect the admin reporting page only after the read-only database checks.

## PII warning

The CSV and preview contain student names and email-derived identifiers. Keep
the source file outside the repository, use a restricted terminal, and do not
paste output into public tickets or CI logs. Raw emails and names are never
written to application logs; preview intentionally shows proposed/current names
to the authorized operator and masks email addresses.
