"""Live tests for the ChatGPT fan-out endpoint (ai_optimization_chat_gpt_llm_scraper).

Real API calls. Whole file costs about $0.40; no single test reaches $1.

    uv run pytest tests/live/test_dataforseo_fanout_live.py -m live --run-live -v -s

Costs are read back from DataForSEO.cost_log through `client.get_job_cost`, which is also
the check that every billed task was logged.
"""

from __future__ import annotations

import json
import time

import pandas as pd
import pytest

from skyward.data.dataforseo import fanout_prompt_id
from skyward.functions import generate_job_id

KEY = "ai_optimization_chat_gpt_llm_scraper"
TABLE = "ai_optimization-chat_gpt-llm_scraper"

SERVICES = ["charter bus rental", "corporate shuttle service", "wedding shuttle",
            "school field trip bus", "minibus rental"]
CITIES = ["Chicago", "Dallas", "Atlanta", "Phoenix", "Denver", "Seattle", "Boston",
          "Miami", "Nashville", "Portland"]
TEMPLATES = ["How much does a {s} cost in {c}?", "Who are the best {s} companies in {c}?",
             "What should I ask before booking a {s} in {c}?",
             "How far ahead should I book a {s} in {c}?", "{s} {c}"]


def many_prompts(n: int) -> list[str]:
    out = [t.format(s=s, c=c) for t in TEMPLATES for s in SERVICES for c in CITIES]
    assert len(set(out)) >= n
    return out[:n]


def job_cost(client, job_id: str, expected: float, tracker, label: str) -> float:
    """Total cost_log spend for the job; waits briefly for the streamed rows to be readable."""
    total = 0.0
    for _ in range(6):
        df = client.get_job_cost(job_id, by="total")
        total = 0.0 if df is None or df.empty else float(df.iloc[0]["total_cost_usd"] or 0)
        if total >= expected * 0.99:
            break
        time.sleep(5)
    tracker.record(f"{KEY}:{label}", total)
    return total


@pytest.fixture(scope="module")
def ep(dfs_client_live):
    return dfs_client_live.ai_optimization_chat_gpt_llm_scraper


@pytest.mark.live
def test_ai_optimization_chat_gpt_llm_scraper_standard_small(ep, dfs_client_live, cost_tracker):
    prompts = ["How much does a charter bus cost in Chicago?", "What is 100% of 50 + 25?",
               "best LASIK surgeons in Manhattan", "kitchen hood cleaning cost",
               "Who wrote Pride and Prejudice?"]
    job = generate_job_id()
    df = ep.post(prompts, job_id=job, upload=False)

    assert len(df) == 5 and set(df["prompt"]) == set(prompts)
    assert (df["status_code"] == 20000).all() and (df["endpoint_mode"] == "standard").all()
    assert df["domain_id"].isna().all() and df["task_id"].is_unique
    assert (df["prompt_id"] == df["prompt"].map(fanout_prompt_id)).all()
    assert df["fan_out_count"].notna().sum() >= 4          # forced search: fan-out expected
    assert df["model"].notna().all() and df["answer_chars"].gt(0).all()
    for raw in df["fan_out_queries"].dropna():
        queries = json.loads(raw)
        assert queries and all(isinstance(q, str) and q for q in queries)
    for raw in df["sources"].dropna():
        for src in json.loads(raw):
            assert src["url"].startswith("http") and "utm_source=chatgpt.com" not in src["url"]
    # '+' and '%' reached ChatGPT intact: it adds 50 and 25
    answer = df[df["prompt"] == "What is 100% of 50 + 25?"]["answer_markdown"].iloc[0]
    assert "75" in answer
    assert job_cost(dfs_client_live, job, 0.006, cost_tracker, "standard_small") == pytest.approx(0.006)


@pytest.mark.live
def test_ai_optimization_chat_gpt_llm_scraper_live_small(ep, dfs_client_live, cost_tracker):
    job = generate_job_id()
    df = ep.live(["charter bus rental Dallas", "Is PRK safer than LASIK?"], job_id=job,
                 upload=False)
    assert len(df) == 2 and (df["endpoint_mode"] == "live").all()
    assert (df["status_code"] == 20000).all() and df["fan_out_count"].notna().any()
    assert job_cost(dfs_client_live, job, 0.008, cost_tracker, "live_small") == pytest.approx(0.008)


