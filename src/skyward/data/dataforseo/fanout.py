"""Prompt and response helpers for the ChatGPT LLM Scraper (fan-out) endpoint.

Pure functions, no I/O: prompt normalisation and the `prompt_id` join key, the wire
escaping DataForSEO needs, the request tag that maps a result back to its prompt, and the
parser that turns one task of a `task_get/advanced` or `live/advanced` response into one
table row.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter

PROMPT_ID_LENGTH = 16
MAX_PROMPT_CHARS = 2000
MAX_TITLE_CHARS = 500
OK_STATUS = 20000

# Endpoint columns, in table order (the metadata block is stamped elsewhere).
SCHEMA = [
    "prompt_id", "prompt", "location_code", "language_code", "force_web_search", "attempt",
    "status_code", "status_message", "model", "answered_at",
    "fan_out_count", "source_count", "search_result_count", "brand_entity_count",
    "local_business_count", "answer_chars", "answer_markdown",
    "item_types", "fan_out_queries", "sources", "search_results", "brand_entities",
    "local_businesses",
]

_NO_ANSWER_SUFFIX = "x"
_TAG = re.compile(r"([0-9a-f]{%d})\.([0-9]{1,9})(\.x)?" % PROMPT_ID_LENGTH)
_ESCAPED = re.compile(r"%(25|2B)")
_TRACKING = ("utm_source", "chatgpt.com")


def normalize_prompt(text) -> str:
    """NFC plus a strip of the ends. Case and inner whitespace are what ChatGPT reads,
    so they are left alone."""
    return unicodedata.normalize("NFC", str(text)).strip()


def fanout_prompt_id(text) -> str:
    """Stable join key for a prompt: 16 hex characters of SHA-256 over the normalised text.

    Location, language and the web-search flag are deliberately not part of it, so the
    same prompt has the same id wherever it is run.
    """
    digest = hashlib.sha256(normalize_prompt(text).encode("utf-8")).hexdigest()
    return digest[:PROMPT_ID_LENGTH]


def escape_prompt(text: str) -> str:
    """DataForSEO decodes %XX and turns '+' into a space inside `keyword`; protect both."""
    return text.replace("%", "%25").replace("+", "%2B")


def unescape_prompt(text: str) -> str:
    """Reverse of `escape_prompt`, in one pass so an escaped '%2B' literal survives."""
    return _ESCAPED.sub(lambda m: "%" if m.group(1) == "25" else "+", text)


def make_tag(prompt_id: str, attempt: int, store_answer: bool = True) -> str:
    tag = f"{prompt_id}.{int(attempt)}"
    return tag if store_answer else f"{tag}.{_NO_ANSWER_SUFFIX}"


def parse_tag(tag) -> tuple[str | None, int | None, bool]:
    """(prompt_id, attempt, store_answer) from a tag; (None, None, True) when it isn't ours."""
    m = _TAG.fullmatch(str(tag or ""))
    if m is None:
        return None, None, True
    return m.group(1), int(m.group(2)), m.group(3) is None


def clean_url(url) -> str | None:
    """Drop the `utm_source=chatgpt.com` ChatGPT appends, so sources and search results join."""
    if not url:
        return None
    url = str(url)
    base, sep, rest = url.partition("?")
    if not sep:
        return url
    query, hash_sep, fragment = rest.partition("#")
    kept = [p for p in query.split("&") if p and tuple(p.split("=", 1)) != _TRACKING]
    out = base + ("?" + "&".join(kept) if kept else "")
    return out + (hash_sep + fragment if hash_sep else "")


