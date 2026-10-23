-- 20261023143415_meta_026_dashboard_client_admin.sql
--
-- Let the operator dashboard create and retire clients, domains and sites.
--
-- Asked for by Adam 2026-10-23: three-dot menus on the Cloud Runner page's
-- client cards and domain rows, plus buttons to add a domain to a client and
-- to add a client. Until now every one of those was a Phase 0 verb run from a
-- terminal, so an operator who wanted to record a new prospect had to ask a
-- developer.
--
-- WHY FUNCTIONS RATHER THAN GRANTS. The dashboard's own rule is that it reads
-- the pipelines' schemas and never writes to them (web/server/db.py). The one
-- existing exception is narrow and shaped for it: the cloud runner grants
-- `dashboard_ro` insert on `runner.command` alone, a queue the VM agent
-- drains. Identity is not a queue. Granting the dashboard update on
-- `meta.site` would let any defect in the web layer rewrite which client a
-- domain belongs to, which is the exact failure Adam asked to prevent when he
-- said "we need to make sure we don't accidentally assign the same domain to
-- multiple clients". So the writes are six SECURITY DEFINER functions that
-- validate first, and the dashboard gets execute on those and nothing else.
-- Same shape as p3.set_sitemap_review and pipeline.set_task_creation, which
-- are the two live precedents.
--
-- WHO DID IT. `meta.site` already carries a `log_change` trigger on insert,
-- update and delete, so site changes are audited today. `meta.clients` and
-- `meta.domains` carry none, so a rename or a domain retirement left no trace
-- at all. This adds the same trigger to both.
--
-- THE DOMAIN RULE, WHICH IS SUBTLER THAN IT LOOKS. 1,166 of our 1,212 domain
-- rows have no site row: they are competitors and other sites we have merely
-- recorded. So a domain already existing is normal and is NOT a conflict. The
-- conflict is a domain that already has a SITE row, under any client, at any
-- status. These functions reuse an existing domain row and refuse only on the
-- site row, which both honours Adam's rule and stops the registry filling with
-- duplicates of domains we already know.

-- ---------------------------------------------------------------------------
-- 1. Audit the two tables that were not audited.
-- ---------------------------------------------------------------------------

drop trigger if exists log_change on meta.clients;
create trigger log_change
    after insert or update or delete on meta.clients
    for each row execute function pipeline.log_change();

drop trigger if exists log_change on meta.domains;
create trigger log_change
    after insert or update or delete on meta.domains
    for each row execute function pipeline.log_change();

-- ---------------------------------------------------------------------------
-- 2. Remember what a client-level deactivation switched off.
-- ---------------------------------------------------------------------------
--
-- Adam's call, 2026-10-23: deactivating a client deactivates every domain
-- under it. The half nobody thinks about is reactivation -- without a record,
-- turning the client back on would resurrect sites that were retired
-- deliberately, perhaps a year earlier. This column marks only the sites a
-- client-level deactivation switched off, so reactivating restores exactly
-- those and leaves the rest alone.

alter table meta.site
    add column if not exists offboarded_by_client_at timestamptz;

comment on column meta.site.offboarded_by_client_at is
  'Set when a client-level deactivation retired this site, cleared when a client-level reactivation restores it. Null means this site''s state was set on its own and a client toggle must not touch it.';

-- ---------------------------------------------------------------------------
-- 3. The write functions.
-- ---------------------------------------------------------------------------

-- Refuse an unattributed change, and label the change log with the person.
--
-- A SECURITY DEFINER function runs as its OWNER, so `change_log.changed_by`
-- records the database role and not the operator. `pipeline.set_task_creation`
-- solves that by setting `app.changed_via` for the transaction, which the
-- log_change trigger reads. Same thing here, in one place, so six functions
-- cannot drift into labelling six different ways.
create or replace function meta._label_change(p_who text, p_what text)
returns void
language plpgsql
security definer
set search_path = meta, pipeline, public
as $$
begin
    if p_who is null or btrim(p_who) = '' then
        raise exception 'who is making this change is required. Say who is making it.'
            using errcode = 'check_violation';
    end if;
    -- Unless the caller already labelled this transaction.
    if coalesce(current_setting('app.changed_via', true), '') = '' then
        perform set_config('app.changed_via',
                           'dashboard ' || p_what || ' by ' || btrim(p_who),
                           true);
    end if;
end;
$$;

-- Retire or restore ONE site. Adam's call, 2026-10-23: one button, writing
-- the lifecycle and the domain flag together.
--
-- They are written together because they are the same fact stored twice and
-- the data proves it: every domain flagged inactive is also paused or
-- offboarded, and every active one is active. P0-Testing found the cost of
-- writing only one on 2026-09-26 -- setting the lifecycle changed nothing on
-- screen until the domain flag was set too, because the read API folds the
-- lifecycle into `is_active`.
create or replace function meta.set_site_active(
    p_domain_id integer,
    p_active    boolean,
    p_who       text
) returns meta.site
language plpgsql
security definer
set search_path = meta, pipeline, public
as $$
declare
    result meta.site;
