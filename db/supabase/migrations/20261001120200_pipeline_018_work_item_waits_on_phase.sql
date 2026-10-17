-- 20261001120200_pipeline_018_work_item_waits_on_phase.sql
--
-- Which later phase a work item WAITS ON, the mirror of blocks_phase.
-- Phase 2's "Phase 3 Dependent" and "Fix Now, Revisit" findings cannot be
-- finished until Phase 3 assigns keywords. Phase 3 reads this to find and
-- close them itself; nothing in Phase 2 blocks Phase 3 (Adam, 2026-09-30).
--
-- One nullable column. Phase 1 never writes or reads it, so its rows stay
-- null and nothing Phase 1 does changes. Text, like blocks_phase, so a
-- phase is named the way the milestone names it.

alter table pipeline.work_item
    add column if not exists waits_on_phase text;

comment on column pipeline.work_item.waits_on_phase is
    'The phase whose output this item needs before it can be finished '
    '("3" for keyword-dependent technical work). Null when nothing.';
