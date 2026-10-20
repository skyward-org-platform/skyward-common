-- 20261016120000_p3_005_sitemap_review_writes.sql
--
-- The dashboard's sitemap review writes (spec docs/superpowers/specs/
-- 2026-10-16-p3-sitemap-review-page-design.md in skyward-seo-pipeline).
-- A reviewer approves, rejects or notes a page, and leaves overall notes,
-- from the operator dashboard. The dashboard login stays read only: it is
-- granted EXECUTE on exactly two SECURITY DEFINER functions, which write
-- these two tables and nothing else, require the reviewer, and label the
-- change log "dashboard sitemap review by <who>". Adam approved this
-- exception to the dashboard's read-only rule on 2026-10-16.
--
-- Apply as postgres from skyward-common's .env, with Adam's OK.

-- A note on a page without a verdict (Adam, 2026-10-16).
alter table p3.sitemap_review drop constraint if exists sitemap_review_status_check;
alter table p3.sitemap_review add constraint sitemap_review_status_check
    check (status in ('approved', 'not approved', 'note'));

create table p3.sitemap_note (
    sitemap_note_id uuid primary key default gen_random_uuid(),
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    version       integer not null,
    author        text not null check (btrim(author) <> ''),
    note          text not null check (btrim(note) <> ''),
    fix_status    text not null default 'open' check (fix_status in ('open','fixed','wont fix')),
    fix_note      text,
    fixed_in_version integer,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
comment on table p3.sitemap_note is 'A remark on a whole sitemap version, not one page ("too many seeds on city pages"). Written by p3.add_sitemap_note from the dashboard; answered by the agent with p3 sitemap-note-fix (fix_status, fix_note).';
create trigger log_change after insert or update or delete on p3.sitemap_note
    for each row execute function pipeline.log_change();
grant select on p3.sitemap_note to dashboard_ro;


create or replace function p3.set_sitemap_review(
    p_domain_id bigint,
    p_country   integer,
    p_version   integer,
    p_url_key   text,
    p_url       text,
    p_status    text,
    p_note      text,
    p_reviewer  text
)
    returns setof p3.sitemap_review
    language plpgsql
    security definer
    set search_path = pg_catalog, pg_temp
as $$
declare
    v_status    text;
    v_note      text := nullif(btrim(coalesce(p_note, '')), '');
    v_build     text;
    v_lane      text;
    v_cluster   integer;
    v_label     text;
    v_keywords  jsonb := '[]'::jsonb;
begin
    if p_reviewer is null or btrim(p_reviewer) = '' then
        raise exception 'p3.set_sitemap_review: a reviewer is required. Sign in to review.'
            using errcode = 'check_violation';
    end if;
    if p_status is not null and p_status not in ('approved', 'not approved') then
        raise exception 'p3.set_sitemap_review: status must be approved, not approved or empty.'
            using errcode = 'check_violation';
    end if;
    if p_status = 'not approved' and v_note is null then
        raise exception 'p3.set_sitemap_review: a rejection needs a note saying why.'
            using errcode = 'check_violation';
    end if;
    select v.status, v.builds ->> 'build_id' into v_status, v_build
      from p3.sitemap_version v
     where v.domain_id = p_domain_id and v.country_code = p_country and v.version = p_version;
    if v_status is distinct from 'in review' then
        raise exception 'p3.set_sitemap_review: version % is not in review (it is %).',
            p_version, coalesce(v_status, 'missing')
            using errcode = 'check_violation';
    end if;

    if coalesce(current_setting('app.changed_via', true), '') = '' then
        perform set_config('app.changed_via',
                           'dashboard sitemap review by ' || btrim(p_reviewer), true);
    end if;

    -- Nothing left to say: the reviewer cleared their verdict and note.
    if p_status is null and v_note is null then
        return query
        delete from p3.sitemap_review r
         where r.domain_id = p_domain_id and r.url_key = p_url_key
           and r.round = p_version and r.reviewer = btrim(p_reviewer)
        returning r.*;
        return;
    end if;

    -- The cluster the reviewer saw, so an approval carries forward.
    select p.lane_key, p.cluster_id, p.cluster_label into v_lane, v_cluster, v_label
      from p3.sitemap_page p
     where p.domain_id = p_domain_id and p.country_code = p_country
       and p.version = p_version and p.url_key = p_url_key;
    if not found then
        select n.lane_key, n.cluster_id, n.cluster_label into v_lane, v_cluster, v_label
          from p3.sitemap_new_page n
         where n.domain_id = p_domain_id and n.country_code = p_country
           and n.version = p_version and n.suggested_path = p_url_key;
    end if;
    if v_cluster is not null and v_build is not null then
        select coalesce(jsonb_agg(m.keyword order by m.position), '[]'::jsonb) into v_keywords
          from keyword_analysis.cluster_member m
         where m.build_id = v_build::uuid and m.lane_key = v_lane and m.cluster_id = v_cluster;
    end if;

    return query
    insert into p3.sitemap_review as r
        (domain_id, country_code, url_key, url, round, reviewer, reviewer_kind, status,
         reviewed_version, lane_key, cluster_id, cluster_label, cluster_keywords,
         review_note, reviewed_at)
    values
        (p_domain_id, p_country, p_url_key, p_url, p_version, btrim(p_reviewer), 'person',
         coalesce(p_status, 'note'), p_version, v_lane, v_cluster, v_label, v_keywords,
         v_note, now())
    on conflict (domain_id, url_key, round, reviewer) do update
        set status = excluded.status, review_note = excluded.review_note,
            reviewed_at = excluded.reviewed_at, lane_key = excluded.lane_key,
            cluster_id = excluded.cluster_id, cluster_label = excluded.cluster_label,
            cluster_keywords = excluded.cluster_keywords, fix_status = 'open',
            updated_at = now()
    returning r.*;
end;
$$;

comment on function p3.set_sitemap_review(bigint, integer, integer, text, text, text, text, text) is
    'Approve, reject (with a note) or note one page of a sitemap version in review. The reviewer '
    'is required; empty status and note remove that reviewer''s review. One of the two writes '
    'the dashboard is granted.';
revoke all on function p3.set_sitemap_review(bigint, integer, integer, text, text, text, text, text)
    from public;
grant execute on function p3.set_sitemap_review(bigint, integer, integer, text, text, text, text, text)
    to dashboard_ro, adam, pipeline_bot;


create or replace function p3.add_sitemap_note(
    p_domain_id bigint,
    p_country   integer,
    p_version   integer,
    p_note      text,
    p_author    text
)
    returns setof p3.sitemap_note
    language plpgsql
    security definer
    set search_path = pg_catalog, pg_temp
as $$
begin
    if p_author is null or btrim(p_author) = '' then
        raise exception 'p3.add_sitemap_note: an author is required. Sign in to leave a note.'
            using errcode = 'check_violation';
    end if;
    if p_note is null or btrim(p_note) = '' then
        raise exception 'p3.add_sitemap_note: the note is empty.'
            using errcode = 'check_violation';
    end if;
    if not exists (select 1 from p3.sitemap_version v
                    where v.domain_id = p_domain_id and v.country_code = p_country
                      and v.version = p_version) then
        raise exception 'p3.add_sitemap_note: no sitemap version % for this site.', p_version
            using errcode = 'check_violation';
    end if;
    if coalesce(current_setting('app.changed_via', true), '') = '' then
        perform set_config('app.changed_via',
                           'dashboard sitemap review by ' || btrim(p_author), true);
    end if;
    return query
    insert into p3.sitemap_note (domain_id, country_code, version, author, note)
    values (p_domain_id, p_country, p_version, btrim(p_author), btrim(p_note))
    returning *;
end;
$$;

comment on function p3.add_sitemap_note(bigint, integer, integer, text, text) is
    'Leave an overall note on a sitemap version. The author is required. One of the two writes '
    'the dashboard is granted.';
revoke all on function p3.add_sitemap_note(bigint, integer, integer, text, text) from public;
grant execute on function p3.add_sitemap_note(bigint, integer, integer, text, text)
    to dashboard_ro, adam, pipeline_bot;
