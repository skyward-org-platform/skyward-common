"""backlinks_anchors — v3/backlinks/anchors/live

Fetches anchor texts pointing to a target domain, subdomain, or webpage with
per-anchor backlink, referring-domain, link attribute, and location breakdowns.
"""

from __future__ import annotations

from skyward.data.dataforseo.endpoints._backlinks_common import PaginatedBacklinksEndpoint


class BacklinksAnchors(PaginatedBacklinksEndpoint):
    """
    Endpoint: v3/backlinks/anchors/live

    Retrieves anchor texts pointing to a target with per-anchor backlink metrics.
    """

    LIVE_URL = "backlinks/anchors/live"
    TABLE_NAME = "backlinks-anchors"
    ITEM_KEY = "anchor"
    ENTITY_COL = "anchor"