begin
    perform meta._label_change(p_who, 'site activation');

    update meta.site
       set lifecycle_status = case when p_active then 'active' else 'offboarded' end,
           -- A single-site toggle is never part of a client cascade, so it
           -- clears the mark rather than leaving a stale one behind.
           offboarded_by_client_at = null,
           updated_at = now()
     where domain_id = p_domain_id
    returning * into result;

    if not found then
        raise exception 'no site for domain_id %', p_domain_id;
    end if;

    update meta.domains set is_active = p_active where domain_id = p_domain_id;

    return result;
end;
$$;

-- What kind of engagement this site is. Per SITE, deliberately: Adam's call,
-- 2026-10-23, after seeing that the status has always lived on the site row
-- and that a client's domains may legitimately disagree.
create or replace function meta.set_site_engagement(
    p_domain_id integer,
    p_status    text,
    p_who       text
) returns meta.site
language plpgsql
security definer
set search_path = meta, pipeline, public
as $$
declare
    result meta.site;
begin
    perform meta._label_change(p_who, 'site engagement change');
    -- Named here as well as in the table's check constraint, so the refusal
    -- says what the allowed values are rather than quoting a constraint name.
    if p_status is null or p_status not in ('client','prospect','canceled','prototype') then
        raise exception 'engagement status must be one of client, prospect, canceled, prototype (got %)', coalesce(p_status, 'null');
    end if;

    update meta.site
       set engagement_status = p_status,
           updated_at = now()
     where domain_id = p_domain_id
    returning * into result;

    if not found then
        raise exception 'no site for domain_id %', p_domain_id;
    end if;

    return result;
end;
$$;

-- Attach a domain to a client as a new site.
--
-- Reuses an existing meta.domains row when there is one, because most domains
-- we add this way are already in the registry as somebody's competitor, and
-- a second row for the same name would split every later lookup.
create or replace function meta.add_site(
    p_client_id integer,
    p_domain    text,
    p_status    text,
    p_who       text
) returns meta.site
language plpgsql
security definer
set search_path = meta, pipeline, public
as $$
declare
    v_domain    text := lower(btrim(coalesce(p_domain, '')));
    v_domain_id integer;
    v_owner     text;
    result      meta.site;
begin
    perform meta._label_change(p_who, 'site added');
    if v_domain = '' then
        raise exception 'a domain is required';
    end if;
    if p_status is null or p_status not in ('client','prospect','canceled','prototype') then
        raise exception 'engagement status must be one of client, prospect, canceled, prototype (got %)', coalesce(p_status, 'null');
    end if;
    if not exists (select 1 from meta.clients c where c.client_id = p_client_id) then
        raise exception 'no client with client_id %', p_client_id;
    end if;

    select d.domain_id into v_domain_id from meta.domains d where lower(d.domain) = v_domain;

    if v_domain_id is null then
        insert into meta.domains (domain, domain_name, is_active)
             values (v_domain, v_domain, true)
          returning domain_id into v_domain_id;
    end if;

    -- THE CHECK ADAM ASKED FOR. A domain in the registry is normal; a domain
    -- that already has a site row belongs to somebody, and the refusal names
    -- whom so the operator can go and look rather than guess.
    select c.client_name into v_owner
      from meta.site s join meta.clients c on c.client_id = s.client_id
     where s.domain_id = v_domain_id;

    if v_owner is not null then
        raise exception '% is already assigned to %', v_domain, v_owner;
    end if;

    insert into meta.site (domain_id, client_id, engagement_status,
                           lifecycle_status, source)
         values (v_domain_id, p_client_id, p_status, 'active', 'dashboard')
      returning * into result;

    update meta.domains set is_active = true where domain_id = v_domain_id;

    return result;
end;
$$;

-- Create a client and its first site in one statement.
--
-- Adam's call, 2026-10-23: the first domain and its status are part of
-- creating a client, not a later step. The reason is in the dashboard rather
-- than the schema -- the sidebar renders a client only when at least one of
-- its domains survives the filter, so a client with no domains is invisible
-- the moment the form closes. Making the domain part of creation also means
-- every client has somewhere for its status to live from the moment it exists.
--
-- One statement, so a refused domain leaves no half-made client behind.
create or replace function meta.create_client_with_site(
    p_client_name  text,
    p_abbreviation text,
    p_domain       text,
    p_status       text,
    p_who          text
) returns meta.site
language plpgsql
security definer
set search_path = meta, pipeline, public
as $$
declare
    v_name      text := btrim(coalesce(p_client_name, ''));
    v_client_id integer;
