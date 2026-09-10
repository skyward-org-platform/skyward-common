-- 20260910140000_site_002_crawl_verified.sql
-- Whether we have actually crawled the site, as a fact rather than prose.
--
-- site.crawl_config.bot_protection is free text and is used as free text.
-- The first real step 4 run wrote:
--
--   "Cloudflare. robots.txt disallows /cdn-cgi/, and the site is served
--    through Cloudflare; a plain curl with a normal browser user agent
--    was not challenged on 2026-09-10."
--
-- Correct, useful, and unparseable. A readiness check reading it as
-- "protection exists, therefore blocked" stops Phase 1 on a site that
-- crawls perfectly well -- which is worse than the opposite error,
-- because Phase 1 fails fast on a genuinely blocked site anyway.
--
-- Null means nobody has tried, which is a warning rather than a blocker:
-- trying is cheap. False means we tried and were refused, which with no
-- agreed user agent and no whitelisted IP is the real blocker.
alter table site.crawl_config
    add column crawl_verified boolean;

comment on column site.crawl_config.crawl_verified is
    'True = we fetched the site successfully. False = we tried and were '
    'refused. Null = nobody has tried. bot_protection stays free text '
    'describing WHAT is in the way; this says whether it stopped us.';
