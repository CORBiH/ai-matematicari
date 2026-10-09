# MAT-BOT Reports performance audit

Status: implementation and isolated before/after measurements complete; production
runtime validation pending access.

Date: 2026-10-09 (Europe/Sarajevo)

## Scope and measurement boundaries

- No production write, migration, report generation, deployment, push, or paid
  OpenAI request was performed.
- The checked-in deployment configuration was inspected. No VPS SSH credential or
  live Nginx configuration is available in this workspace, so VPS CPU, RAM, swap,
  disk, container utilization, restarts, request queueing, and live Nginx upstream
  timings are **NOT MEASURED**.
- A local Turso/libSQL credential file exists, but its JWT is expired (the server
  returned HTTP 401). Live Turso latency, database region, throttling, and plan
  limits are therefore **NOT MEASURED**.
- Browser navigation/performance instrumentation is unavailable. HTTP timings below
  are Flask test-client server times and explicitly do not include DNS, TLS, Nginx,
  browser parsing, painting, or JavaScript execution.
- Baselines use an isolated, synthetic, non-personal local libSQL database with 120
  active students, four grades, current and prior Thinkific snapshots, six sections
  per snapshot, MAT-BOT activity, an assessment, four sessions per student, and
  saved reports for half the students. Measurements are real for that workload, but
  they are not represented as production measurements.

## Architecture and complete request flow (pre-change)

### Entry points and frontend

- `templates/admin_reports.html` is the Reports dashboard. `GET /admin/reports`
  server-renders it. JavaScript only handles Thinkific file classification, roster
  preview, and apply actions; opening the dashboard itself performs no browser API
  fetch.
- `templates/admin_students.html` is the monthly report roster. Search and filters
  are ordinary `GET /admin/reports/students` form submissions. There is no separate
  client-side sorting implementation: ordering comes from the backend/database.
- `templates/admin_student.html` is the individual monthly view/editor. Opening it
  never calls OpenAI.
- Bulk generation JavaScript loops over selected students and sends one
  `/admin/reports/bulk/generate` request per student, sequentially. The backend also
  accepts multiple IDs, but processes those IDs sequentially. This is two layers of
  serialization.

### Backend endpoints

The `matbot.admin_reports` blueprint owns login/logout, dashboard, Thinkific import,
roster classification/preview/apply, monthly student list and CSV, individual
preview/generate/save/PDF, and bulk generate/download routes under
`/admin/reports`.

### Identity and grade authority

- `student_accounts(provider, external_user_id)` links external identities to the
  canonical `students.id`. Thinkific e-mail is normalized before resolution.
- CSV import cannot silently create identities; roster reconciliation is the
  approval boundary.
- `student_current_grades` is the normalized grade authority. The legacy
  `students.grade + grade_confirmed_at + grade_source` triplet is used only as a
  strict compatibility fallback when no normalized rows exist.
- New reports require an active regular `STUDENT` account and a human-confirmed
  grade. `SUPPORT` and `TEST` remain readable where explicitly included but cannot
  generate new reports.

### Thinkific, MAT-BOT, sessions, and report assembly

- Thinkific CSVs are parsed in `thinkific_progress.py`; upload/course detection is
  in `thinkific_upload.py`; roster comparison is in `thinkific_roster.py`; writes
  and reads are in `reporting_db.py`.
- `report_input.build_report_input` synchronously builds one student/month payload:
  profile, session summary, current/prior Thinkific snapshot and sections, then
  MAT-BOT activity and assessment aggregates.
- MAT-BOT aggregation executes separate queries for event counts, active days,
  assessment totals, and lesson outcomes.
- `report_facts.build_ai_facts` removes identity/PII and deterministically assigns
  evidence policy. It does not query the database.

### Individual AI generation

`POST /student/<id>/generate` performs, synchronously in the request thread:

1. full report-input retrieval;
2. account and grade eligibility checks;
3. existing-report lookup;
4. deterministic fact and prompt construction;
5. exactly one `OpenAIPracticeLLM.report_turn` call using the reporting model;
6. schema/content validation without a repair call;
7. report persistence;
8. redirect to the individual view.

OpenAI does not participate in dashboard load, roster load/filter/search, individual
view load, saved-report retrieval, editing, or PDF generation.

### Deployment/runtime path

The documented production path is browser -> Nginx -> one Gunicorn `gthread`
process -> Flask/Python -> Turso and, only for generation, OpenAI -> Flask ->
Nginx -> browser. The Docker default is one worker, eight threads, and a 120-second
timeout; the local `.env` currently says one worker, eight threads, and 180 seconds,
but that is not proof of live production state. More than one worker is currently
unsafe because session state, rate limits, and turn locks are process-local.