begin
    perform meta._label_change(p_who, 'client created');
    if v_name = '' then
        raise exception 'a client name is required';
    end if;
    if exists (select 1 from meta.clients c where lower(c.client_name) = lower(v_name)) then
        raise exception 'a client named % already exists', v_name;
    end if;

    insert into meta.clients (client_name, abbreviation, is_active)
         values (v_name, nullif(btrim(coalesce(p_abbreviation, '')), ''), true)
      returning client_id into v_client_id;

    -- add_site does the domain validation, so there is one copy of that rule.
    -- It raises on a domain that is already assigned, which rolls this whole
    -- function back: no orphan client row.
    return meta.add_site(v_client_id, p_domain, p_status, p_who);
end;
$$;

-- Retire or restore a whole client, and its sites with it.
--
-- Adam's call, 2026-10-23: the cascade is what you usually mean by closing a
-- client down. The cost he accepted, and the reason for
-- `offboarded_by_client_at`: a reactivation must not resurrect a site that was
-- retired on its own merits before the client was ever switched off.
create or replace function meta.set_client_active(
    p_client_id integer,
    p_active    boolean,
    p_who       text
) returns table (client_id integer, is_active boolean, sites_changed integer)
language plpgsql
security definer
set search_path = meta, pipeline, public
as $$
declare
    v_changed integer := 0;
    v_ids     integer[] := '{}';
begin
    perform meta._label_change(p_who, 'client activation');
    if not exists (select 1 from meta.clients c where c.client_id = p_client_id) then
        raise exception 'no client with client_id %', p_client_id;
    end if;

    update meta.clients c set is_active = p_active where c.client_id = p_client_id;

    if not p_active then
        -- Mark and retire only the sites that are live right now. One that is
        -- already retired is left alone AND left unmarked, so restoring the
        -- client does not restore it.
        with retired as (
            update meta.site s
               set lifecycle_status = 'offboarded',
                   offboarded_by_client_at = now(),
                   updated_at = now()
             where s.client_id = p_client_id
               and s.lifecycle_status = 'active'
            returning s.domain_id
        )
        select count(*), coalesce(array_agg(domain_id), '{}')
          into v_changed, v_ids
          from retired;
    else
        -- Restore EXACTLY what this cascade retired, and nothing else.
        with restored as (
            update meta.site s
               set lifecycle_status = 'active',
                   offboarded_by_client_at = null,
                   updated_at = now()
             where s.client_id = p_client_id
               and s.offboarded_by_client_at is not null
            returning s.domain_id
        )
        select count(*), coalesce(array_agg(domain_id), '{}')
          into v_changed, v_ids
          from restored;
    end if;

    update meta.domains d set is_active = p_active
     where d.domain_id = any (v_ids);

    return query select p_client_id, p_active, v_changed;
end;
$$;

-- Rename a client, or set its abbreviation.
create or replace function meta.rename_client(
    p_client_id    integer,
    p_client_name  text,
    p_abbreviation text,
    p_who          text
) returns meta.clients
language plpgsql
security definer
set search_path = meta, pipeline, public
as $$
declare
    v_name text := btrim(coalesce(p_client_name, ''));
    result meta.clients;
begin
    perform meta._label_change(p_who, 'client renamed');
    if v_name = '' then
        raise exception 'a client name is required';
    end if;
    if exists (select 1 from meta.clients c
                where lower(c.client_name) = lower(v_name)
                  and c.client_id <> p_client_id) then
        raise exception 'a client named % already exists', v_name;
    end if;

    update meta.clients c
       set client_name = v_name,
           abbreviation = nullif(btrim(coalesce(p_abbreviation, '')), '')
     where c.client_id = p_client_id
    returning * into result;

    if not found then
        raise exception 'no client with client_id %', p_client_id;
    end if;

    return result;
end;
$$;

-- ---------------------------------------------------------------------------
-- 4. Grants. Execute on the functions, and nothing else.
-- ---------------------------------------------------------------------------
--
-- dashboard_ro keeps no table privileges in meta. Everything it may change in
-- this schema is the six calls below, each of which validates first.

-- _label_change is called BY the others as their definer, so the dashboard
-- never needs execute on it directly.
grant execute on function meta.set_site_active(integer, boolean, text)               to dashboard_ro;
grant execute on function meta.set_site_engagement(integer, text, text)              to dashboard_ro;
grant execute on function meta.add_site(integer, text, text, text)                   to dashboard_ro;
grant execute on function meta.create_client_with_site(text, text, text, text, text) to dashboard_ro;
grant execute on function meta.set_client_active(integer, boolean, text)             to dashboard_ro;
grant execute on function meta.rename_client(integer, text, text, text)              to dashboard_ro;
