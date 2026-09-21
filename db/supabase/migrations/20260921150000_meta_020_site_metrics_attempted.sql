-- 20260921150000_meta_020_site_metrics_attempted.sql
-- Record when the CLIENT's own domain was last sent for sizing.
--
-- Decided by Adam 2026-09-21. competitor-metrics sends the client's domain
-- in the same batch as its competitors, because the comparison threshold
-- is relative to the client. meta_015 gave each competitor row
-- metrics_attempted_at, the negative cache that stops a domain DataForSEO
-- holds nothing for from being re-bought every run. The client has no
-- competitor row, so its attempt had nowhere to be written: a new or small
-- client with no DataForSEO data -- the ordinary onboarding case -- was
-- re-bought on every run. Found live on testcorporateshuttle (848).
--
-- Same meaning as the competitor column: we SENT the domain, whether or not
-- anything came back.

alter table meta.site
    add column if not exists metrics_attempted_at timestamptz;

comment on column meta.site.metrics_attempted_at is
    'When this site''s own domain was last sent to DataForSEO for sizing, '
    'whether or not data came back. The client-side twin of '
    'meta.site_competitors.metrics_attempted_at; read as a negative cache '
    'so an unmeasurable client domain is not re-bought every run.';
