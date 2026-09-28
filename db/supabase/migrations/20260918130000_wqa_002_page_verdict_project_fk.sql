-- wqa_002: foreign key on wqa.page_verdict.project_id
--
-- page_verdict.project_id carried no foreign key while meta.site.project_id
-- does, so a verdict row could name a project that does not exist and
-- nothing would say so until a join came back short.
--
-- The ON DELETE behaviour deliberately differs from the domain_id foreign
-- key on this same table. domain_id CASCADEs: deleting a site is how a
-- client goes away, and its audit rows go with it. project_id takes NO
-- ACTION, matching meta.site.project_id, so deleting a project is BLOCKED
-- while verdicts still reference it rather than silently destroying the
-- audit history of work that was really done.
--
-- Validates instantly: the table is empty at the time this is applied.

alter table wqa.page_verdict
    add constraint page_verdict_project_id_fkey
    foreign key (project_id) references meta.projects(project_id);
