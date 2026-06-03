"""Tests for MetaClient domain lookup methods."""

from __future__ import annotations

from skyward.data.meta import MetaClient


def test_clean_domain_preserve_path_true():
    """_clean_domain with preserve_path=True keeps the path."""
    assert MetaClient._clean_domain("https://www.kitchenguard.com/fw", preserve_path=True) == "kitchenguard.com/fw"
    assert MetaClient._clean_domain("example.com/fw/", preserve_path=True) == "example.com/fw"
    assert MetaClient._clean_domain("https://www.example.com:8080/path?q=1#frag", preserve_path=True) == "example.com/path"


def test_clean_domain_preserve_path_false_unchanged():
    """_clean_domain with preserve_path=False (default) still strips paths."""
    assert MetaClient._clean_domain("https://www.kitchenguard.com/fw") == "kitchenguard.com"
    assert MetaClient._clean_domain("example.com/page") == "example.com"
