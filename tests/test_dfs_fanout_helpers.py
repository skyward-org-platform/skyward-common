"""Prompt/response helpers for the ChatGPT LLM Scraper (fan-out) endpoint.

Fixtures under tests/fixtures/llm_scraper are real responses, trimmed.
"""

import json
from pathlib import Path

import pytest

from skyward.data.dataforseo import fanout_prompt_id
from skyward.data.dataforseo.fanout import (
    SCHEMA, clean_url, escape_prompt, local_row, make_tag, normalize_prompt, parse_tag,
    parse_task, unescape_prompt,
)

FIXTURES = Path(__file__).parent / "fixtures" / "llm_scraper"


def load_task(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())["tasks"][0]


# ----- prompt id -----

def test_prompt_id_is_16_hex_and_stable():
    pid = fanout_prompt_id("charter bus rental")
    assert len(pid) == 16 and int(pid, 16) >= 0
    assert pid == fanout_prompt_id("charter bus rental")


def test_prompt_id_ignores_outer_whitespace_and_unicode_form():
    assert fanout_prompt_id("  cafe prices \n") == fanout_prompt_id("cafe prices")
    assert fanout_prompt_id("café") == fanout_prompt_id("café")


def test_prompt_id_keeps_case_and_inner_whitespace():
    assert fanout_prompt_id("LASIK cost") != fanout_prompt_id("lasik cost")
    assert fanout_prompt_id("a  b") != fanout_prompt_id("a b")
    assert normalize_prompt(" a  B ") == "a  B"


# ----- wire escaping -----

@pytest.mark.parametrize("text", [
    "What is 100% of 50 + 25?", "plain", "%2B literal", "%25", "a+b%c", "++%%", "50%25+",
])
def test_escape_round_trip(text):
    assert unescape_prompt(escape_prompt(text)) == text


def test_escape_protects_percent_and_plus():
    assert escape_prompt("100% of 50 + 25") == "100%25 of 50 %2B 25"


# ----- tag -----

def test_tag_round_trip():
    pid = fanout_prompt_id("x")
    assert parse_tag(make_tag(pid, 3)) == (pid, 3, True)
    assert parse_tag(make_tag(pid, 1, store_answer=False)) == (pid, 1, False)
    assert len(make_tag(pid, 999, store_answer=False)) < 255


@pytest.mark.parametrize("tag", [None, "", "probe-3", "abc.1", "0123456789abcdef.x",
                                 "0123456789abcdef.1.y", "0123456789abcdef.1.x.z",
                                 "0123456789abcdeg.1", "0123456789ABCDEF.1",
                                 "0123456789abcdef.\u00b2", "0123456789abcdef.1\n"])
def test_foreign_tags_are_not_ours(tag):
    assert parse_tag(tag) == (None, None, True)


# ----- urls -----

@pytest.mark.parametrize("url,expected", [
    ("https://a.com/p?utm_source=chatgpt.com", "https://a.com/p"),
    ("https://a.com/p?x=1&utm_source=chatgpt.com", "https://a.com/p?x=1"),
    ("https://a.com/p?utm_source=chatgpt.com&x=1#frag", "https://a.com/p?x=1#frag"),
    ("https://a.com/p?utm_source=news", "https://a.com/p?utm_source=news"),
    ("https://a.com/p", "https://a.com/p"),
    (None, None),
])
def test_clean_url(url, expected):
    assert clean_url(url) == expected


# ----- parser on real responses -----

