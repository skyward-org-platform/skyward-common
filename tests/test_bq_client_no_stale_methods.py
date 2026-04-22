"""Guard test — ensures no caller exists for the to-be-deleted BQ methods.

NOTE: This guard only covers skyward-common. Before the migration ships,
the user must separately audit `skyward-seo`, `skyward-ai-faqs`, and
`skyward-data-hub-admin` for the same patterns.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _grep_sources(needle: str) -> list[tuple[Path, int, str]]:
    matches = []
    for py_file in ROOT.rglob("*.py"):
        if "data/bigquery/client.py" in str(py_file):
            continue
        if py_file.name == "test_bq_client_no_stale_methods.py":
            continue
        for i, line in enumerate(py_file.read_text().splitlines(), start=1):
            if needle in line:
                matches.append((py_file, i, line.strip()))
    return matches


def test_no_callers_of_get_client_domains_on_bq_client():
    patterns = [
        "bq_client.get_client_domains",
        "bq.get_client_domains",
        "BigQueryClient.get_client_domains",
    ]
    offenders = []
    for p in patterns:
        offenders += _grep_sources(p)
    assert not offenders, f"Found stale callers of BigQueryClient.get_client_domains:\n{offenders}"


def test_no_callers_of_get_project_domains_on_bq_client():
    patterns = [
        "bq_client.get_project_domains",
        "bq.get_project_domains",
        "BigQueryClient.get_project_domains",
    ]
    offenders = []
    for p in patterns:
        offenders += _grep_sources(p)
    assert not offenders, f"Found stale callers of BigQueryClient.get_project_domains:\n{offenders}"