`ReportingDatabase` keeps one lazy libSQL connection and holds one process-wide lock
around each database method. Database operations are therefore serialized inside
the Gunicorn process. An OpenAI wait holds a Gunicorn request thread, but does not
hold the database lock. With eight threads, one generation does not inherently
block every unrelated request; enough concurrent blocking generations can still
occupy every thread and queue all traffic.

## Baseline measurements (before changes)

All p50/p95 results below are actual isolated local measurements. Query counts
include schema introspection because those calls cross the same libSQL connection in
production.

| Operation | Workload | p50 | p95 | SQL executions (p50) | DB execute time p50 | Response bytes p50 |
|---|---:|---:|---:|---:|---:|---:|
| Reports dashboard, cold | 120 students, 1 run | 56.06 ms | NOT MEASURED | 78 | 17.14 ms | 15,969 |
| Reports dashboard, warm | 120 students, n=12 | 21.68 ms | 29.80 ms | 72 | 15.43 ms | 15,969 |
| Monthly roster, cold | 120 students, 1 run | 653.54 ms | NOT MEASURED | 1,400 | 539.97 ms | 170,556 |
| Monthly roster, warm | 120 students, n=8 | 487.05 ms | 584.72 ms | 1,393 | 421.03 ms | 170,556 |
| Search returning one student | n=12 | 22.92 ms | 25.11 ms | 84 | 18.71 ms | 11,786 |
| Grade filter returning 30 students | n=12 | 122.59 ms | 141.17 ms | 403 | 104.22 ms | 50,968 |
| Saved student profile page | n=12 | 12.95 ms | 14.96 ms | 81 | 8.96 ms | 10,733 |
| Unsaved student profile page | n=12 | 12.12 ms | 13.39 ms | 81 | 8.72 ms | 5,946 |
| Monthly input aggregation, one student | n=20 | 2.98 ms | 3.94 ms | 11 | 2.67 ms | n/a |
| Existing report retrieval | n=20 | 0.86 ms | 1.21 ms | 8 | 0.63 ms | n/a |
| Individual deterministic preparation | n=20 | 4.00 ms | 5.38 ms | 11 | 3.51 ms | n/a |

AI baseline calls were not paid calls. A deterministic fake model with a measured
50 ms wait per call isolated orchestration behavior:

| Operation | Workload | p50 | p95 | SQL executions p50 | DB time p50 | Throughput |
|---|---:|---:|---:|---:|---:|---:|
| Individual generation | 1 student, n=8 | 59.27 ms | 71.05 ms | 29 | 4.57 ms | 16.87 students/s |
| Bulk generation | 10 students, n=5 | 613.35 ms | 642.91 ms | 263 | 53.40 ms | 16.30 students/s |

The fake-model figures measure application serialization only and must not be read
as OpenAI latency.

## Query-plan evidence

`EXPLAIN QUERY PLAN` on the representative local schema found:

- monthly `learning_activity` aggregation uses the unique `(source, event_key)`
  autoindex by `source`, then a temporary B-tree for grouping; it lacks an index
  beginning with `student_id` and the time range;
- monthly `assessment_attempts` aggregation similarly uses only the unique
  `(source, external_attempt_id)` autoindex by `source`;
- monthly `student_sessions` correctly uses
  `idx_student_sessions_student_date(student_id, session_date)`.

Indexes for the first two paths require a versioned, additive migration and must not
be applied to production during this audit.

## Likely bottlenecks established before modification

1. **Roster N+1/full-object overfetch — confirmed locally.** The table needs a
   handful of summary fields, but `_filtered_report_rows` constructs the full
   report payload for every student, including sessions, all Thinkific sections,
   prior-month section deltas, active-day counts, and lesson outcomes. Query count
   grows linearly (1,393 SQL executions for 120 students).
2. **Full schema diagnostic in every web request — confirmed locally.**
   `schema_state()` invokes the operator-oriented `ReportingDatabase.check()`, which
   introspects every reporting table and validates every schema version. It causes
   72 SQL executions even on a warm dashboard request.
3. **Repeated monthly-report schema introspection — confirmed locally.** Reading a
   single existing report costs eight SQL executions because compatibility checks
   repeat on each read.
4. **Missing activity/assessment access-path indexes — confirmed by EXPLAIN.**
   Cost will grow with total historical MAT-BOT rows, not only the target student.
5. **Sequential bulk architecture — confirmed by code and isolated timing.** Both
   browser and server serialize independent OpenAI waits. There is no transient
   retry/backoff policy.
