-- 20261019230000_runner_001_session_control.sql
-- The control plane for cloud pipeline-runner sessions.
--
-- Asked for by Adam 2026-10-19, building the cloud runner (see memory
-- project-cloud-pipeline-runner). The operator dashboard starts and
-- reopens one Claude session per client domain on a GCP VM, but the VM
-- has no public IP, so the dashboard cannot call it. Both sides already
-- reach Supabase over HTTPS, so Supabase is the rendezvous: the dashboard
-- WRITES a command row, the VM agent POLLS for it, acts, and writes back
-- session state the dashboard reads. No relay, no websocket, no inbound
-- hole in the VM.
--
-- THE FAILURE THIS EXISTS TO NAME. A dashboard that spawned sessions by
-- fire-and-forget could open two sessions for one domain (a shared git
-- checkout racing itself) or lose track of one that is already running.
-- runner.session is keyed on domain_id, so there is AT MOST ONE row per
-- domain, and its status is the single source of truth the dashboard and
-- the agent both read. The command queue is the only way state changes,
-- so two clicks cannot both win.
--
-- WHAT IS NOT HERE. No credentials and no transcripts: the session's
-- conversation lives on Anthropic's servers (Remote Control) and the
-- secrets live on the VM. These tables hold only which domain has a
-- session, what state it is in, and the queue of start/reopen/stop asks.

create schema if not exists runner;

-- One row per domain that has (or had) a session. Keyed on domain_id so a
-- domain can never have two.
create table if not exists runner.session (
    domain_id          integer primary key,
    client_id          integer,
    domain             text not null,
    claude_session_id  uuid not null,           -- deterministic per domain; --session-id / --resume
    status             text not null default 'stopped'
                         check (status in ('stopped','starting','running','stopping','error')),
    session_url        text,                     -- the Remote Control URL the agent captures
    tmux_window        text,                     -- where it runs on the VM
    pid                integer,
    last_error         text,
    started_at         timestamptz,
    last_seen_at       timestamptz,              -- agent refreshes while the session is alive
    updated_at         timestamptz not null default now()
);

comment on table runner.session is
  'One cloud-runner session per client domain. Written by the VM agent; read by the operator dashboard. At most one row per domain_id by construction.';

-- The command queue. The dashboard inserts; the agent claims and completes.
create table if not exists runner.command (
    id            uuid primary key default gen_random_uuid(),
    domain_id     integer not null,
    action        text not null check (action in ('start','reopen','stop')),
    requested_by  text,                          -- the operator's email from the dashboard session
    status        text not null default 'pending'
                    check (status in ('pending','claimed','done','error')),
    result        jsonb,                         -- what happened (or the error)
    created_at    timestamptz not null default now(),
    claimed_at    timestamptz,
    done_at       timestamptz
);

comment on table runner.command is
  'Start/reopen/stop requests from the operator dashboard, polled and completed by the VM agent.';

-- The agent polls this index: oldest pending first.
create index if not exists command_pending_idx
    on runner.command (created_at)
    where status = 'pending';

-- A single-row heartbeat so the dashboard can tell the agent is alive
-- (distinct from whether the VM is powered on, which the dashboard asks
-- the GCP API).
create table if not exists runner.agent_heartbeat (
    id               integer primary key default 1 check (id = 1),
    agent_last_seen  timestamptz,
    hostname         text,
    note             text
);

insert into runner.agent_heartbeat (id) values (1)
    on conflict (id) do nothing;

-- Grants. The schema is owned by `postgres` (who applies this). The two
-- clients connect as different roles, so each gets exactly what it needs:
--   adam          -- the VM agent: reads commands, writes session state.
--   dashboard_ro  -- the operator dashboard's read API: reads session
--                    state, and inserts commands (its one write).
grant usage on schema runner to adam, dashboard_ro, service_role, authenticated, anon;

-- The agent does the full lifecycle on every table.
grant select, insert, update, delete on all tables in schema runner to adam, service_role;

-- The dashboard reads state and queues commands, nothing more.
grant select on runner.session, runner.agent_heartbeat to dashboard_ro;
grant select, insert on runner.command to dashboard_ro;
