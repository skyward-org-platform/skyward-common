-- 20260926120000_wqa_005_dashboard_can_read_verdicts.sql
--
-- Let the operator dashboard READ the audit's verdicts.
--
-- The dashboard runs as `dashboard_ro`, a role that cannot write, and it
-- can already select every table in `pipeline` -- score, work_item,
-- work_item_url, change_report, change_finding -- because the per-person
-- logins migration set default privileges on that schema. It has no USAGE
-- on `wqa` at all, so wqa.page_verdict is invisible to it: the audit's
-- decision per URL, the redirect map and the reviewer's overrides cannot
-- be shown, which is most of what an operator opens Phase 1 to look at.
--
-- READ ONLY, deliberately and permanently. No INSERT, UPDATE or DELETE is
-- granted here and none should be: the dashboard's job is review, edits go
-- through an agent and its update skill, and that is what keeps
-- pipeline.change_log an honest record of who changed what. A dashboard
-- that could write would be able to change a verdict with nothing saying
-- it had.
--
-- Default privileges as well as the table, so a wqa table added later is
-- readable without somebody remembering this file. The alternative is a
-- dashboard page that silently shows nothing the first time the pipeline
-- adds a table, which is this app's most-repeated bug in another guise.
--
-- Grants: this migration IS a grant, so it must be applied as postgres
-- like the others.

grant usage on schema wqa to dashboard_ro;

grant select on all tables in schema wqa to dashboard_ro;

alter default privileges in schema wqa
    grant select on tables to dashboard_ro;
