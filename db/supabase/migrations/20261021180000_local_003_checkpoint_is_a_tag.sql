-- `checkpoint` is a tag on a field, never a state of a row.
--
-- WHY
-- local_001 took the design's state list unchanged: draft, checkpoint,
-- approved, pushed, retired. The same word also exists as a `tag`
-- inside field_sources, which is where per-field review already lives.
-- Two records of one idea can disagree, and nothing says which wins --
-- the same failure the hours / opening_hours split is spelled out to
-- avoid in site_004. Adam ruled on 2026-10-21: one meaning, one place.
-- The tag stays, the state goes.
--
-- So the row's state now says only what has happened to the row:
-- draft, approved, pushed, retired. Whether a particular field is
-- awaiting somebody's eye is a property of that field and is read from
-- field_sources["<path>"].tag.
--
-- NOTHING TO CONVERT. local.local_content is empty (verified
-- 2026-10-21, 0 rows), so this drops and re-adds the check with no data
-- migration behind it. Had there been rows in 'checkpoint', they would
-- have needed a destination first: this is only free today.
--
-- An applied migration is immutable, which is why this is its own file
-- rather than an edit to local_001.

alter table local.local_content
    drop constraint local_content_state_check;

alter table local.local_content
    add constraint local_content_state_check
        check (state in ('draft', 'approved', 'pushed', 'retired'));

comment on column local.local_content.state is
    'What has happened to this row: draft, approved, pushed, retired. '
    'Whether a field is awaiting review is not a state here, it is '
    'field_sources["<path>"].tag = checkpoint on the row that holds '
    'the field.';

comment on column site.business_location.field_sources is
    'Per-field provenance keyed by column or JSON path: {"telephone": '
    '{source, detail, tag, state, job_id, as_of, conflict}}. source is a '
    'sources[] token; detail the line the workbook prints; tag '
    'automatic|checkpoint|warning|human; state draft|approved|pushed; '
    'conflict {source: value, ...}. Replaced wholesale by upsert, so '
    'the writer reads and merges. checkpoint is a tag only: it was '
    'removed from both state lists in local_003.';
