-- 20261009120000_keyword_analysis_006_slug_terms.sql
--
-- How Phase 3 cleans the keywords it builds from a site's URL paths ("slug"
-- keywords). Review item 1B (Adam, 2026-10-08): Insofast's slug keywords
-- came out as page-path junk ("explore explore diy shipping container
-- insulation", "about us recognized accredited") because the stop list was
-- hardcoded, matched whole folders only, and could not reject a page.
--
-- A starter list (domain_id NULL; today's hardcoded stop segments plus
-- navigational pages to reject) plus per-site additions and removals, the
-- same shape as keyword_analysis.directory_domain. Operators edit it, so
-- every edit goes to pipeline.change_log (trigger).
--
-- Apply as postgres from skyward-common's .env.

create table keyword_analysis.slug_term (
    slug_term_id  bigserial primary key,
    domain_id     bigint references meta.site(domain_id) on delete cascade, -- NULL = starter list
    term          text not null check (term = lower(term) and term <> ''),
    match         text not null check (match in ('folder','contains')),
    action        text not null check (action in ('strip','reject')),
    listed        boolean not null default true,  -- false on a site row = remove a starter term
    notes         text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    check (domain_id is not null or listed)
);
create unique index slug_term_uniq on keyword_analysis.slug_term (coalesce(domain_id,-1), term, match);
comment on table keyword_analysis.slug_term is 'How slug keywords are cleaned: a starter list (domain_id NULL) plus per-site additions and removals. match=folder: the URL folder IS the term; contains: the folder contains it as a word. action=strip: drop the word; reject: the page gives no slug keyword.';
create trigger log_change after insert or update or delete on keyword_analysis.slug_term
    for each row execute function pipeline.log_change();
grant select on keyword_analysis.slug_term to dashboard_ro;
insert into keyword_analysis.slug_term (domain_id, term, match, action, notes)
select null, t, 'folder', 'strip', 'starter: was DEFAULT_STOP_SEGMENTS' from unnest(array[
    'article','articles','blog','blogs','news','post',
    'posts','story','stories','guide','guides','resource',
    'resources','insight','insights','tip','tips','update',
    'updates','press','media','library','category',
    'categories','tag','tags','topic','topics','author',
    'authors','archive','archives','page','pages','faq',
    'faqs','help','support','docs','documentation','product',
    'products','shop','store','collection','collections',
    'item','items','service','services','location',
    'locations','city','cities','region','regions','area',
    'areas','index','home','default','main','www','en',
    'en-us','en-gb','en-au','us','uk','au'
]) t;
insert into keyword_analysis.slug_term (domain_id, term, match, action, notes) values
 (null,'about-us','folder','reject','navigational'), (null,'about','folder','reject','navigational'),
 (null,'contact','folder','reject','navigational'), (null,'contact-us','folder','reject','navigational'),
 (null,'cart','folder','reject','navigational'), (null,'checkout','folder','reject','navigational'),
 (null,'my-account','folder','reject','navigational'), (null,'login','folder','reject','navigational'),
 (null,'privacy-policy','folder','reject','navigational'), (null,'terms','folder','reject','navigational'),
 (null,'product-category','folder','strip','woocommerce category container');