6. **Remote database round-trip amplification — contributing factor by
   architecture, production magnitude not measured.** A remote service turns each
   unnecessary SQL execution into a network round trip. This does not establish a
   Turso service fault.
7. **OpenAI wait — necessarily dominant during actual generation, magnitude not
   measured.** The code makes one blocking external call per newly generated
   report. No paid benchmark was authorized.
8. **Large roster HTML — contributing factor.** The synthetic 120-row response is
   170,556 bytes before transport compression. Browser parse/render time is not
   measured.
9. **VPS, Nginx, and live Gunicorn saturation — insufficient evidence.** Repository
   configuration alone cannot establish runtime saturation or justify more CPU/RAM.

## Implemented safe changes

- replaced per-request exhaustive schema diagnostics with a narrow read-only
  readiness query that returns exactly what the web guard currently consumes;
- fetches roster summary fields in a bounded number of set-based queries and leaves
  full aggregation for the individual page/generation path;
- caches successful monthly-report schema capability checks for the life of the
  libSQL connection;
- logs PII-free dashboard, roster, AI, persistence, and bulk timing fields plus
  payload sizes, call counts, row counts, and concurrency;
- adds locally proven composite indexes through an idempotent SQL script, with
  production application deferred to deployment review;
- introduces conservative, configurable bounded server-side bulk concurrency around
  independent report generations, with per-student isolation and bounded transient
  retry, then remove the browser's one-request-per-student serialization;
- preserves all current authorization, eligibility, grade, report validation,
  persistence, and UI semantics.

The individual monthly input was also reduced from eleven warm SQL executions to
five by fetching both Thinkific periods/sections in one query and all MAT-BOT
monthly aggregates in one query. Bulk requests are sent in two-student batches and
run with concurrency two by default. A transient failure may be retried once only
when a complete retry still fits the 50-second bulk AI deadline. A process-local
in-flight guard prevents duplicate concurrent generation for the same
student/month. No new service or dependency was introduced.

## Before/after benchmark

Same machine, interpreter, synthetic dataset, and measurement harness were used.
Percent improvements use p50 wall time; positive means faster. Local libSQL has no
network RTT, so query-count reductions are the more transferable result for Turso.

| Operation | Before | After | Improvement |
|---|---:|---:|---:|
| Reports page cold server load | 56.06 ms | 38.04 ms | 32.1% |
| Reports page warm server load | 21.68 ms | 8.02 ms | 63.0% |
| Student roster API/server render (120 rows) | 487.05 ms / 1,393 SQL | 56.76 ms / 6 SQL | 88.3%; 99.6% fewer SQL calls |
| Student search (one match) | 22.92 ms / 84 SQL | 17.46 ms / 6 SQL | 23.8% |
| Student grade filter (30 matches) | 122.59 ms / 403 SQL | 19.38 ms / 6 SQL | 84.2% |
| Existing report retrieval | 0.86 ms / 8 SQL | 0.16 ms / 1 SQL | 81.4% |
| Monthly aggregation | 2.98 ms / 11 SQL | 3.64 ms / 5 SQL | -22.1% local wall time; 54.5% fewer SQL calls |
| Individual report preparation | 4.00 ms / 11 SQL | 3.67 ms / 5 SQL | 8.3% |
| Individual generation, 50 ms fake API | 59.27 ms | 57.77 ms | 2.5% |
| Bulk generation, ten students, 50 ms fake API each | 613.35 ms (16.30/s) | 298.83 ms (33.46/s) | 51.3%; throughput +105.3% |
| Turso query latency | NOT MEASURED | NOT MEASURED | Expired configured JWT |
| Application CPU usage | NOT MEASURED | NOT MEASURED | No comparable process profile captured |
| Application memory usage | NOT MEASURED | NOT MEASURED | No comparable process profile captured |

Additional p95 results: dashboard warm 29.80 -> 8.93 ms; roster warm 584.72 ->
60.87 ms; grade filter 141.17 -> 20.41 ms; existing report read 1.21 ->
0.18 ms; monthly aggregation 3.94 -> 4.33 ms; individual preparation 5.38 ->
4.86 ms; isolated individual generation 71.05 -> 69.92 ms; isolated bulk
642.91 -> 370.62 ms.

The local monthly aggregate became 0.66 ms slower because one richer SQL statement
does more work inside SQLite, while measured database execution time fell from
2.67 to 2.21 ms and round trips fell from eleven to five. No claim is made about
the unmeasured remote result; this trade is expected to favor a remote database,
but production timing must confirm it.

