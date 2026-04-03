"""
Meta tables v2 migration script.

This script:
1. Drops old Meta tables (clients, companies, company_domains, projects,
   project_companies, client_datasets)
2. Creates the redesigned Meta tables:
   - clients, domains, client_domains, projects, project_domains, client_datasets
3. Does NOT touch: Meta.dataset_catalog, Meta.table_catalog

Run with: python common/data/BigQuery/migrate_meta.py
"""

from google.cloud import bigquery

PROJECT = "data-hub-468216"
DATASET = "Meta"

client = bigquery.Client(project=PROJECT)


def drop_old_tables() -> None:
    """Drop old Meta tables that are being replaced."""

    tables_to_drop = [
        "clients",
        "companies",
        "company_domains",
        "projects",
        "project_companies",
        "client_datasets",
    ]

    for table in tables_to_drop:
        fqn = f"`{PROJECT}.{DATASET}.{table}`"
        print(f"Dropping {fqn}...")
        try:
            client.query(f"DROP TABLE IF EXISTS {fqn}").result()
            print("  OK")
        except Exception as e:
            print(f"  Error: {e}")


def create_tables() -> None:
    """Create the redesigned Meta tables."""

    ddl_statements = [
        # ── clients ──
        f"""
        CREATE TABLE IF NOT EXISTS `{PROJECT}.{DATASET}.clients` (
            client_id   STRING NOT NULL   OPTIONS(description="Unique identifier for the client (e.g. shs, goskyward)"),
            client_name STRING NOT NULL   OPTIONS(description="Display name of the client (e.g. Sears Home Services)"),
            is_active   BOOLEAN DEFAULT TRUE OPTIONS(description="Whether this client is currently active; FALSE for former clients"),
            notes       STRING           OPTIONS(description="Free-form notes about the client"),
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP() OPTIONS(description="When this client record was created")
        )
        OPTIONS(description="Registry of all clients Skyward works with")
        """,

        # ── domains ──
        f"""
        CREATE TABLE IF NOT EXISTS `{PROJECT}.{DATASET}.domains` (
            domain_id   STRING NOT NULL    OPTIONS(description="Unique identifier derived from the domain (e.g. searspartsdirect_com)"),
            domain      STRING NOT NULL    OPTIONS(description="The actual domain (e.g. searspartsdirect.com)"),
            domain_name STRING             OPTIONS(description="Human-friendly name (e.g. Sears Parts Direct); may be unknown for competitors"),
            is_active   BOOLEAN DEFAULT TRUE OPTIONS(description="Whether this domain is still being tracked; FALSE for deprecated/unused domains"),
            notes       STRING             OPTIONS(description="Free-form notes about the domain"),
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP() OPTIONS(description="When this domain record was created")
        )
        OPTIONS(description="Standalone registry of all domains tracked across clients and projects. A domain has no inherent ownership — relationships to clients are defined in client_domains.")
        """,

        # ── client_domains ──
        f"""
        CREATE TABLE IF NOT EXISTS `{PROJECT}.{DATASET}.client_domains` (
            client_id     STRING NOT NULL   OPTIONS(description="FK → clients; the client this relationship belongs to"),
            domain_id     STRING NOT NULL   OPTIONS(description="FK → domains; the domain being associated"),
            is_competitor  BOOLEAN NOT NULL  OPTIONS(description="FALSE = clients own website, TRUE = a competitors website"),
            notes         STRING            OPTIONS(description="Free-form notes about this client-domain relationship")
        )
        OPTIONS(description="Junction table mapping clients to the domains they care about, with a flag indicating whether the domain is the clients own site or a competitor.")
        """,

        # ── projects ──
        f"""
        CREATE TABLE IF NOT EXISTS `{PROJECT}.{DATASET}.projects` (
            project_id   STRING NOT NULL    OPTIONS(description="Unique identifier for the project"),
            client_id    STRING NOT NULL    OPTIONS(description="FK → clients; the client this project belongs to"),
            project_type STRING NOT NULL    OPTIONS(description="Type of project (e.g. seo_pipeline, ai_faqs, wqa)"),
            project_name STRING             OPTIONS(description="Optional display name for the project"),
            notes        STRING             OPTIONS(description="Free-form notes about the project"),
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP() OPTIONS(description="When this project record was created")
        )
        OPTIONS(description="Individual projects scoped to a client (e.g. an SEO pipeline run, an AI FAQ generation)")
        """,

        # ── project_domains ──
        f"""
        CREATE TABLE IF NOT EXISTS `{PROJECT}.{DATASET}.project_domains` (
            project_id STRING NOT NULL   OPTIONS(description="FK → projects; the project this mapping belongs to"),
            domain_id  STRING NOT NULL   OPTIONS(description="FK → domains; the domain included in the project"),
            role       STRING NOT NULL   OPTIONS(description="The domains role in this project: client or competitor"),
            priority   STRING            OPTIONS(description="Optional priority ranking for this domain within the project"),
            notes      STRING            OPTIONS(description="Free-form notes about this project-domain relationship")
        )
        OPTIONS(description="Maps which domains are included in a specific project and their role within it. A project may use only a subset of a clients domains/competitors.")
        """,

        # ── client_datasets ──
        f"""
        CREATE TABLE IF NOT EXISTS `{PROJECT}.{DATASET}.client_datasets` (
            client_id    STRING NOT NULL    OPTIONS(description="FK → clients; the client who owns this dataset"),
            domain_id    STRING             OPTIONS(description="FK → domains; optional, set when the dataset is specific to one domain rather than the client as a whole"),
            dataset_id   STRING NOT NULL    OPTIONS(description="BigQuery dataset name (e.g. analytics_1234567)"),
            dataset_type STRING NOT NULL    OPTIONS(description="Type of dataset (e.g. ga4, gsc)"),
            hostname     STRING             OPTIONS(description="Associated hostname if applicable"),
            is_active    BOOLEAN DEFAULT TRUE OPTIONS(description="Whether this dataset mapping is still active"),
            notes        STRING             OPTIONS(description="Free-form notes about this dataset mapping"),
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP() OPTIONS(description="When this dataset mapping was created")
        )
        OPTIONS(description="Maps clients to their BigQuery datasets (e.g. GA4, GSC) for automated data discovery")
        """,
    ]

    for ddl in ddl_statements:
        table_name = ddl.split(f"{DATASET}.")[1].split("`")[0]
        print(f"Creating {DATASET}.{table_name}...")
        try:
            client.query(ddl).result()
            print("  OK")
        except Exception as e:
            print(f"  Error: {e}")


