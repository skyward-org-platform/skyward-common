"""Allowlist of standard-mode endpoints the collector is allowed to drain.

`tasks_ready` is per-endpoint, so the collector only polls endpoints registered here —
anything else is out of scope by design (no orphan-draining of endpoints we don't support).
Each handler carries the tasks_ready / task_get URLs, the canonical table, and a parser
(the endpoint's own `_parse_response`, which stamps task_id per row).

To support a new standard-mode endpoint: give it a `COLLECTOR_ENDPOINT_KEY` and add it to
`_STANDARD_ENDPOINTS` below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

# Attribute names on the client for each supported standard-mode endpoint.
_STANDARD_ENDPOINTS = (
    "serp_google_organic",
    "keywords_data_google_ads_search_volume",
)


@dataclass
class EndpointHandler:
    key: str
    ready_url: str
    get_url: str
    canonical_table: str
    parse: Callable[[dict, object], pd.DataFrame]  # (raw task_get response, keyword) -> df
    cast: Callable[[pd.DataFrame], pd.DataFrame]    # endpoint type coercion for the canonical load


def build_allowlist(client) -> dict[str, EndpointHandler]:
    """Build {endpoint_key: EndpointHandler} from the client's standard-mode endpoints."""
    handlers: dict[str, EndpointHandler] = {}
    for attr in _STANDARD_ENDPOINTS:
        ep = getattr(client, attr)
        handlers[ep.COLLECTOR_ENDPOINT_KEY] = EndpointHandler(
            key=ep.COLLECTOR_ENDPOINT_KEY,
            ready_url=ep.READY_URL,
            get_url=ep.GET_URL,
            canonical_table=ep.TABLE_NAME,
            parse=(lambda raw, keyword, ep=ep: ep._parse_response(raw, keyword)),
            cast=(lambda df, ep=ep: ep._cast_types(df)),
        )
    return handlers
