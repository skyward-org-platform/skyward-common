"""Sheet-parsing rules for scripts/sync_authority_placement.py (no Google, no DB)."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_authority_placement as s  # noqa: E402

HEADER = s.SHEET_OWNED + s.TABLE_OWNED


def seed():
    """A header and five made-up rows shaped like the sheet."""
    rows = [HEADER]
    for i, (site, status) in enumerate([("a.com", "live"), ("b.com", "prospect"), ("c.com", "lost"),
                                        ("d.com", "prospect"), ("e.com", "prospect")]):
        r = [""] * len(HEADER)
        r[HEADER.index("placement_id")] = f"00000000-0000-4000-8000-00000000000{i}"
        r[HEADER.index("domain_id")] = "22"
        r[HEADER.index("site")] = site
        r[HEADER.index("status")] = status
        rows.append(r)
    return rows


def test_seed_parses_cleanly():
    p = s.parse_sheet(seed())
    assert p.errors == []
    assert len(p.rows) == 5
    assert p.new_ids == {}
    assert sum(r["status"] == "live" for r in p.rows) == 1


def test_blank_id_gets_a_uuid_and_blank_lines_are_skipped():
    v = seed()
    header = v[0]
    row = [""] * len(header)
    row[header.index("domain_id")] = "22"
    row[header.index("site")] = "Example.com"
    row[header.index("status")] = "Prospect"
    v += [row, [""] * len(header)]
    p = s.parse_sheet(v)
    assert p.errors == []
    assert list(p.new_ids) == [len(v) - 1]
    assert p.rows[-1]["site"] == "example.com" and p.rows[-1]["status"] == "prospect"


def test_bad_values_are_all_reported():
    v = seed()
    h = v[0]
    v[1][h.index("status")] = "Live?"
    v[2][h.index("went_live")] = "Oct 1"
    v[3][h.index("site")] = "https://x.com/page"
    v[4][h.index("dofollow")] = "maybe"
    v[5][h.index("placement_id")] = v[1][h.index("placement_id")]
    p = s.parse_sheet(v)
    joined = "\n".join(p.errors)
    for needle in ("row 2, status", "row 3, went_live", "row 4, site", "row 5, dofollow", "same id as row"):
        assert needle in joined


def test_live_needs_a_site():
    v = seed()
    h = v[0]
    v[1][h.index("status")] = "live"
    v[1][h.index("site")] = ""
    assert any("a live link needs its site" in e for e in s.parse_sheet(v).errors)


def test_local_test_flag_refuses_remote_hosts(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u@db.example.com:5432/x")
    try:
        s.database_url(local_test=True)
    except s.SyncError:
        return
    raise AssertionError("expected a refusal")


def test_pre_existing_blank_means_false_and_bad_value_is_reported():
    v = seed()
    h = v[0]
    v[1][h.index("pre_existing")] = "TRUE"
    v[2][h.index("pre_existing")] = "sort of"
    p = s.parse_sheet(v)
    assert p.rows[0]["pre_existing"] is True and p.rows[2]["pre_existing"] is False
    assert any("row 3, pre_existing" in e for e in p.errors)


def test_live_paid_link_must_be_sponsored():
    v = seed()
    h = v[0]
    v[1][h.index("type")] = "paid"  # row 2 is live
    assert any("sponsored" in e for e in s.parse_sheet(v).errors)
    v[1][h.index("sponsored")] = "TRUE"
    assert s.parse_sheet(v).errors == []


def test_30d_reading_due_only_after_30_days():
    import datetime as dt

    import enrich_authority_placement as e

    v = seed()
    h = v[0]
    v[1][h.index("went_live")] = "2026-10-01"
    p = s.parse_sheet(v)
    assert e.due_from_csv(v, p, "30d", False, dt.date(2026, 10, 30)) == []
    assert [x[1] for x in e.due_from_csv(v, p, "30d", False, dt.date(2026, 10, 31))] == ["a.com"]
    v[2][h.index("metrics_read_at")] = "2026-11-11"
    first = [x[1] for x in e.due_from_csv(v, s.parse_sheet(v), "first", False, dt.date(2026, 11, 11))]
    assert "b.com" not in first and "a.com" in first
