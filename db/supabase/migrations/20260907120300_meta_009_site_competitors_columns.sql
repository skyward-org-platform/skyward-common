-- 20260907120300_meta_009_site_competitors_columns.sql
-- Additive columns on meta.site_competitors. All nullable or
-- defaulted, so existing readers are unaffected.
alter table meta.site_competitors
    add column status              text not null default 'active'
                                       check (status in
                                           ('active','rejected')),
    add column type                text
                                       check (type in
                                           ('direct','adjacent',
                                            'aspirational')),
    add column market_scope        text
                                       check (market_scope in
                                           ('national','regional',
                                            'local')),
    add column verification_status text,
    add column operator_group      text,
    add column source              text;

comment on column meta.site_competitors.status is
    'Rejected competitors stay in the table so a domain someone already '
    'ruled out is visible next to the ones kept, rather than being '
    're-evaluated every time. notes carries the description on an '
    'active row and the reason on a rejected one.';