Roster response size was essentially unchanged (170,556 -> 170,758 bytes). The
optimization removes backend work and round trips, not rows or interface content.
Browser parse/render time and response serialization as a separate stage remain
NOT MEASURED; route wall time includes Jinja rendering and response construction.

## Root-cause verdict

Ranked by evidence and impact:

| Area | Classification | Evidence and confidence |
|---|---|---|
| A. Python/backend implementation | **CONFIRMED BOTTLENECK** | Full report assembly per roster row; removing it cut local roster p50 88.3%. High confidence. |
| B. Frontend architecture | **CONTRIBUTING FACTOR** | Browser serialized one request per student; bounded batching doubled isolated throughput. High confidence for bulk, browser rendering impact unmeasured. |
| C. SQL queries | **CONFIRMED BOTTLENECK** | 1,393 -> 6 roster executions; EXPLAIN found missing month access paths. High confidence. |
| D. Turso infrastructure | **INSUFFICIENT EVIDENCE** | Credential is expired; no live RTT, region, throttling, or plan metrics. High confidence in the limitation. |
| E. Hetzner VPS CPU | **INSUFFICIENT EVIDENCE** | No SSH/runtime metrics. |
| F. Hetzner VPS RAM | **INSUFFICIENT EVIDENCE** | No SSH/runtime metrics. |
| G. Gunicorn configuration | **CONTRIBUTING FACTOR** | Blocking work uses request threads; one process has eight threads and one serialized DB connection. Live saturation is unmeasured. Medium confidence. |
| H. Nginx configuration | **INSUFFICIENT EVIDENCE** | Live config/upstream timings unavailable. |
| I. OpenAI API latency | **INSUFFICIENT EVIDENCE** for magnitude | Exactly one external wait is on every new-report critical path, but no paid call was authorized. The fake delay only proves orchestration behavior. |
| J. Network latency | **INSUFFICIENT EVIDENCE** | No valid Turso measurement and no VPS path measurement. |
| K. Bulk architecture | **CONFIRMED BOTTLENECK** | Sequential baseline 16.30/s; bounded concurrency reached 33.46/s. High confidence. |

The application/query design is the first problem to fix and was large enough to
explain severe remote behavior without assuming weak hardware. Turso's remote RTT
would amplify the defect, but that makes excessive calls an application problem,
not evidence that Turso itself is slow.

## Hetzner and Gunicorn assessment

1. **Is the VPS underpowered?** Unknown; no CPU, steal/throttle, RAM, swap, OOM,
   disk, network, Docker utilization, or restart metrics were available.
2. **Are resources saturated?** Unknown for the same reason.
3. **Does Gunicorn contribute?** Architecturally yes: OpenAI waits occupy a thread
   and all database methods share one connection lock. One report does not block
   seven other threads, but eight concurrent blocking requests can queue all app
   traffic. Runtime saturation is not proven.
4. **Would more CPU/RAM help?** There is no evidence to buy either. More CPU does
   not remove Turso round trips or OpenAI wait; more RAM does not fix N+1 queries.
   This is an inference from the architecture, not a VPS measurement.
5. **Can the current VPS handle the optimized workload?** Not established. The
   correct next step is to deploy under change control, collect the new PII-free
   timings plus `docker stats`/host metrics, and decide from p50/p95 saturation.

Do not increase `WEB_CONCURRENCY`: process-local sessions, rate limits, turn locks,
and the generation in-flight guard rely on the documented single-process design.
The default bulk concurrency of two is intentionally below the eight Gunicorn
threads. No Nginx change is recommended without its actual config/timings.

## Turso assessment

1. Turso service latency as a cause is **not established**.
2. Query design and excessive calls are the primary confirmed database-side issue.
3. Excessive remote round trips were certainly present (up to 1,393 executions for
   the synthetic roster), although their production time share is unmeasured.
4. Database region/proximity is unknown.
5. A Turso plan upgrade is not justified by current evidence.
6. The local result proves large gains are possible without an upgrade.
7. Migration away from Turso is not justified.

After obtaining a fresh read-only credential, measure lazy connect + first query,
30 warm `SELECT 1` calls, representative roster/full-report queries, and controlled
concurrency from the VPS. Record median/p95 and database region before revisiting
plan or region decisions.

## OpenAI assessment

No paid OpenAI benchmark was run. Consequently, the share of real generation time
attributable to OpenAI is unknown. Instrumentation now logs safe token usage,
external latency, total AI stage time, deterministic data/fact time, persistence
time, total generation time, and call count without prompt, report, student name,
student ID, credential, or token content.

