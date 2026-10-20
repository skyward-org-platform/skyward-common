-- 20261014180000_tsa_004_phase_2_expected_changes.sql
--
-- pipeline.work_item_url.expected_change gains Phase 2's nine values, so
-- each URL on a Phase 2 work item says what it should look like once the
-- fix is done (Adam approved the set 2026-10-13; design in the pipeline
-- repo, docs/superpowers/specs/2026-10-12-p2-expected-impact.md).
--
-- Until now every Phase 2 URL wrote 'other' (3,429 rows live on
-- 2026-10-14). In this pipeline 'other' means "names no measurable end
-- state", so none of them could ever be re-checked. Each value below names
-- a state the next crawl or audit can see:
--
--   made_indexable      200, indexable, no noindex, self canonical
--   canonical_fixed     one canonical, pointing where the rule says
--   sitemap_corrected   in the sitemap if indexable, out of it if not
--   links_added         more internal links in, or a shallower depth
--   links_repointed     no longer linked to (it was a broken target)
--   faster              meets the speed limits the rules state
--   schema_fixed        structured data with no validation errors
--   schema_added        carries every type its page type requires
--   metadata_rewritten  title, meta description and H1 within the rules
--
-- 'other' stays for a check that fits none of these. Existing rows are not
-- touched here: the pipeline rewrites them on the next emit-work-items.
--
-- Same drop-and-re-add as wqa_024, which added 'canonicalized'. Every
-- value live before this migration is kept.

begin;

alter table pipeline.work_item_url
    drop constraint if exists work_item_url_expected_change_check;

alter table pipeline.work_item_url
    add constraint work_item_url_expected_change_check
    check (expected_change = any (array[
        -- Phase 1 (pipeline_012, wqa_024)
        'redirected_away', 'receives_redirect', 'content_rewritten',
        'removed', 'noindexed', 'canonicalized',
        -- Phase 2 (this migration)
        'made_indexable', 'canonical_fixed', 'sitemap_corrected',
        'links_added', 'links_repointed', 'faster',
        'schema_fixed', 'schema_added', 'metadata_rewritten',
        'other'
    ]));

commit;
