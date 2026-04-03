"""
Seed script for Meta tables and Logs.upload_events schema updates.

This script:
1. Creates the Meta dataset and 6 tables:
   - clients, companies, company_domains, projects, project_companies, client_datasets
2. ALTERs Logs.upload_events to add client_id, project_id, notes columns
3. Seeds Meta tables with existing data from SEOPipeline

Run with: python common/data/BigQuery/seed_meta.py
"""

import os
import sys
import re

# Ensure project root is in path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

RUNNING_DIR = os.getcwd()
from common.config.settings import load_config
cfg = load_config()
os.chdir(RUNNING_DIR)

from common.data.BigQuery.bigQuery_client import BigQueryClient


def create_meta_schema(bq_client: BigQueryClient) -> None:
    """Create Meta dataset and tables if they don't exist."""

    ddl_statements = [
        # Create schema
        """
        CREATE SCHEMA IF NOT EXISTS `data-hub-468216.Meta`
        OPTIONS (location = 'US')
        """,

        # Create clients table
        """
        CREATE TABLE IF NOT EXISTS `data-hub-468216.Meta.clients` (
            client_id STRING NOT NULL,
            client_name STRING NOT NULL,
            notes STRING,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
        )
        """,

        # Create companies table
        """
        CREATE TABLE IF NOT EXISTS `data-hub-468216.Meta.companies` (
            company_id STRING NOT NULL,
            company_name STRING NOT NULL,
            client_id STRING,
            notes STRING,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
        )
        """,

        # Create company_domains table
        """
        CREATE TABLE IF NOT EXISTS `data-hub-468216.Meta.company_domains` (
            company_id STRING NOT NULL,
            domain STRING NOT NULL,
            is_primary BOOLEAN DEFAULT TRUE,
            notes STRING
        )
        """,

        # Create projects table
        """
        CREATE TABLE IF NOT EXISTS `data-hub-468216.Meta.projects` (
            project_id STRING NOT NULL,
            client_id STRING NOT NULL,
            project_type STRING NOT NULL,
            project_name STRING,
            notes STRING,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
        )
        """,

        # Create project_companies table
        """
        CREATE TABLE IF NOT EXISTS `data-hub-468216.Meta.project_companies` (
            project_id STRING NOT NULL,
            company_id STRING NOT NULL,
            role STRING NOT NULL,
            priority STRING,
            notes STRING
        )
        """,

        # Create client_datasets table (for caching GA4/GSC dataset mappings)
        """
        CREATE TABLE IF NOT EXISTS `data-hub-468216.Meta.client_datasets` (
            client_id STRING NOT NULL,
            dataset_id STRING NOT NULL,
            dataset_type STRING NOT NULL,
            hostname STRING,
            is_active BOOLEAN DEFAULT TRUE,
            notes STRING,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
        )
        """,

        # Create table_catalog (cached index of all BQ tables across all datasets)
        """
        CREATE TABLE IF NOT EXISTS `data-hub-468216.Meta.table_catalog` (
            dataset STRING NOT NULL,
            table_name STRING NOT NULL,
            row_count INT64,
            size_bytes INT64,
            is_active BOOLEAN DEFAULT TRUE,
            status_changed_at TIMESTAMP,
            notes STRING,
            last_indexed_at TIMESTAMP
        )
        """,
    ]

    for ddl in ddl_statements:
        print(f"Executing: {ddl.strip()[:60]}...")
        try:
            bq_client.client.query(ddl).result()
            print("  OK")
        except Exception as e:
            print(f"  Error: {e}")


def alter_upload_events(bq_client: BigQueryClient) -> None:
    """Add new columns to Logs.upload_events if they don't exist."""

    # BigQuery doesn't support ADD COLUMN IF NOT EXISTS in a single statement,
    # so we check the schema first and add columns individually
    table_ref = bq_client.client.get_table("data-hub-468216.Logs.upload_events")
    existing_cols = {field.name for field in table_ref.schema}

    columns_to_add = [
        ("client_id", "STRING"),
        ("project_id", "STRING"),
        ("notes", "STRING"),
    ]

    for col_name, col_type in columns_to_add:
        if col_name in existing_cols:
            print(f"Column {col_name} already exists in Logs.upload_events")
            continue

        alter_sql = f"""
            ALTER TABLE `data-hub-468216.Logs.upload_events`
            ADD COLUMN {col_name} {col_type}
        """
        print(f"Adding column {col_name} to Logs.upload_events...")
        try:
            bq_client.client.query(alter_sql).result()
            print("  OK")
        except Exception as e:
            print(f"  Error: {e}")


