-- 20260911160000_pipeline_004_drop_change_log.sql
-- Revert 20260911140000. The change log was not asked for and not approved.
--
-- It also put a trigger in the write path of all 23 knowledge-base tables,
-- which is a standing operational risk taken on for a feature nobody
-- requested: a trigger that errors fails the write it is attached to.
do $$
declare
    t text;
begin
    foreach t in array array[
        'brand.brand_term', 'brand.commercial_rules',
        'brand.engagement_context', 'brand.goal', 'brand.identity',
        'brand.intake_keyword', 'brand.lead_rule', 'brand.market',
        'brand.offering', 'brand.payment_rule', 'brand.persona',
        'brand.proof_asset', 'brand.term_exclusion', 'brand.value_input',
        'brand.voice_rule', 'meta.data_access', 'meta.site',
        'meta.site_competitors', 'pipeline.open_question',
        'site.business_location', 'site.crawl_config', 'site.gbp',
        'site.structure'
    ] loop
        execute format('drop trigger if exists log_change on %s', t);
    end loop;
end;
$$;

drop function if exists pipeline.log_change();
drop table if exists pipeline.change_log;
