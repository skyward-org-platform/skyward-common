-- 20260921130000_wqa_003_page_verdict_sv_keyword.sql
--
-- The best-search-volume keyword trio, missing from page_verdict since
-- wqa_001. The table carries the best-TRAFFIC-volume trio but not this
-- one, which was an omission rather than a choice:
--
--   * the triage rules read best_sv_kw_rank and best_sv_kw_sv to decide
--     a page has "keyword rankings with SV", so a verdict whose logic
--     cites them could not be justified from its own row;
--   * the workbook shows all three.
--
-- The spec builds the deliverable from page_verdict, not from BigQuery,
-- so the alternative -- joining these back from wqa_output at delivery --
-- would have met the letter of a workbook and broken the rule behind it.
--
-- Types mirror best_tv_keyword / best_tv_kw_sv / best_tv_kw_rank exactly.
-- Nullable: a page with no ranking keyword has none. Instant: the table
-- holds no rows yet.

alter table wqa.page_verdict
    add column best_sv_keyword  text,
    add column best_sv_kw_sv    integer,
    add column best_sv_kw_rank  integer;
