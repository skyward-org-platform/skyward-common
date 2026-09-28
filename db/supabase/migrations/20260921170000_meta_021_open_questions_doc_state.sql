-- 20260921170000_meta_021_open_questions_doc_state.sql
-- Where the site's open-questions Doc lives, and what we last saw of it.
--
-- Decided with Adam 2026-09-21. Phase 0 produces two Drive deliverables:
-- the client questionnaire (meta_018 already tracks it) and an OPEN
-- QUESTIONS Doc with two tabs, Internal (Skyward- and research-answerable)
-- and Client. The Doc is internal only: tabs do not have separate sharing,
-- so a client-facing copy would expose the internal tab. Client questions
-- reach the client through the questionnaire.
--
-- The Doc is rewritten from pipeline.open_question on every close-out, but
-- somebody may type an answer into it, and Adam ruled that such an answer
-- COUNTS. So a close-out first reads what changed since our last write,
-- applies the answers, and only then rewrites. That needs the same three
-- pointers the questionnaire has, for the same reasons (meta_018): which
-- document, which revision WE wrote -- so our own rewrite is never read
-- back as somebody's answer -- and which revision we last READ.
--
-- Revision ids are TEXT: Drive's ids are opaque strings. Nullable
-- throughout: a site with no Doc yet is the normal state.

alter table meta.site
    add column if not exists open_questions_doc_id text,
    add column if not exists open_questions_revision_written text,
    add column if not exists open_questions_revision_ingested text;

comment on column meta.site.open_questions_doc_id is
    'Google Doc id of this site''s internal open-questions Doc (tabs: '
    'Internal, Client). Rewritten from pipeline.open_question each '
    'close-out, after any answers typed into it have been read.';

comment on column meta.site.open_questions_revision_written is
    'Drive revision id of the last version WE wrote, so our own rewrite is '
    'never read back as somebody''s answer.';

comment on column meta.site.open_questions_revision_ingested is
    'Drive revision id of the last version whose answers we READ into '
    'pipeline.open_question.';
