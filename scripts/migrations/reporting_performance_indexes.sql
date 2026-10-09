-- MAT-BOT Reports performance indexes (proposed, additive, not auto-applied).
--
-- This file is intentionally not part of the web request path and was not run
-- against production during the audit. Apply only through the reviewed deploy
-- migration window after taking a backup and checking table sizes/lock time.
-- Every statement is idempotent and changes no student/report row.

CREATE INDEX IF NOT EXISTS idx_learning_activity_source_month_student
ON learning_activity (source, student_id, occurred_at, event_type);

CREATE INDEX IF NOT EXISTS idx_assessment_attempts_source_month_student
ON assessment_attempts (source, student_id, completed_at)
WHERE completed_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_student_accounts_student_provider
ON student_accounts (student_id, provider);