def test_full_response():
    task = load_task("full")
    row = parse_task(task, prompt="the prompt")
    assert set(row) == set(SCHEMA) | {"task_id"}
    assert row["task_id"] == task["id"]
    assert row["prompt"] == "the prompt" and row["prompt_id"] == fanout_prompt_id("the prompt")
    assert row["status_code"] == 20000
    assert row["model"] == "gpt-6" and row["answered_at"].startswith("2026-")
    assert (row["location_code"], row["language_code"]) == (2840, "en")
    assert row["force_web_search"] is True
    fan_out = json.loads(row["fan_out_queries"])
    assert row["fan_out_count"] == len(fan_out) == 5
    sources = json.loads(row["sources"])
    assert row["source_count"] == len(sources) == 14
    assert [s["position"] for s in sources] == list(range(1, 15))
    assert set(sources[0]) == {"position", "url", "domain", "title", "source_name",
                               "publication_date", "citation_count"}
    assert all(s["citation_count"] >= 1 for s in sources)
    results = json.loads(row["search_results"])
    assert row["search_result_count"] == len(results) == len({r["url"] for r in results})
    assert not any("utm_source=chatgpt.com" in r["url"] for r in results)
    assert row["brand_entity_count"] == 6
    assert row["answer_chars"] == len(row["answer_markdown"]) > 0
    assert "chat_gpt_text" in json.loads(row["item_types"])


def test_null_fan_out_is_a_row_with_null_not_zero():
    row = parse_task(load_task("null_fanout"), prompt="Who wrote Pride and Prejudice?")
    assert row["status_code"] == 20000
    assert row["fan_out_count"] is None and row["fan_out_queries"] is None
    assert row["sources"] is None and row["source_count"] == 0
    assert row["local_businesses"] is None and row["local_business_count"] == 0
    assert row["answer_chars"] > 0


def test_local_businesses_block():
    row = parse_task(load_task("local_businesses"), prompt="charter bus rental")
    businesses = json.loads(row["local_businesses"])
    assert row["local_business_count"] == len(businesses) >= 2
    first = businesses[0]
    assert set(first) == {"title", "domain", "url", "address", "phone", "rating",
                          "reviews_count"}
    assert first["title"] == "Bus USA Dallas Fortworth" and first["rating"] == 4.5


def test_brand_name_comes_from_markdown():
    task = load_task("brand_swap")
    raw = task["result"][0]["brand_entities"]
    swapped = next(b for b in raw if b["title"] != b["markdown"])
    brands = json.loads(parse_task(task, prompt="p")["brand_entities"])
    match = next(b for b in brands if b["name"] == swapped["markdown"])
    assert match["description"] == swapped["title"]
    same = [b for b in raw if b["title"] == b["markdown"]]
    for b in same:
        assert next(x for x in brands if x["name"] == b["title"])["description"] is None


def test_store_answer_off_keeps_length_only():
    task = load_task("full")
    task["data"]["tag"] = make_tag(fanout_prompt_id("p"), 2, store_answer=False)
    row = parse_task(task, prompt="p")
    assert row["answer_markdown"] is None and row["answer_chars"] > 0
    assert row["attempt"] == 2


def test_collector_shape_rebuilds_prompt_from_request_echo():
    """The collector has no prompt list: prompt comes from data.keyword (escaped form),
    never from result.keyword, which has already lost the '+'."""
    task = load_task("plus_percent")
    prompt = "What is 100% of 50 + 25?"
    task["data"]["keyword"] = escape_prompt(prompt)
    task["data"]["tag"] = make_tag(fanout_prompt_id(prompt), 1)
    row = parse_task(task)
    assert row["prompt"] == prompt
    assert row["prompt_id"] == fanout_prompt_id(prompt) and row["attempt"] == 1
    assert row["force_web_search"] is False


def test_foreign_tag_falls_back_to_hashing_the_prompt():
    task = load_task("null_fanout")          # tag is "probe-9"
    row = parse_task(task)
    assert row["prompt_id"] == fanout_prompt_id(task["data"]["keyword"])
    assert row["attempt"] == 1


# ----- parser on synthetic shapes -----

def _synthetic(result):
    return {"id": "t1", "status_code": 20000, "status_message": "Ok.",
            "data": {"keyword": "p", "location_code": 2840, "language_code": "en",
                     "force_web_search": True, "tag": make_tag(fanout_prompt_id("p"), 1)},
            "result": [result]}