@pytest.mark.live
def test_ai_optimization_chat_gpt_llm_scraper_standard_250(ep, dfs_client_live, cost_tracker):
    """Scale check: three requests, incremental collection. About $0.30."""
    prompts = many_prompts(250)
    estimate = ep.estimate_cost(prompts, endpoint_mode="standard")
    assert estimate.list_price_usd == pytest.approx(0.30)
    job = generate_job_id()
    started = time.monotonic()
    df = ep.post(prompts, job_id=job, upload=False, retry_empty=0)
    elapsed = time.monotonic() - started
    ok = df[df["status_code"] == 20000]
    with_fan_out = int(ok["fan_out_count"].notna().sum())
    print(f"\n250 prompts: {elapsed:.0f}s, {len(ok)} ok, {with_fan_out} with fan-out, "
          f"{len(df) - len(ok)} failed, avg {ok['fan_out_count'].mean():.2f} queries")
    assert len(df) == 250 and set(df["prompt"]) == set(prompts)
    assert len(ok) >= 240 and with_fan_out >= 0.9 * len(ok)
    cost = job_cost(dfs_client_live, job, 0.30, cost_tracker, "standard_250")
    assert cost == pytest.approx(0.30, abs=0.01) and cost <= estimate.max_usd


@pytest.mark.live
def test_ai_optimization_chat_gpt_llm_scraper_samples_and_retry(ep, dfs_client_live, cost_tracker):
    """Unforced evergreen questions come back empty; the retry forces the search."""
    job = generate_job_id()
    df = ep.post(["What is the capital of France?", "Who wrote Pride and Prejudice?"],
                 job_id=job, upload=False, force_web_search=False, samples=2, retry_empty=1)
    assert df["task_id"].is_unique
    for prompt, group in df.groupby("prompt"):
        assert sorted(group["attempt"])[:2] == [1, 2]
        retries = group[group["attempt"] > 2]
        empties = group[(group["attempt"] <= 2) & group["fan_out_count"].isna()]
        assert len(retries) == len(empties)
        assert retries["force_web_search"].all()
    job_cost(dfs_client_live, job, 0.0048, cost_tracker, "samples_and_retry")


@pytest.mark.live
def test_ai_optimization_chat_gpt_llm_scraper_high_priority(ep, dfs_client_live, cost_tracker):
    job = generate_job_id()
    df = ep.post(many_prompts(3), job_id=job, upload=False, priority=2, retry_empty=0)
    assert len(df) == 3
    assert job_cost(dfs_client_live, job, 0.0072, cost_tracker, "high_priority") == pytest.approx(0.0072)


@pytest.mark.live
def test_ai_optimization_chat_gpt_llm_scraper_bad_inputs(ep, dfs_client_live, cost_tracker):
    job = generate_job_id()
    df = ep.post(["charter bus rental", "x" * 2001, ""], job_id=job, upload=False,
                 location_code=1014221, ignore_location_check=True, retry_empty=0)
    assert len(df) == 3
    sent = df[df["prompt"] == "charter bus rental"].iloc[0]
    assert sent["status_code"] == 40501 and "location_code" in sent["status_message"]
    local = df[df["prompt"] != "charter bus rental"]
    assert local["status_code"].isna().all() and local["task_id"].isna().all()
    assert len(ep.rejected_prompts) == 3
    with pytest.raises(ValueError, match="language_code"):
        ep.post(["p"], job_id=generate_job_id(), upload=False, language_code="xx")
    with pytest.raises(TypeError, match="no domain"):
        ep.post(["p"], job_id=generate_job_id(), upload=False, domain="example.com")
    job_cost(dfs_client_live, job, 0.0, cost_tracker, "bad_inputs")


@pytest.mark.live
def test_ai_optimization_chat_gpt_llm_scraper_upload_roundtrip(ep, dfs_client_live, cost_tracker):
    """Three prompts into the real table and both views, then the test's own rows are removed."""
    from google.cloud import bigquery

    bq = dfs_client_live.bq_client.client
    table = f"`{bq.project}.DataForSEO.{TABLE}`"
    job = generate_job_id()
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("job", "STRING", job)])
    df = ep.post(many_prompts(3), job_id=job)
    try:
        saved = bq.query(f"SELECT * FROM {table} WHERE job_id = @job", job_config=cfg
                         ).result().to_dataframe()
        assert len(saved) == 3 and set(saved["task_id"]) == set(df["task_id"])
        assert saved["domain_id"].isna().all() and saved["upload_id"].notna().all()
        assert saved["answered_at"].notna().all() and saved["fan_out_count"].notna().any()
        assert pd.api.types.is_integer_dtype(saved["attempt"])
        queries = bq.query(
            f"SELECT q.* FROM `{bq.project}.DataForSEO.{TABLE}_queries` q "
            f"WHERE prompt_id IN (SELECT prompt_id FROM {table} WHERE job_id = @job)",
            job_config=cfg).result().to_dataframe()
        assert len(queries) >= 1 and (queries["runs_with_query"] <= queries["runs_total"]).all()
        sources = bq.query(
            f"SELECT * FROM `{bq.project}.DataForSEO.{TABLE}_sources` WHERE job_id = @job",
            job_config=cfg).result().to_dataframe()
        assert sources.empty or sources["url"].str.startswith("http").all()
    finally:
        bq.query(f"DELETE FROM {table} WHERE job_id = @job", job_config=cfg).result()
    job_cost(dfs_client_live, job, 0.0036, cost_tracker, "upload_roundtrip")
