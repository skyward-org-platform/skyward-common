"""backlinks_referring_domains — v3/backlinks/referring_domains/live

Fetches referring domains pointing to a target domain, subdomain, or webpage
with authority, link attribute, TLD, platform, semantic location, and country breakdowns.
"""

from __future__ import annotations

from skyward.data.dataforseo.endpoints._backlinks_common import PaginatedBacklinksEndpoint


class BacklinksReferringDomains(PaginatedBacklinksEndpoint):
    """
    Endpoint: v3/backlinks/referring_domains/live

    Retrieves referring domains pointing to a target with per-domain backlink metrics.
    """

    LIVE_URL = "backlinks/referring_domains/live"
    TABLE_NAME = "backlinks-referring_domains"
    ITEM_KEY = "domain"
    # Stored as `referring_domain` so BaseEndpoint._stamp_fetch_metadata's client
    # `domain` column does not overwrite the referring domain value.
    ENTITY_COL = "referring_domain"

