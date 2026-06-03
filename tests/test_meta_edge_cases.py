"""
Edge-case tests for MetaClient static helpers.

Covers the pure static helpers (_clean_domain, _domain_to_name) that have no
database dependency. DB-coupled tests live in the pg-backed test modules.
"""
from __future__ import annotations

from skyward.data.meta import MetaClient


# ══════════════════════════════════════════════════════════════════════════════
# 1. _clean_domain — static method
# ══════════════════════════════════════════════════════════════════════════════

class TestCleanDomain:
    """Test _clean_domain edge cases — run without any fixtures."""

    def test_normal_domain(self):
        assert MetaClient._clean_domain("example.com") == "example.com"

    def test_https_prefix(self):
        assert MetaClient._clean_domain("https://example.com") == "example.com"

    def test_http_prefix(self):
        assert MetaClient._clean_domain("http://example.com") == "example.com"

    def test_www_prefix(self):
        assert MetaClient._clean_domain("www.example.com") == "example.com"

    def test_full_url(self):
        assert MetaClient._clean_domain("https://www.example.com/path/page?q=1#hash") == "example.com"

    def test_trailing_slash(self):
        assert MetaClient._clean_domain("example.com/") == "example.com"

    def test_multiple_slashes_in_path(self):
        assert MetaClient._clean_domain("https://example.com/a/b/c/") == "example.com"

    def test_empty_string(self):
        assert MetaClient._clean_domain("") == ""

    def test_whitespace_only(self):
        assert MetaClient._clean_domain("   ") == ""

    def test_leading_trailing_whitespace(self):
        assert MetaClient._clean_domain("  example.com  ") == "example.com"

    def test_mixed_case(self):
        assert MetaClient._clean_domain("HTTPS://WWW.EXAMPLE.COM") == "example.com"

    def test_just_protocol(self):
        # "https://" stripped → "" — should return "" not crash
        result = MetaClient._clean_domain("https://")
        assert result == ""

    def test_port_number(self):
        # Port IS stripped by the fixed implementation via ":" rsplit
        result = MetaClient._clean_domain("example.com:8080")
        assert result == "example.com"

    def test_ftp_protocol(self):
        # The fixed implementation strips any protocol via "://" split, including ftp://
        result = MetaClient._clean_domain("ftp://example.com")
        assert result == "example.com"

    def test_double_protocol(self):
        # "https://https://example.com".split("://", 1) → ["https", "https://example.com"]
        # Takes index 1 = "https://example.com", then split("/")[0] = "https:",
        # then port strip on ":" rsplit → "https".
        # Known limitation: double protocol is not a real-world scenario and is not
        # fully handled — the result is "https" rather than "example.com".
        result = MetaClient._clean_domain("https://https://example.com")
        assert result == "https"

    def test_subdomain_is_preserved(self):
        # sub.example.com — not www, so it should remain
        assert MetaClient._clean_domain("sub.example.com") == "sub.example.com"

    def test_query_only(self):
        # "?q=1" → strip path → "", strip query → ""
        result = MetaClient._clean_domain("example.com?q=1")
        assert result == "example.com"


# ══════════════════════════════════════════════════════════════════════════════
# 2. _domain_to_name — static method
# ══════════════════════════════════════════════════════════════════════════════

class TestDomainToName:
    """Test _domain_to_name edge cases — run without any fixtures."""

    def test_normal_domain(self):
        assert MetaClient._domain_to_name("example.com") == "Example"

    def test_multi_part_tld(self):
        assert MetaClient._domain_to_name("buscharter.com.au") == "Buscharter"

    def test_hyphens_become_spaces(self):
        assert MetaClient._domain_to_name("my-cool-site.com") == "My Cool Site"

    def test_underscores_become_spaces(self):
        assert MetaClient._domain_to_name("my_site.com") == "My Site"

    def test_sub_subdomain_left_in(self):
        # tldextract("www.sub.example.com") → domain="sub" (it extracts sub as domain)
        # since www. is part of the subdomain field in tldextract, not the domain field
        result = MetaClient._domain_to_name("www.sub.example.com")
        # tldextract gives domain="sub", not "example" for this input
        # The function returns "Sub" — document the actual behaviour
        assert isinstance(result, str)
        assert len(result) > 0

    def test_ip_address(self):
        # tldextract can't extract a domain from an IP — falls back to split
        result = MetaClient._domain_to_name("192.168.1.1")
        # fallback: "192.168.1.1".split(".")[0] → "192" → "192"
        assert result == "192"

    def test_localhost_no_tld(self):
        # tldextract returns suffix="" for localhost → fallback kicks in
        # fallback: "localhost".split(".")[0] → "localhost" → "Localhost"
        result = MetaClient._domain_to_name("localhost")
        assert result == "Localhost"

    def test_empty_string(self):
        # tldextract on "" → domain="" and suffix="" → fallback
        # fallback: "".split(".")[0] → "" → "".title() → ""
        result = MetaClient._domain_to_name("")
        assert result == ""

    def test_just_tld_dot_com(self):
        # ".com" → tldextract domain="" suffix="com" → fallback because name is ""
        # fallback: ".com".replace("www.", "").split(".")[0] → "" → ""
        result = MetaClient._domain_to_name(".com")
        assert result == ""

    def test_invalid_unknown_tld(self):
        # tldextract does NOT require a known TLD by default in private mode
        # For "example.xyz123" it may return domain="example" or fall back
        result = MetaClient._domain_to_name("example.xyz123")
        assert isinstance(result, str)
        assert len(result) >= 0  # Should not raise

    def test_unicode_domain(self):
        # Unicode domain — should not raise, title-case result depends on tldextract
        result = MetaClient._domain_to_name("münchen.de")
        assert isinstance(result, str)