def test_search_results_deduplicated_after_stripping_tracking():
    row = parse_task(_synthetic({
        "fan_out_queries": ["q"], "markdown": "a",
        "search_results": [
            {"url": "https://a.com/x?utm_source=chatgpt.com", "domain": "a.com", "title": "A"},
            {"url": "https://a.com/x", "domain": "a.com", "title": "A again"},
            {"url": "https://b.com/", "domain": "b.com", "title": "T" * 2500},
        ],
    }))
    results = json.loads(row["search_results"])
    assert [r["url"] for r in results] == ["https://a.com/x", "https://b.com/"]
    assert [r["position"] for r in results] == [1, 2]
    assert row["search_result_count"] == 2
    assert len(results[1]["title"]) == 500


def test_citation_count_counts_answer_blocks_citing_the_url():
    src = {"url": "https://a.com/x", "domain": "a.com", "title": "A", "source_name": "A",
           "publication_date": None}
    other = {**src, "url": "https://b.com/"}
    row = parse_task(_synthetic({
        "fan_out_queries": ["q"], "markdown": "a", "sources": [src, other],
        "items": [
            {"type": "chat_gpt_text", "sources": [src]},
            {"type": "chat_gpt_text", "sources": [{**src, "url": src["url"] + "?utm_source=chatgpt.com"}]},
            {"type": "chat_gpt_text", "sources": None},
        ],
    }))
    sources = json.loads(row["sources"])
    assert [(s["url"], s["citation_count"]) for s in sources] == [
        ("https://a.com/x", 2), ("https://b.com/", 0)]


@pytest.mark.parametrize("task", [
    {"id": "t9", "status_code": 40501, "status_message": "Invalid Field: 'keyword'.",
     "data": {"keyword": "p", "tag": "0123456789abcdef.1"}, "result": None},
    {"id": "t9", "status_code": 40102, "status_message": "No Search Results.",
     "data": {"keyword": "p", "tag": "0123456789abcdef.1"}},
    {"id": "t9", "status_code": 20000, "status_message": "Ok.",
     "data": {"keyword": "p", "tag": "0123456789abcdef.1"}, "result": []},
])
def test_failed_or_resultless_task_still_gives_a_row(task):
    row = parse_task(task)
    assert row["task_id"] == "t9" and row["prompt"] == "p"
    assert row["prompt_id"] == "0123456789abcdef"
    assert row["status_code"] == task["status_code"]
    assert row["status_message"] == task["status_message"]
    assert row["fan_out_count"] is None and row["model"] is None


@pytest.mark.parametrize("task", [None, {}, {"data": None}, "nonsense"])
def test_garbage_does_not_raise(task):
    row = parse_task(task)
    assert set(row) == set(SCHEMA) | {"task_id"}


def test_local_row():
    row = local_row("p", "0123456789abcdef", attempt=1, message="too long",
                    location_code=2840, language_code="en", force_web_search=True)
    assert set(row) == set(SCHEMA) | {"task_id"}
    assert row["status_code"] is None and row["status_message"] == "too long"
    assert row["task_id"] is None


def test_list_fields_with_junk_elements_do_not_raise():
    row = parse_task(_synthetic({
        "fan_out_queries": ["q"], "markdown": "a",
        "sources": [None, {"url": "https://a.com/"}, "junk"],
        "search_results": [None, {"url": "https://b.com/"}],
        "brand_entities": [None, {"title": "T", "markdown": "N"}],
        "items": [None, {"type": "chat_gpt_local_businesses", "items": [None, {"title": "B"}]},
                  {"type": "chat_gpt_text", "sources": [None]}],
    }))
    assert (row["source_count"], row["search_result_count"], row["brand_entity_count"],
            row["local_business_count"]) == (1, 1, 1, 1)


def test_null_location_in_result_does_not_wipe_what_was_sent():
    row = parse_task(_synthetic({"fan_out_queries": ["q"], "markdown": "a",
                                 "location_code": None, "language_code": None}))
    assert (row["location_code"], row["language_code"]) == (2840, "en")
