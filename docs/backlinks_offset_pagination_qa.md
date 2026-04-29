# Backlinks Pagination — QA Test Plan

Verifies the new `limit` / `page_size` / `offset` behavior in `BacklinksBacklinks._fetch_live`.

## What changed

- `_build_payload` now includes `offset` (default 0); default per-call `limit` raised from 100 → 1000.
- `_fetch_live` paginates internally:
  - `limit` (kwarg) — total rows wanted across all calls. Default `None` = pull everything.
  - `page_size` (kwarg) — rows per API call. Default 1000, clamped to 1000.
  - Loop terminates when: `limit` reached, API returns fewer than `page_size` rows, or DFS's 20,000 offset cap is hit.

## Pre-flight

```bash
uv run python -c "
from skyward.config import load_config
from skyward.data.dataforseo import DataForSEOClient, ClientConfig
cfg = load_config()
c = DataForSEOClient(ClientConfig(login=cfg.dataforseo_login, password=cfg.dataforseo_password))
print(c.get_balance())
"
```

Confirm balance > $1 before running live tests below.

## Test 1 — Small target, pagination kicks in

**Goal:** prove offset pagination loops correctly without needing a 1k+ backlink target.

Pick a domain with ~25–50 dofollow backlinks (verify via `backlinks-summary` first). Then:

```python
from skyward.data.dataforseo import DataForSEOClient, ClientConfig
from skyward.config import load_config

cfg = load_config()
client = DataForSEOClient(ClientConfig(
    login=cfg.dataforseo_login,
    password=cfg.dataforseo_password,
    debug=True,
))

df = client.backlinks_backlinks.live(
    "<small-domain.com>",
    limit=25,
    page_size=10,
)
print(len(df))           # expect 25
print(df["url_from"].nunique())  # sanity: no duplicate URLs across pages
```

**Pass criteria:**
- Returns exactly 25 rows (or fewer if domain has <25 backlinks).
- Debug log shows 3 API calls (offset=0, 10, 20).
- No duplicate `url_from` + `backlink_to` pairs across pages.

## Test 2 — `limit=None`, pull everything on small target

```python
df = client.backlinks_backlinks.live("<small-domain.com>", page_size=10)
print(len(df))
```

**Pass criteria:**
- Loops until last page returns < 10 rows, then stops.
- Row count matches `backlinks-summary.backlinks` (dofollow only) for that domain, ± minor drift.

## Test 3 — `limit` larger than available rows

```python
df = client.backlinks_backlinks.live("<small-domain.com>", limit=10000, page_size=100)
```

**Pass criteria:** stops on natural end-of-data, not at `limit`. No infinite loop.

## Test 4 — `limit` smaller than `page_size`

```python
df = client.backlinks_backlinks.live("<small-domain.com>", limit=5, page_size=100)
```

**Pass criteria:** single API call requesting 5 rows; returns ≤5.

## Test 5 — Backward compatibility (existing callers)

Any existing code passing only `limit` ≤ 1000 should still produce one API call returning up to `limit` rows:

```python
df = client.backlinks_backlinks.live("<small-domain.com>", limit=100)
```

**Pass criteria:** ≤100 rows returned, single API call (debug log).

## Test 6 — Large target (optional, costs money)

If a target with >1,000 dofollow backlinks is available:

```python
df = client.backlinks_backlinks.live("<large-domain.com>", page_size=1000)
print(len(df))
```

**Pass criteria:**
- Returns >1,000 rows (proves the original cap is gone).
- Stops at either real end-of-data or 20,000 (DFS offset ceiling).

## Test 7 — DFS offset cap guard

Synthetic check (no API call): set `limit=50000, page_size=1000` on a large target. Loop must exit at offset=20,000 even if API would return more.

## Sign-off checklist

- [ ] Test 1 passes
- [ ] Test 2 passes
- [ ] Test 3 passes
- [ ] Test 4 passes
- [ ] Test 5 passes
- [ ] Test 6 passes (or skipped — note reason)
- [ ] Test 7 passes
- [ ] No regressions in existing `tests/live/test_dataforseo_live.py::test_backlinks_backlinks_*` runs
- [ ] CLAUDE.md "Key knobs" entry reads correctly