def verify_tables() -> None:
    """Verify all expected tables exist and old ones are gone."""

    expected = ["clients", "domains", "client_domains", "projects", "project_domains", "client_datasets"]
    should_not_exist = ["companies", "company_domains", "project_companies"]

    print("\n--- Verifying tables ---")

    for table in expected:
        fqn = f"{PROJECT}.{DATASET}.{table}"
        try:
            t = client.get_table(fqn)
            cols = [f.name for f in t.schema]
            print(f"  ✓ {table} exists ({len(cols)} columns: {', '.join(cols)})")
        except Exception:
            print(f"  ✗ {table} MISSING")

    for table in should_not_exist:
        fqn = f"{PROJECT}.{DATASET}.{table}"
        try:
            client.get_table(fqn)
            print(f"  ✗ {table} still exists (should have been dropped)")
        except Exception:
            print(f"  ✓ {table} correctly removed")

    # Confirm untouched tables
    for table in ["dataset_catalog", "table_catalog"]:
        fqn = f"{PROJECT}.{DATASET}.{table}"
        try:
            client.get_table(fqn)
            print(f"  ✓ {table} untouched")
        except Exception:
            print(f"  ⚠ {table} not found (was it expected?)")


def main():
    print("=" * 60)
    print("Meta Tables v2 Migration")
    print("=" * 60)

    print("\nStep 1: Drop old tables")
    print("-" * 40)
    drop_old_tables()

    print("\nStep 2: Create new tables")
    print("-" * 40)
    create_tables()

    print("\nStep 3: Verify")
    print("-" * 40)
    verify_tables()

    print("\n" + "=" * 60)
    print("Migration complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
