-- Where the client questionnaire lives, and what we last saw of it.
--
-- WHY
-- The questionnaire is a living document edited from BOTH ends: we fill it
-- before the kickoff meeting from what we hold plus Phase 0 research, the
-- client corrects it and raises their own gaps, and then either side
-- updates it. Every cycle we have to answer "what changed since we last
-- looked" and turn that into knowledge-base updates.
--
-- Three facts had nowhere to live, and all three bit on the first real
-- cycle (2026-09-18, InSoFast):
--
-- 1. THE DOCUMENT ID. Not stored anywhere. It existed only in a Slack
--    message, so a future run could not even find the document it was
--    supposed to diff.
--
--    This stays necessary even though GraphRAG stores the questionnaire's
--    CONTENT, because GraphRAG will not have the COMMENTS. A comment is
--    anchored to a location in the document, is a separate Drive API
--    surface from the body, and is often where the real answer is -- "it's
--    about 12 of us" left on an empty employee-count cell is the answer to
--    that cell, and nothing in the body says it. Adam, 2026-09-18: the
--    link is what lets us check for a revision since our last, view the
--    revision we hold, and read the comments directly.
--
--    Comments have returned ZERO on both InSoFast questionnaires so far,
--    so this path is untested exactly where it is expected to carry real
--    answers. That is a reason to keep the pointer, not to drop it.
--
--    NO SEPARATE URL COLUMN, deliberately. The link is
--    https://docs.google.com/document/d/<questionnaire_doc_id>/edit and
--    the id is what every API call needs. A stored URL would be a second
--    copy of the same fact, free to drift from the first.
--
-- 2. THE REVISION WE LAST WROTE. Our writes land in the SAME revision
--    history as the client's. Without this, a later pass re-reads a cell
--    WE wrote and treats it as client-supplied. A wrong value can be
--    corrected later; a wrong PROVENANCE is invisible, and the knowledge
--    base fills with our own inferences laundered into client statements.
--
-- 3. THE REVISION WE LAST INGESTED. Without it, "what is new" is inferred
--    from timestamps, which is wrong in a way nobody notices.
--
-- WHY THESE CANNOT BE DERIVED, WHICH IS WHAT MAKES THEM NON-OPTIONAL
-- The obvious alternative is to skip the columns and read authorship back
-- from Drive. It does not work: on Paul's fill of the InSoFast
-- questionnaire, revision 155 (2026-09-18T00:05:57Z) carries NO
-- lastModifyingUser at all. The field is simply absent. So Drive
-- attribution is not reliably present even within one document's history,
-- and the marker has to be STORED rather than inferred.
--
-- The same pull showed the second failure mode. That document moved TWICE
-- after Paul sent it -- revisions 155 and 156 -- the later one minutes
-- before his Slack note, because he was folding in details from the
-- kickoff call. An ingest run the previous evening would have taken a
-- pre-kickoff version while believing it current, and nothing in its
-- output would have said so. Both failures are solved by the same two
-- fields.
--
-- ONE DOCUMENT, DELIBERATELY
-- Adam, 2026-09-18: "in the future there will be one document for it.
-- Right now there are two, based on the fact that we've been testing
-- this." So this models the intended end state -- one questionnaire per
-- site -- rather than the testing arrangement.
--
-- Revision ids are TEXT, not integers. Drive's revision ids are opaque
-- strings; they look numeric today and that is not a guarantee.
--
-- Nullable throughout: a site with no questionnaire yet is the normal
-- state, not an error.

alter table meta.site
    add column questionnaire_doc_id text,
    add column questionnaire_revision_written text,
    add column questionnaire_revision_ingested text;

comment on column meta.site.questionnaire_doc_id is
    'Google Doc id of this site''s client questionnaire. One document per '
    'site. Before this existed the id lived only in a Slack message.';

comment on column meta.site.questionnaire_revision_written is
    'Drive revision id of the last version WE wrote. Distinguishes our own '
    'edits from the client''s in a shared revision history, so a later '
    'pass cannot re-ingest our inferences as client statements.';

comment on column meta.site.questionnaire_revision_ingested is
    'Drive revision id of the last version we READ INTO the knowledge '
    'base. "What changed" is measured from here, never from a timestamp: '
    'Drive revisions can carry no lastModifyingUser at all (observed on '
    'InSoFast revision 155), and a document can move between being sent '
    'and being read.';