def _dumps(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _title(value) -> str | None:
    return None if value is None else str(value)[:MAX_TITLE_CHARS]


def _dicts(value) -> list[dict]:
    """The dict elements of a list field; anything else in there is skipped."""
    return [v for v in value or [] if isinstance(v, dict)] if isinstance(value, list) else []


def _sources(result: dict) -> list[dict] | None:
    top = result.get("sources")
    if top is None:
        return None
    cited: Counter = Counter()
    for item in _dicts(result.get("items")):
        for src in _dicts(item.get("sources")):
            cited[clean_url(src.get("url"))] += 1
    out, seen = [], set()
    for src in _dicts(top):
        url = clean_url(src.get("url"))
        if url in seen:
            continue
        seen.add(url)
        out.append({
            "position": len(out) + 1, "url": url, "domain": src.get("domain"),
            "title": _title(src.get("title")), "source_name": src.get("source_name"),
            "publication_date": src.get("publication_date"),
            "citation_count": cited.get(url, 0),
        })
    return out


def _search_results(result: dict) -> list[dict] | None:
    top = result.get("search_results")
    if top is None:
        return None
    out, seen = [], set()
    for sr in _dicts(top):
        url = clean_url(sr.get("url"))
        if url in seen:
            continue
        seen.add(url)
        out.append({"position": len(out) + 1, "url": url, "domain": sr.get("domain"),
                    "title": _title(sr.get("title"))})
    return out


def _brand_entities(result: dict) -> list[dict] | None:
    top = result.get("brand_entities")
    if top is None:
        return None
    out = []
    for be in _dicts(top):
        # The docs put the brand name in `title`; in practice `markdown` holds the name
        # and `title` is often a descriptor ("Charter bus booking marketplace").
        name = be.get("markdown") or be.get("title")
        title = be.get("title")
        out.append({"name": name, "description": title if title and title != name else None,
                    "category": be.get("category")})
    return out


def _local_businesses(result: dict) -> list[dict] | None:
    out, found = [], False
    for item in _dicts(result.get("items")):
        if item.get("type") != "chat_gpt_local_businesses":
            continue
        found = True
        for b in _dicts(item.get("items")):
            rating = b.get("rating")
            out.append({
                "title": _title(b.get("title")), "domain": b.get("domain"),
                "url": clean_url(b.get("url")), "address": b.get("address"),
                "phone": b.get("phone"),
                "rating": rating.get("value") if isinstance(rating, dict) else rating,
                "reviews_count": b.get("reviews_count"),
            })
    return out if found else None


def _empty_row() -> dict:
    return {**{c: None for c in SCHEMA}, "task_id": None}


def local_row(prompt: str, prompt_id: str, *, attempt: int, message: str, location_code=None,
              language_code=None, force_web_search=None, status_code=None,
              task_id=None) -> dict:
    """A row for a prompt that produced no usable DataForSEO result (refused locally,
    rejected at submit, timed out), so every prompt asked for is accounted for."""
    return {**_empty_row(), "prompt_id": prompt_id, "prompt": prompt, "attempt": attempt,
            "location_code": location_code, "language_code": language_code,
            "force_web_search": force_web_search, "status_code": status_code,
            "status_message": message, "task_id": task_id}


def parse_task(task: dict, *, prompt: str | None = None) -> dict:
    """One row from one task of a task_get/advanced or live/advanced response.

    `prompt` is the caller's own text when it is known (self-contained and live modes).
    The collector has only the response, so the prompt is rebuilt by un-escaping the
    request echo; `result.keyword` is never used because it is the decoded form, which
    has already lost any '+'.
    """
    task = task if isinstance(task, dict) else {}
    data = task.get("data") or {}
    prompt_id, attempt, store_answer = parse_tag(data.get("tag"))
    if prompt is None and data.get("keyword") is not None:
        prompt = unescape_prompt(str(data["keyword"]))
    if prompt_id is None and prompt is not None:
        prompt_id = fanout_prompt_id(prompt)

    row = local_row(
        prompt, prompt_id, attempt=attempt if attempt is not None else 1,
        message=task.get("status_message"), status_code=task.get("status_code"),
        location_code=data.get("location_code"), language_code=data.get("language_code"),
        force_web_search=data.get("force_web_search"), task_id=task.get("id"),
    )
    results = task.get("result") or []
    result = results[0] if results and isinstance(results[0], dict) else None
    if task.get("status_code") != OK_STATUS or result is None:
        return row

    fan_out = result.get("fan_out_queries")
    sources = _sources(result)
    search_results = _search_results(result)
    brands = _brand_entities(result)
    businesses = _local_businesses(result)
    answer = result.get("markdown")
    row.update({
        "location_code": result.get("location_code") or row["location_code"],
        "language_code": result.get("language_code") or row["language_code"],
        "model": result.get("model"),
        "answered_at": result.get("datetime"),
        "fan_out_count": None if fan_out is None else len(fan_out),
        "source_count": len(sources or []),
        "search_result_count": len(search_results or []),
        "brand_entity_count": len(brands or []),
        "local_business_count": len(businesses or []),
        "answer_chars": len(answer or ""),
        "answer_markdown": answer if store_answer else None,
        "item_types": _dumps(result.get("item_types")),
        "fan_out_queries": _dumps(fan_out),
        "sources": _dumps(sources),
        "search_results": _dumps(search_results),
        "brand_entities": _dumps(brands),
        "local_businesses": _dumps(businesses),
    })
    return row
