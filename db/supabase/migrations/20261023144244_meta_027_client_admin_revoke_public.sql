-- meta_027: take PUBLIC off the client-admin functions.
--
-- A defect in meta_026, found immediately after applying it by comparing the
-- new functions' ACLs against the two that already existed.
--
-- Postgres grants EXECUTE on a new function to PUBLIC by default. For an
-- ordinary function that is harmless. For a SECURITY DEFINER function it is
-- not: the function runs as its owner, so PUBLIC execute means any role that
-- can connect may do what the owner can do. Here that is creating clients,
-- reassigning domains and offboarding sites.
--
-- The two existing precedents, p3.set_sitemap_review and
-- pipeline.set_task_creation, carry no PUBLIC entry at all -- only postgres,
-- dashboard_ro, adam and pipeline_bot. meta_026 did not match them because it
-- granted without revoking. This brings the six into line.
--
-- Exposure in the meantime was limited rather than nil: of anon,
-- authenticated, dashboard_ro and service_role, only dashboard_ro can log in
-- on this project, and it was meant to have execute anyway. The principle
-- still stands, and the gap between "limited" and "none" is this file.

revoke execute on function meta.set_site_active(integer, boolean, text)               from public;
revoke execute on function meta.set_site_engagement(integer, text, text)              from public;
revoke execute on function meta.add_site(integer, text, text, text)                   from public;
revoke execute on function meta.create_client_with_site(text, text, text, text, text) from public;
revoke execute on function meta.set_client_active(integer, boolean, text)             from public;
revoke execute on function meta.rename_client(integer, text, text, text)              from public;

-- _label_change is called by the six as their definer and never directly, so
-- it keeps no grants of its own. Revoked explicitly rather than left to the
-- default, which is the whole point of this file.
revoke execute on function meta._label_change(text, text) from public;

-- The roles the precedents grant, so the pipeline's own code can call these
-- too rather than reimplementing the rules.
grant execute on function meta.set_site_active(integer, boolean, text)               to adam, pipeline_bot;
grant execute on function meta.set_site_engagement(integer, text, text)              to adam, pipeline_bot;
grant execute on function meta.add_site(integer, text, text, text)                   to adam, pipeline_bot;
grant execute on function meta.create_client_with_site(text, text, text, text, text) to adam, pipeline_bot;
grant execute on function meta.set_client_active(integer, boolean, text)             to adam, pipeline_bot;
grant execute on function meta.rename_client(integer, text, text, text)              to adam, pipeline_bot;