The 50 ms fake-call experiment showed why concurrency matters: single-report time
barely changed because the artificial external wait dominated, while two-way bulk
concurrency doubled throughput. Keep the current reporting model until a paid,
quality-scored comparison is explicitly authorized; changing models merely for
speed would violate the quality requirement.

## Changed files

- `matbot/reporting_db.py`: one-query web readiness, set-based roster summaries,
  paired Thinkific retrieval, combined MAT-BOT aggregation, connection-scoped
  successful schema-capability cache.
- `matbot/report_input.py`: uses the paired current/prior Thinkific read.
- `matbot/admin_reports.py`: lazy roster data, safe stage timing, bounded bulk
  executor, retry deadline/backoff, duplicate in-flight guard, sanitized errors.
- `matbot/parent_report.py`: transient classification and PII-free AI timing.
- `matbot/config.py` and `.env.example`: bounded bulk concurrency/batch/retry/deadline
  settings; defaults require no production env change.
- `templates/admin_students.html`: batches two selected students per request while
  preserving status, retry, selection, edit, and download behavior.
- `scripts/migrations/reporting_performance_indexes.sql`: optional additive,
  idempotent indexes; not applied to production.
- `tests/test_reports_performance.py`, `tests/test_admin_reports.py`, and
  `tests/test_admin_parent_report.py`: query ceilings, EXPLAIN proof, lazy roster,
  concurrency, retry/permanent failure, isolation, and duplicate prevention.
- this audit document.

## Tests and untested scenarios

- Final Reports-focused regression run: 130 passed in 41.77 s, including the
  duplicate in-flight guard, bulk concurrency/retry behavior, query ceilings,
  and report-input model coverage.
- Full suite: 11,100 passed, 29 failed, 4 skipped in 451.58 s. None of the failures
  implicated Reports code. Twenty-eight were Windows environment/shell/sandbox
  failures (`sh` absent or Windows paths passed to WSL bash; Node initially denied
  parent-directory `lstat`). The remaining existing libSQL four-thread identity
  race passed immediately when rerun outside the loaded full suite.
- Escalated rerun: frontend DOM and the identity concurrency test passed; 27
  pre-push/deploy shell tests remained unexecutable because a compatible POSIX
  `sh` with Windows path translation is unavailable.
- Production Turso, VPS, Docker, Nginx, Gunicorn queueing, real OpenAI, browser
  navigation/render, CPU, and memory remain untested.

## Deployment and rollback

No deployment or push occurred.

The application changes require no schema migration. The proposed index script is
optional and must be handled separately:

1. inspect production table sizes and take the normal database backup;
2. apply during a low-traffic window because index creation reads tables and can
   increase write latency/locking temporarily;
3. run `EXPLAIN QUERY PLAN` and the read-only reporting diagnostic;
4. monitor write latency and database size.

The three indexes add storage and write amplification. They can be rolled back by
dropping only those named indexes; application correctness does not depend on them.
App rollback is a normal Git revert/redeploy. Successful schema caching is scoped
to one connection and disappears on reconnect/restart. Bulk behavior can be rolled
back without code by setting concurrency and request size to one and retries to
zero, though the frontend batch size is rendered from the same server setting.

## Cost/benefit and final recommendation

- Keep the current Hetzner VPS for now, conditionally: measure it after deployment
  before spending. This is a recommendation to defer an unsupported upgrade, not a
  claim that the VPS is proven sufficient.
- Keep the current Turso plan and database architecture pending valid VPS-to-Turso
  measurements. Do not migrate databases or move region without measured benefit.
- Keep the current OpenAI reporting model pending an authorized latency/quality
  benchmark.
- The implemented changes add no service and no fixed monthly cost. Indexes consume
  some database storage/write work. Concurrency changes burst shape, not normal call
  count. A transient bulk failure can add at most one attempt and only within the
  deadline, so rare retry cost may rise; permanent/validation/auth failures do not
  retry.
- Current hosting prices and plan limits are unknown because the live plans and
  account tiers were not available; no price was invented.

**Final verdict:** the main confirmed problem was application/query architecture,
especially roster overfetch, repeated schema introspection, and sequential bulk
generation. Turso's network nature amplified it, but Turso service quality is not
proven to be a problem. Hetzner CPU/RAM and Nginx are unmeasured, and OpenAI is an
unmeasured external critical path only during generation. Deploy the code first,
collect the new timings and host/database metrics, then consider infrastructure.
There is currently no evidence-based reason to spend more on VPS, Turso, a new
database, Redis/Celery, or a different model.
