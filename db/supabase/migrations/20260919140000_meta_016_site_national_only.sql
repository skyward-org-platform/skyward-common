-- meta.site.national_only: declaring a site needs no per-market codes.
--
-- WHY
-- The Phase 3 readiness blocker was:
--
--     m.get("general_location_codes") or m.get("ruling")
--
-- so ANY non-empty text in brand.market.ruling passed it. Thread 2 wrote
-- plain descriptive prose on 59 rows -- explaining why InSoFast's markets
-- are state-grain -- and Phase 3 flipped to ready with zero location
-- codes written. A false green light on the one phase that spends money,
-- tripped by writing a sentence rather than by deciding anything.
--
-- `ruling` stays what it is: the reasoning. This is the decision. They
-- are different facts and a boolean cannot be tripped by prose.
--
-- Adam's call, 2026-09-19.
--
-- WHY ON THE SITE RATHER THAN THE MARKET
-- Thread 2 filed a second bug in the same area: the `markets` check tests
-- len(rows) >= 1, so a site with NO markets is blocked and has no row to
-- write a ruling onto -- the documented "national-only, record a ruling"
-- escape hatch was unreachable exactly when it was needed. A per-market
-- flag repeats that exactly: no row, nowhere to put it.
--
-- National-only is a property of the site. It goes on the site, where it
-- can be set before a single market exists.
--
-- NOT NULL DEFAULT FALSE, deliberately: every existing site is
-- undeclared, which is the honest reading of "nobody has ruled on this
-- yet", and it keeps the blocker's behaviour unchanged for all 938 of
-- them. Declaring one is an act, never an absence.
--
-- meta.site is already in the readiness snapshot (scoped, not read_only)
-- and writable through the kb verbs, so an operator can set this with
-- `kb meta.site update --row '{"national_only": true}'` and the check
-- sees it with no plumbing change.

alter table meta.site
    add column national_only boolean not null default false;

comment on column meta.site.national_only is
    'This site is deliberately national in scope, so Phase 3 needs no '
    'per-market location codes. An explicit declaration, NOT inferred '
    'from brand.market.ruling, which holds the reasoning and used to '
    'clear the blocker on any non-empty text.';