def normalize_id(name: str) -> str:
    """Convert a name to a normalized ID (lowercase, underscores, no special chars)."""
    # Replace spaces and hyphens with underscores
    normalized = re.sub(r"[\s\-]+", "_", name.lower())
    # Remove any non-alphanumeric characters except underscores
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return normalized


def domain_to_company_id(domain: str) -> str:
    """Derive company_id from domain (e.g., 'example.com' -> 'example_com')."""
    return normalize_id(domain.replace(".", "_"))


def seed_meta_tables(bq_client: BigQueryClient) -> None:
    """Seed Meta tables with existing data from SEOPipeline."""

    # Hardcode known data for Skyward
    hardcoded_data = {
        "clients": [
            {"client_id": "skyward", "client_name": "Skyward", "notes": "Internal"},
        ],
        "companies": [
            {"company_id": "skyward", "company_name": "Skyward", "client_id": "skyward", "notes": "Internal company"},
        ],
        "company_domains": [
            {"company_id": "skyward", "domain": "goskyward.io", "is_primary": True, "notes": None},
        ],
    }

    # Insert hardcoded data using MERGE (idempotent)
    print("\n--- Inserting hardcoded data ---")

    for client in hardcoded_data["clients"]:
        merge_sql = f"""
            MERGE `data-hub-468216.Meta.clients` T
            USING (SELECT '{client["client_id"]}' AS client_id) S
            ON T.client_id = S.client_id
            WHEN NOT MATCHED THEN
                INSERT (client_id, client_name, notes)
                VALUES ('{client["client_id"]}', '{client["client_name"]}', '{client["notes"]}')
        """
        try:
            bq_client.client.query(merge_sql).result()
            print(f"  Inserted/verified client: {client['client_id']}")
        except Exception as e:
            print(f"  Error inserting client {client['client_id']}: {e}")

    for company in hardcoded_data["companies"]:
        notes_val = f"'{company['notes']}'" if company["notes"] else "NULL"
        merge_sql = f"""
            MERGE `data-hub-468216.Meta.companies` T
            USING (SELECT '{company["company_id"]}' AS company_id) S
            ON T.company_id = S.company_id
            WHEN NOT MATCHED THEN
                INSERT (company_id, company_name, client_id, notes)
                VALUES ('{company["company_id"]}', '{company["company_name"]}', '{company["client_id"]}', {notes_val})
        """
        try:
            bq_client.client.query(merge_sql).result()
            print(f"  Inserted/verified company: {company['company_id']}")
        except Exception as e:
            print(f"  Error inserting company {company['company_id']}: {e}")

    for cd in hardcoded_data["company_domains"]:
        notes_val = f"'{cd['notes']}'" if cd["notes"] else "NULL"
        merge_sql = f"""
            MERGE `data-hub-468216.Meta.company_domains` T
            USING (SELECT '{cd["company_id"]}' AS company_id, '{cd["domain"]}' AS domain) S
            ON T.company_id = S.company_id AND T.domain = S.domain
            WHEN NOT MATCHED THEN
                INSERT (company_id, domain, is_primary, notes)
                VALUES ('{cd["company_id"]}', '{cd["domain"]}', {str(cd["is_primary"]).upper()}, {notes_val})
        """
        try:
            bq_client.client.query(merge_sql).result()
            print(f"  Inserted/verified company_domain: {cd['company_id']} -> {cd['domain']}")
        except Exception as e:
            print(f"  Error inserting company_domain: {e}")

    # Query SEOPipeline.projects for existing data
    print("\n--- Seeding from SEOPipeline.projects ---")

    try:
        projects_query = """
            SELECT DISTINCT
                client_name,
                project_id,
                domain,
                project_type
            FROM `data-hub-468216.SEOPipeline.projects`
            WHERE client_name IS NOT NULL AND project_id IS NOT NULL
        """
        projects_df = bq_client.client.query(projects_query).result().to_dataframe()
        print(f"  Found {len(projects_df)} projects in SEOPipeline.projects")
    except Exception as e:
        print(f"  Error querying SEOPipeline.projects: {e}")
        projects_df = None

    # Query SEOPipeline.competitors for competitor domains
    print("\n--- Seeding from SEOPipeline.competitors ---")

    try:
        competitors_query = """
            SELECT DISTINCT
                project_id,
                domain,
                priority
            FROM `data-hub-468216.SEOPipeline.competitors`
            WHERE project_id IS NOT NULL AND domain IS NOT NULL
        """
        competitors_df = bq_client.client.query(competitors_query).result().to_dataframe()
        print(f"  Found {len(competitors_df)} competitor entries in SEOPipeline.competitors")
    except Exception as e:
        print(f"  Error querying SEOPipeline.competitors: {e}")
        competitors_df = None

    # Process projects data
    if projects_df is not None and not projects_df.empty:
        print("\n--- Processing projects data ---")

        # Extract unique clients
        unique_clients = projects_df["client_name"].dropna().unique()
        for client_name in unique_clients:
            client_id = normalize_id(client_name)
            merge_sql = f"""
                MERGE `data-hub-468216.Meta.clients` T
                USING (SELECT '{client_id}' AS client_id) S
                ON T.client_id = S.client_id
                WHEN NOT MATCHED THEN
                    INSERT (client_id, client_name, notes)
                    VALUES ('{client_id}', '{client_name}', 'Auto-seeded from SEOPipeline.projects')
            """
            try:
                bq_client.client.query(merge_sql).result()
                print(f"  Inserted/verified client: {client_id}")
            except Exception as e:
                print(f"  Error inserting client {client_id}: {e}")

        # Extract unique domains and create companies
        unique_domains = projects_df["domain"].dropna().unique()
        for domain in unique_domains:
            company_id = domain_to_company_id(domain)
            # Try to find which client this domain belongs to
            client_row = projects_df[projects_df["domain"] == domain].iloc[0]
            client_id = normalize_id(client_row["client_name"]) if client_row["client_name"] else None
            client_id_sql = f"'{client_id}'" if client_id else "NULL"

            # Insert company
            merge_sql = f"""
                MERGE `data-hub-468216.Meta.companies` T
                USING (SELECT '{company_id}' AS company_id) S
                ON T.company_id = S.company_id
                WHEN NOT MATCHED THEN
                    INSERT (company_id, company_name, client_id, notes)
                    VALUES ('{company_id}', '{domain}', {client_id_sql}, 'Auto-seeded from SEOPipeline.projects')
            """
            try:
                bq_client.client.query(merge_sql).result()
                print(f"  Inserted/verified company: {company_id}")
            except Exception as e:
                print(f"  Error inserting company {company_id}: {e}")

            # Insert company_domain
            merge_sql = f"""
                MERGE `data-hub-468216.Meta.company_domains` T
                USING (SELECT '{company_id}' AS company_id, '{domain}' AS domain) S
                ON T.company_id = S.company_id AND T.domain = S.domain
                WHEN NOT MATCHED THEN
                    INSERT (company_id, domain, is_primary, notes)
                    VALUES ('{company_id}', '{domain}', TRUE, 'Auto-seeded from SEOPipeline.projects')
            """
            try:
                bq_client.client.query(merge_sql).result()
                print(f"  Inserted/verified company_domain: {company_id} -> {domain}")
            except Exception as e:
                print(f"  Error inserting company_domain: {e}")

        # Insert projects
        for _, row in projects_df.iterrows():
            project_id = row["project_id"]
            client_id = normalize_id(row["client_name"]) if row["client_name"] else "unknown"
            project_type = row["project_type"] if row["project_type"] else "unknown"

            merge_sql = f"""
                MERGE `data-hub-468216.Meta.projects` T
                USING (SELECT '{project_id}' AS project_id) S
                ON T.project_id = S.project_id
                WHEN NOT MATCHED THEN
                    INSERT (project_id, client_id, project_type, project_name, notes)
                    VALUES ('{project_id}', '{client_id}', '{project_type}', NULL, 'Auto-seeded from SEOPipeline.projects')
            """
            try:
                bq_client.client.query(merge_sql).result()
                print(f"  Inserted/verified project: {project_id}")
            except Exception as e:
                print(f"  Error inserting project {project_id}: {e}")

            # Link project to client domain as "client" role
            if row["domain"]:
                company_id = domain_to_company_id(row["domain"])
                merge_sql = f"""
                    MERGE `data-hub-468216.Meta.project_companies` T
                    USING (SELECT '{project_id}' AS project_id, '{company_id}' AS company_id) S
                    ON T.project_id = S.project_id AND T.company_id = S.company_id
                    WHEN NOT MATCHED THEN
                        INSERT (project_id, company_id, role, priority, notes)
                        VALUES ('{project_id}', '{company_id}', 'client', NULL, 'Auto-seeded from SEOPipeline.projects')
                """
                try:
                    bq_client.client.query(merge_sql).result()
                    print(f"  Linked project {project_id} to company {company_id} (client)")
                except Exception as e:
                    print(f"  Error linking project to company: {e}")

    # Process competitors data
    if competitors_df is not None and not competitors_df.empty:
        print("\n--- Processing competitors data ---")

        for _, row in competitors_df.iterrows():
            project_id = row["project_id"]
            domain = row["domain"]
            priority = row["priority"] if row["priority"] else None
            company_id = domain_to_company_id(domain)
            priority_sql = f"'{priority}'" if priority else "NULL"

            # Insert company if not exists
            merge_sql = f"""
                MERGE `data-hub-468216.Meta.companies` T
                USING (SELECT '{company_id}' AS company_id) S
                ON T.company_id = S.company_id
                WHEN NOT MATCHED THEN
                    INSERT (company_id, company_name, client_id, notes)
                    VALUES ('{company_id}', '{domain}', NULL, 'Auto-seeded from SEOPipeline.competitors')
            """
            try:
                bq_client.client.query(merge_sql).result()
            except Exception as e:
                pass  # Likely already exists

            # Insert company_domain if not exists
            merge_sql = f"""
                MERGE `data-hub-468216.Meta.company_domains` T
                USING (SELECT '{company_id}' AS company_id, '{domain}' AS domain) S
                ON T.company_id = S.company_id AND T.domain = S.domain
                WHEN NOT MATCHED THEN
                    INSERT (company_id, domain, is_primary, notes)
                    VALUES ('{company_id}', '{domain}', TRUE, 'Auto-seeded from SEOPipeline.competitors')
            """
            try:
                bq_client.client.query(merge_sql).result()
            except Exception as e:
                pass  # Likely already exists

            # Link project to competitor domain as "competitor" role
            merge_sql = f"""
                MERGE `data-hub-468216.Meta.project_companies` T
                USING (SELECT '{project_id}' AS project_id, '{company_id}' AS company_id) S
                ON T.project_id = S.project_id AND T.company_id = S.company_id
                WHEN NOT MATCHED THEN
                    INSERT (project_id, company_id, role, priority, notes)
                    VALUES ('{project_id}', '{company_id}', 'competitor', {priority_sql}, 'Auto-seeded from SEOPipeline.competitors')
            """
            try:
                bq_client.client.query(merge_sql).result()
                print(f"  Linked project {project_id} to competitor {company_id}")
            except Exception as e:
                print(f"  Error linking project to competitor: {e}")


def main():
    """Main entry point for seeding Meta tables."""
    print("=" * 60)
    print("Meta Tables Seed Script")
    print("=" * 60)

    # Initialize BigQuery client
    print("\nInitializing BigQuery client...")
    bq_client = BigQueryClient(
        credentials_info=cfg.datahub_credentials,
        project_id=cfg.datahub_project_id
    )
    print("  OK")

    # Step 1: Create Meta schema and tables
    print("\n" + "=" * 60)
    print("Step 1: Creating Meta dataset and tables")
    print("=" * 60)
    create_meta_schema(bq_client)

    # Step 2: ALTER Logs.upload_events
    print("\n" + "=" * 60)
    print("Step 2: Altering Logs.upload_events")
    print("=" * 60)
    alter_upload_events(bq_client)

    # Step 3: Seed Meta tables with existing data
    print("\n" + "=" * 60)
    print("Step 3: Seeding Meta tables")
    print("=" * 60)
    seed_meta_tables(bq_client)

    print("\n" + "=" * 60)
    print("Seed script completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
