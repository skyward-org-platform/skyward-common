-- 20260910100000_brand_004_plural_sources.sql
-- One row can come from more than one document.
--
-- Every brand and site table carried `source text not null` -- one
-- value, one origin. That held while a row came from one place. It stops
-- holding at step 4, where research is synthesised WITH what step 3
-- already stored, so a merged row genuinely has two origins and the
-- column can only name one of them.
--
-- It is already false today. BusBank's personas exist in both the intake
-- sheet and the brand docs under different names; merging those into one
-- row is the right answer and there is nowhere to record that it came
-- from both.
--
-- pipeline.open_question has used `sources text[]` since it was created,
-- for the same reason: a contradiction has two sources by definition.
-- This brings the other tables in line.
--
-- NOT applied to meta.site, meta.data_access or meta.site_competitors.
-- Their `source` is provenance of record -- which process wrote the row,
-- 'phase_0_onboarding' or 'client_domains_backfill' -- which is
-- genuinely singular. They also hold real production data across every
-- client and are written by skyward-common's MetaClient, so changing
-- them costs a cross-repo release for no gain.
--
-- source is not part of any natural key, so no constraint is affected.

alter table brand.market add column sources text[];
update brand.market set sources = array[source];
alter table brand.market alter column sources set not null;
alter table brand.market
    add constraint market_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.market drop column source;

alter table brand.engagement_context add column sources text[];
update brand.engagement_context set sources = array[source];
alter table brand.engagement_context alter column sources set not null;
alter table brand.engagement_context
    add constraint engagement_context_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.engagement_context drop column source;

alter table brand.identity add column sources text[];
update brand.identity set sources = array[source];
alter table brand.identity alter column sources set not null;
alter table brand.identity
    add constraint identity_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.identity drop column source;

alter table brand.commercial_rules add column sources text[];
update brand.commercial_rules set sources = array[source];
alter table brand.commercial_rules alter column sources set not null;
alter table brand.commercial_rules
    add constraint commercial_rules_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.commercial_rules drop column source;

alter table brand.payment_rule add column sources text[];
update brand.payment_rule set sources = array[source];
alter table brand.payment_rule alter column sources set not null;
alter table brand.payment_rule
    add constraint payment_rule_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.payment_rule drop column source;

alter table brand.goal add column sources text[];
update brand.goal set sources = array[source];
alter table brand.goal alter column sources set not null;
alter table brand.goal
    add constraint goal_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.goal drop column source;

alter table brand.value_input add column sources text[];
update brand.value_input set sources = array[source];
alter table brand.value_input alter column sources set not null;
alter table brand.value_input
    add constraint value_input_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.value_input drop column source;

alter table brand.offering add column sources text[];
update brand.offering set sources = array[source];
alter table brand.offering alter column sources set not null;
alter table brand.offering
    add constraint offering_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.offering drop column source;

alter table brand.persona add column sources text[];
update brand.persona set sources = array[source];
alter table brand.persona alter column sources set not null;
alter table brand.persona
    add constraint persona_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.persona drop column source;

alter table brand.lead_rule add column sources text[];
update brand.lead_rule set sources = array[source];
alter table brand.lead_rule alter column sources set not null;
alter table brand.lead_rule
    add constraint lead_rule_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.lead_rule drop column source;

alter table brand.proof_asset add column sources text[];
update brand.proof_asset set sources = array[source];
alter table brand.proof_asset alter column sources set not null;
alter table brand.proof_asset
    add constraint proof_asset_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.proof_asset drop column source;

alter table brand.voice_rule add column sources text[];
update brand.voice_rule set sources = array[source];
alter table brand.voice_rule alter column sources set not null;
alter table brand.voice_rule
    add constraint voice_rule_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.voice_rule drop column source;

alter table brand.brand_term add column sources text[];
update brand.brand_term set sources = array[source];
alter table brand.brand_term alter column sources set not null;
alter table brand.brand_term
    add constraint brand_term_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.brand_term drop column source;

alter table brand.intake_keyword add column sources text[];
update brand.intake_keyword set sources = array[source];
alter table brand.intake_keyword alter column sources set not null;
alter table brand.intake_keyword
    add constraint intake_keyword_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.intake_keyword drop column source;

alter table brand.term_exclusion add column sources text[];
update brand.term_exclusion set sources = array[source];
alter table brand.term_exclusion alter column sources set not null;
alter table brand.term_exclusion
    add constraint term_exclusion_sources_not_empty
        check (cardinality(sources) > 0);
alter table brand.term_exclusion drop column source;

alter table site.structure add column sources text[];
update site.structure set sources = array[source];
alter table site.structure alter column sources set not null;
alter table site.structure
    add constraint structure_sources_not_empty
        check (cardinality(sources) > 0);
alter table site.structure drop column source;

alter table site.crawl_config add column sources text[];
update site.crawl_config set sources = array[source];
alter table site.crawl_config alter column sources set not null;
alter table site.crawl_config
    add constraint crawl_config_sources_not_empty
        check (cardinality(sources) > 0);
alter table site.crawl_config drop column source;

alter table site.business_location add column sources text[];
update site.business_location set sources = array[source];
alter table site.business_location alter column sources set not null;
alter table site.business_location
    add constraint business_location_sources_not_empty
        check (cardinality(sources) > 0);
alter table site.business_location drop column source;

alter table site.gbp add column sources text[];
update site.gbp set sources = array[source];
alter table site.gbp alter column sources set not null;
alter table site.gbp
    add constraint gbp_sources_not_empty
        check (cardinality(sources) > 0);
alter table site.gbp drop column source;

-- cardinality > 0 rather than a default of '{}': the old column was NOT
-- NULL, so every row had to say where it came from. An empty array would
-- quietly give that up.
