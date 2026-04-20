"""Guard test — client.py should contain DataForSEOClient and ClientConfig only."""

from pathlib import Path

CLIENT_PY = Path(__file__).resolve().parents[1] / "src" / "skyward" / "data" / "dataforseo" / "client.py"


def test_client_py_no_longer_defines_base_endpoint():
    content = CLIENT_PY.read_text()
    assert "class BaseEndpoint" not in content, (
        "Legacy BaseEndpoint should be gone from client.py — "
        "new BaseEndpoint lives in base.py"
    )


def test_client_py_no_longer_defines_extracted_endpoints():
    content = CLIENT_PY.read_text()
    for cls in [
        "class BacklinksBacklinks",
        "class BacklinksBulkPagesSummary",
        "class SerpGoogleOrganic",
        "class DataforseoLabsGoogleKeywordSuggestions",
        "class DataforseoLabsGoogleRelatedKeywords",
        "class DataforseoLabsGoogleRankedKeywords",
        "class DataforseoLabsGoogleKeywordOverview",
        "class DataforseoLabsGoogleSearchIntent",
        "class DataforseoLabsGoogleDomainRankOverview",
        "class KeywordsDataGoogleAdsSearchVolume",
    ]:
        assert cls not in content, f"{cls} should be extracted to endpoints/ — not in client.py"


def test_client_py_is_slim():
    content = CLIENT_PY.read_text()
    line_count = len(content.splitlines())
    assert line_count < 600, f"client.py is {line_count} lines — should be under 600 after M4"
