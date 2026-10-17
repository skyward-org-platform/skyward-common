-- 20261006120000_keyword_analysis_005_directory_domains_shared_urls.sql
--
-- Directory sites, and the shared URLs and intent on each cluster.
--
-- WHY (Adam and Paul, 2026-10-05)
-- The cluster list shows the URLs that recur across a cluster's top-10
-- results, twice: all of them, and with directory and user-content sites
-- (Yelp, YouTube, Wikipedia...) taken out. Content enrichment will skip the
-- same sites as sources. Which sites count is a STARTER LIST shared by
-- every client (domain_id NULL), plus per-client additions and removals
-- (domain_id set). Every edit goes through pipeline.change_log, as Brand
-- DNA does, whether an operator or a maintenance-lane agent makes it.
--
-- The cluster columns are cache, rebuilt with every p3 workbook build, so
-- they are not change-logged (no more than the rest of keyword_analysis).

create table keyword_analysis.directory_domain (
    directory_domain_id bigserial primary key,
    -- NULL: the starter list, for every client. Set: this site only.
    domain_id       bigint references meta.site(domain_id) on delete cascade,
    -- Registrable domain, lower case, no www: 'yelp.com'.
    domain          text not null
                        check (domain = lower(domain) and domain !~ '^www\.'
                               and domain !~ '[/ ]'),
    kind            text not null
                        check (kind in ('social', 'review', 'q_and_a',
                                        'encyclopedia', 'video', 'maps',
                                        'jobs', 'travel', 'marketplace',
                                        'directory', 'other')),
    -- FALSE: this client takes a starter-list domain OFF its list. Only a
    -- client row can remove; the starter list only adds.
    listed          boolean not null default true,
    notes           text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    check (domain_id is not null or listed)
);
create unique index directory_domain_scope_domain
    on keyword_analysis.directory_domain (coalesce(domain_id, -1), domain);

comment on table keyword_analysis.directory_domain is
    'Directory and user-content sites: the starter list (domain_id NULL) '
    'plus per-site additions (listed) and removals (not listed). A site''s '
    'effective list = starter + its additions - its removals. Filtered out '
    'of the cluster list''s second shared-URL view and skipped by content '
    'enrichment as a source. Change-logged.';

create trigger log_change
    after insert or update or delete on keyword_analysis.directory_domain
    for each row execute function pipeline.log_change();

-- The starter list: general sites only. Vertical aggregators (bus
-- booking, transit apps) are added per client.
insert into keyword_analysis.directory_domain (domain, kind) values
    ('facebook.com', 'social'), ('instagram.com', 'social'),
    ('linkedin.com', 'social'), ('tiktok.com', 'social'),
    ('x.com', 'social'), ('twitter.com', 'social'),
    ('pinterest.com', 'social'), ('nextdoor.com', 'social'),
    ('yelp.com', 'review'), ('tripadvisor.com', 'review'),
    ('bbb.org', 'review'), ('trustpilot.com', 'review'),
    ('glassdoor.com', 'review'),
    ('reddit.com', 'q_and_a'), ('quora.com', 'q_and_a'),
    ('wikipedia.org', 'encyclopedia'),
    ('youtube.com', 'video'), ('vimeo.com', 'video'),
    ('google.com', 'maps'), ('mapquest.com', 'maps'), ('apple.com', 'maps'),
    ('indeed.com', 'jobs'), ('ziprecruiter.com', 'jobs'),
    ('expedia.com', 'travel'), ('kayak.com', 'travel'),
    ('viator.com', 'travel'), ('getyourguide.com', 'travel'),
    ('rome2rio.com', 'travel'), ('booking.com', 'travel'),
    ('amazon.com', 'marketplace'), ('ebay.com', 'marketplace'),
    ('thumbtack.com', 'marketplace'), ('angi.com', 'marketplace'),
    ('yellowpages.com', 'directory'), ('manta.com', 'directory');

-- Each cluster's recurring URLs (top 10 by how many of its keywords they
-- appear for in the top 10), all and without directories:
--   [{"url", "domain", "keywords", "best_rank", "directory"}]
-- and its most common search intent with that intent's share.
alter table keyword_analysis.cluster
    add column shared_urls jsonb,
    add column shared_urls_filtered jsonb,
    add column intent text,
    add column intent_share numeric;

create index cluster_intent on keyword_analysis.cluster (build_id, lane_key, intent);
