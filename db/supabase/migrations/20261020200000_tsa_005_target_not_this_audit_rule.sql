-- 20261020200000_tsa_005_target_not_this_audit_rule.sql
--
-- tsa.target.exclusion_rule gains 'not-this-audit': a page whose newest
-- Phase 1 verdict comes from an older audit than Phase 1's latest one. Phase 1
-- did not measure it this time, so Phase 2 leaves it out and says so on its
-- row. The pipeline started writing it in skyward-seo-pipeline PR 166; the
-- first live write (busbank, 2026-10-20) was refused by the check below.
--
-- Same drop-and-re-add as tsa_003, with every earlier value kept.

alter table tsa.target drop constraint if exists target_exclusion_rule_check;
alter table tsa.target add constraint target_exclusion_rule_check
    check (exclusion_rule is null or exclusion_rule in
           ('doubled-slash','clean-twin','non-200','off-host','malformed',
            'held','not-this-audit'));
