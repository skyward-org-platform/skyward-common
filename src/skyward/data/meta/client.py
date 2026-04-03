from __future__ import annotations

import pandas as pd
from typing import Optional, List
from google.cloud import bigquery


class MetaClient:
    """Shared Meta table operations — clients, domains, projects, datasets."""

    VALID_PRIORITIES = ("VERY LOW", "LOW", "NORMAL", "HIGH", "VERY HIGH")

    DEFAULT_DATASET_PREFIXES = {
        "ga4": ["analytics_"],
        "gsc": ["jepto_gsc_", "searchconsole_"],
        "gmb": ["jepto_gmb_"],
        "facebook": ["jepto_facebook_"],
    }

    def __init__(self, bq_client):
        self.bq = bq_client
        self._project_id = bq_client.client.project
        self._max_ids = {}  # Cache: (dataset, table, id_column) -> max_id

    # ══════════════════════════════════════════════════════════════════════════
    # ID generation
    # ══════════════════════════════════════════════════════════════════════════

    def get_next_id(self, table: str, id_column: str, dataset: str = "Meta") -> int:
        """Get the next auto-incremented integer ID for any BQ table."""
        query = f"""
            SELECT MAX({id_column}) AS max_id
            FROM `{self._project_id}.{dataset}.{table}`
        """
        df = self.bq.client.query(query).result().to_dataframe()
        max_id = df["max_id"].iloc[0]
        if max_id is None or pd.isna(max_id):
            next_id = 1
        else:
            next_id = int(max_id) + 1
        # Update cache
        self._max_ids[(dataset, table, id_column)] = next_id
        return next_id

    def get_max_id(self, table: str, id_column: str, dataset: str = "Meta") -> int:
        """Get the current max ID from cache or BQ."""
        cache_key = (dataset, table, id_column)
        if cache_key not in self._max_ids:
            query = f"""
                SELECT MAX({id_column}) AS max_id
                FROM `{self._project_id}.{dataset}.{table}`
            """
            df = self.bq.client.query(query).result().to_dataframe()
            max_id = df["max_id"].iloc[0]
            self._max_ids[cache_key] = int(max_id) if max_id is not None and not pd.isna(max_id) else 0
        return self._max_ids[cache_key]

    @staticmethod
    def format_id(id_value: int, max_id: int) -> str:
        """Format an ID with zero-padding based on the current max ID.

        Examples:
            format_id(1, 5) -> "1"
            format_id(1, 47) -> "01"
            format_id(1, 150) -> "001"
            format_id(1, 1000) -> "0001"
        """
        if max_id <= 0:
            return str(id_value)
        width = len(str(max_id))
        return f"{id_value:0{width}d}"

    # ══════════════════════════════════════════════════════════════════════════
    # Client CRUD
    # ══════════════════════════════════════════════════════════════════════════

    def list_clients(self, search: Optional[str] = None, include_counts: bool = False) -> pd.DataFrame:
        """Get all clients from Meta.clients, optionally filtered by search term.

        Search matches against client_name and abbreviation (case-insensitive).
        If include_counts=True, adds domain_count, competitor_count, project_count columns.
        """
        if include_counts:
            count_subqueries = """,
                (SELECT COUNT(*) FROM `{project}.Meta.client_domains` cd
                 WHERE cd.client_id = c.client_id AND cd.is_competitor = FALSE) AS domain_count,
                (SELECT COUNT(*) FROM `{project}.Meta.client_domains` cd
                 WHERE cd.client_id = c.client_id AND cd.is_competitor = TRUE) AS competitor_count,
                (SELECT COUNT(*) FROM `{project}.Meta.projects` p
                 WHERE p.client_id = c.client_id) AS project_count""".format(project=self._project_id)
        else:
            count_subqueries = ""

        if search:
            query = f"""
                SELECT c.client_id, c.client_name, c.abbreviation, c.is_active, c.notes, c.created_at
                    {count_subqueries}
                FROM `{self._project_id}.Meta.clients` c
                WHERE LOWER(c.client_name) LIKE CONCAT('%', LOWER(@search), '%')
                   OR LOWER(c.abbreviation) LIKE CONCAT('%', LOWER(@search), '%')
                ORDER BY c.client_name
            """
            params = [bigquery.ScalarQueryParameter("search", "STRING", search)]
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

        query = f"""
            SELECT c.client_id, c.client_name, c.abbreviation, c.is_active, c.notes, c.created_at
                {count_subqueries}
            FROM `{self._project_id}.Meta.clients` c
            ORDER BY c.client_name
        """
        return self.bq.client.query(query).result().to_dataframe()

    def add_client(
        self,
        client_name: str,
        abbreviation: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        """
        Insert a new client with an auto-generated ID.

        Args:
            client_name: Display name for the client
            abbreviation: Short abbreviation (e.g. "TNA", "SHS")
            notes: Optional notes

        Returns:
            The generated client_id
        """
        client_id = self.get_next_id("clients", "client_id")

        query = f"""
            INSERT INTO `{self._project_id}.Meta.clients` (client_id, client_name, abbreviation, notes)
            VALUES (@client_id, @client_name, @abbreviation, @notes)
        """

        params = [
            bigquery.ScalarQueryParameter("client_id", "INT64", client_id),
            bigquery.ScalarQueryParameter("client_name", "STRING", client_name),
            bigquery.ScalarQueryParameter("abbreviation", "STRING", abbreviation),
            bigquery.ScalarQueryParameter("notes", "STRING", notes),
        ]

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        self.bq.client.query(query, job_config=job_config).result()
        return client_id

    def update_client(self, client_id: int, **fields) -> None:
        """
        Update client fields dynamically.

        Args:
            client_id: The client to update
            **fields: Keyword arguments matching column names to update
                      (e.g., client_name="New Name", is_active=False)
        """
        if not fields:
            return

        set_clauses = []
        params = []
        for key, value in fields.items():
            set_clauses.append(f"{key} = @{key}")
            if isinstance(value, bool):
                params.append(bigquery.ScalarQueryParameter(key, "BOOL", value))
            else:
                params.append(bigquery.ScalarQueryParameter(key, "STRING", value))

        params.append(bigquery.ScalarQueryParameter("client_id", "INT64", client_id))

        query = f"""
            UPDATE `{self._project_id}.Meta.clients`
            SET {", ".join(set_clauses)}
            WHERE client_id = @client_id
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        self.bq.client.query(query, job_config=job_config).result()

    def deactivate_client(self, client_id: int, cascade: bool = False) -> None:
        """
        Deactivate a client, optionally cascading to domains and datasets.

        Args:
            client_id: The client to deactivate
            cascade: If True, also deactivates linked domains and client_datasets
        """
        if cascade:
            # Get domain_ids linked to this client
            domain_query = f"""
                SELECT domain_id
                FROM `{self._project_id}.Meta.client_domains`
                WHERE client_id = @client_id
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("client_id", "INT64", client_id)
                ]
            )
            domain_df = self.bq.client.query(domain_query, job_config=job_config).result().to_dataframe()

            # Deactivate domains that aren't linked to any OTHER active client
            if not domain_df.empty:
                domain_ids = domain_df["domain_id"].tolist()
                deactivate_domains_query = f"""
                    UPDATE `{self._project_id}.Meta.domains`
                    SET is_active = FALSE
                    WHERE domain_id IN UNNEST(@domain_ids)
                    AND domain_id NOT IN (
                        SELECT cd.domain_id
                        FROM `{self._project_id}.Meta.client_domains` cd
                        JOIN `{self._project_id}.Meta.clients` c ON cd.client_id = c.client_id
                        WHERE c.is_active = TRUE AND cd.client_id != @client_id
                    )
                """
                params = [
                    bigquery.ArrayQueryParameter("domain_ids", "INT64", domain_ids),
                    bigquery.ScalarQueryParameter("client_id", "INT64", client_id),
                ]
                job_config_d = bigquery.QueryJobConfig(query_parameters=params)
                self.bq.client.query(deactivate_domains_query, job_config=job_config_d).result()

            # Deactivate linked client_datasets
            dataset_update = f"""
                UPDATE `{self._project_id}.Meta.client_datasets`
                SET is_active = FALSE
                WHERE client_id = @client_id
            """
            job_config2 = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("client_id", "INT64", client_id)
                ]
            )
            self.bq.client.query(dataset_update, job_config=job_config2).result()

        self.update_client(client_id, is_active=False)

    # ══════════════════════════════════════════════════════════════════════════
    # Domain CRUD (Meta.domains + Meta.client_domains)
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _domain_to_name(domain: str) -> str:
        """Convert a domain string to a display name.
        Uses tldextract to handle multi-part TLDs like .com.au, .co.uk.
        Falls back to stripping last dot-segment if tldextract can't parse."""
        import tldextract
        extracted = tldextract.extract(domain)
        name = extracted.domain  # e.g. "buscharter" from "buscharter.com.au"
        # Fallback: if tldextract gives empty or the raw suffix is empty
        # (meaning it couldn't parse the TLD), use everything before the first dot
        if not name or not extracted.suffix:
            name = domain.lower().replace("www.", "").split(".")[0]
        name = name.replace("-", " ").replace("_", " ").title()
        return name

    @staticmethod
    def _clean_domain(raw: str) -> str:
        """Extract bare domain from a URL or domain string.
        Strips protocol, www., paths, query strings, port numbers, and trailing slashes."""
        d = raw.strip().lower()
        # Strip any protocol (http, https, ftp, etc.)
        if "://" in d:
            d = d.split("://", 1)[1]
        # Strip www.
        if d.startswith("www."):
            d = d[4:]
        # Strip path, query, fragment
        d = d.split("/")[0]
        d = d.split("?")[0]
        d = d.split("#")[0]
        # Strip port number
        if ":" in d:
            d = d.rsplit(":", 1)[0]
        return d.strip()

    def get_client_domains(self, client_id: int, is_competitor: Optional[bool] = None) -> pd.DataFrame:
        params = [bigquery.ScalarQueryParameter("client_id", "INT64", client_id)]
        competitor_filter = ""
        if is_competitor is not None:
            competitor_filter = "AND cd.is_competitor = @is_competitor"
            params.append(bigquery.ScalarQueryParameter("is_competitor", "BOOL", is_competitor))
        query = f"""
            SELECT d.domain_id, d.domain, d.domain_name, d.is_active, cd.is_competitor, cd.priority, d.notes
            FROM `{self._project_id}.Meta.client_domains` cd
            JOIN `{self._project_id}.Meta.domains` d ON cd.domain_id = d.domain_id
            WHERE cd.client_id = @client_id {competitor_filter}
            ORDER BY cd.is_competitor, d.domain
        """
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

    def add_domains(self, domains: List[str], client_id: int, is_competitor: bool, priority: str = "NORMAL") -> List[dict]:
        """Bulk-add domains and link them to a client. Batched for performance."""
        # Clean input — extract bare domains from URLs
        clean_domains = list(dict.fromkeys(self._clean_domain(d) for d in domains if d.strip()))
        clean_domains = [d for d in clean_domains if d]  # Remove empties
        if not clean_domains:
            return []

        # 1. Batch check which domains already exist (single query)
        check_query = f"""
            SELECT domain_id, domain FROM `{self._project_id}.Meta.domains`
            WHERE domain IN UNNEST(@domains)
        """
        check_params = [bigquery.ArrayQueryParameter("domains", "STRING", clean_domains)]
        check_config = bigquery.QueryJobConfig(query_parameters=check_params)
        existing_df = self.bq.client.query(check_query, job_config=check_config).result().to_dataframe()
        existing_map = dict(zip(existing_df["domain"].tolist(), existing_df["domain_id"].tolist())) if not existing_df.empty else {}

        # 2. Get next ID once, increment in Python for new domains
        new_domains = [d for d in clean_domains if d not in existing_map]
        if new_domains:
            next_num = self.get_next_id("domains", "domain_id")

            # Build rows for bulk insert into Meta.domains
            domain_rows = []
            for domain in new_domains:
                domain_id = next_num
                existing_map[domain] = domain_id
                domain_rows.append({
                    "domain_id": domain_id,
                    "domain": domain,
                    "domain_name": self._domain_to_name(domain),
                })
                next_num += 1

            # Bulk insert new domains
            domains_df = pd.DataFrame(domain_rows)
            domains_df["domain_id"] = domains_df["domain_id"].astype("int64")
            table_ref = f"{self._project_id}.Meta.domains"
            job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
            self.bq.client.load_table_from_dataframe(domains_df, table_ref, job_config=job_config).result()

        # 3. Check which client_domains links already exist
        all_domain_ids = [existing_map[d] for d in clean_domains]
        existing_links_query = f"""
            SELECT domain_id FROM `{self._project_id}.Meta.client_domains`
            WHERE client_id = @client_id AND domain_id IN UNNEST(@domain_ids)
        """
        link_check_params = [
            bigquery.ScalarQueryParameter("client_id", "INT64", client_id),
            bigquery.ArrayQueryParameter("domain_ids", "INT64", all_domain_ids),
        ]
        link_check_config = bigquery.QueryJobConfig(query_parameters=link_check_params)
        existing_links_df = self.bq.client.query(existing_links_query, job_config=link_check_config).result().to_dataframe()
        already_linked = set(existing_links_df["domain_id"].tolist()) if not existing_links_df.empty else set()

        # 4. Bulk insert only new client_domains links
        link_rows = []
        skipped = []
        for domain in clean_domains:
            domain_id = existing_map[domain]
            if domain_id in already_linked:
                skipped.append(domain)
                continue
            link_rows.append({
                "client_id": client_id,
                "domain_id": domain_id,
                "is_competitor": is_competitor,
                "priority": priority.upper() if priority else "NORMAL",
            })

        if link_rows:
            links_df = pd.DataFrame(link_rows)
            links_df["client_id"] = links_df["client_id"].astype("int64")
            links_df["domain_id"] = links_df["domain_id"].astype("int64")
            link_table_ref = f"{self._project_id}.Meta.client_domains"
            link_job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
            self.bq.client.load_table_from_dataframe(links_df, link_table_ref, job_config=link_job_config).result()

        # 5. Return results
        return [
            {"domain_id": existing_map[d], "domain": d, "domain_name": self._domain_to_name(d), "skipped": d in skipped}
            for d in clean_domains
        ]

    def update_domain(self, domain_id: int, domain_name: Optional[str] = None, is_active: Optional[bool] = None, notes: Optional[str] = None) -> None:
        set_clauses = []
        params = [bigquery.ScalarQueryParameter("domain_id", "INT64", domain_id)]
        if domain_name is not None:
            set_clauses.append("domain_name = @domain_name")
            params.append(bigquery.ScalarQueryParameter("domain_name", "STRING", domain_name))
        if is_active is not None:
            set_clauses.append("is_active = @is_active")
            params.append(bigquery.ScalarQueryParameter("is_active", "BOOL", is_active))
        if notes is not None:
            set_clauses.append("notes = @notes")
            params.append(bigquery.ScalarQueryParameter("notes", "STRING", notes))
        if not set_clauses:
            return
        query = f"""
            UPDATE `{self._project_id}.Meta.domains`
            SET {', '.join(set_clauses)}
            WHERE domain_id = @domain_id
        """
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        self.bq.client.query(query, job_config=job_config).result()

    def update_domains_batch(self, rows: list) -> None:
        """Batch-update multiple domains using parameterized queries.

        Args:
            rows: List of Series/dicts with domain_id, domain_name, is_active, notes
        """
        if not rows:
            return

        for row in rows:
            self.update_domain(
                domain_id=row["domain_id"],
                domain_name=row.get("domain_name"),
                is_active=row.get("is_active"),
                notes=row.get("notes"),
            )

    def update_client_domains_priority_batch(self, client_id: int, rows: list) -> None:
        """Batch-update priority on client_domains using individual parameterized updates.

        Args:
            client_id: The client these domains belong to
            rows: List of Series/dicts with domain_id and priority
        """
        if not rows:
            return

        for row in rows:
            domain_id = row["domain_id"]
            priority = str(row.get("priority") or "NORMAL").upper()
            if priority not in self.VALID_PRIORITIES:
                priority = "NORMAL"

            query = f"""
                UPDATE `{self._project_id}.Meta.client_domains`
                SET priority = @priority
                WHERE client_id = @client_id AND domain_id = @domain_id
            """
            params = [
                bigquery.ScalarQueryParameter("priority", "STRING", priority),
                bigquery.ScalarQueryParameter("client_id", "INT64", client_id),
                bigquery.ScalarQueryParameter("domain_id", "INT64", domain_id),
            ]
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            self.bq.client.query(query, job_config=job_config).result()

    def remove_client_domain(self, client_id: int, domain_id: int) -> None:
        query = f"""
            DELETE FROM `{self._project_id}.Meta.client_domains`
            WHERE client_id = @client_id AND domain_id = @domain_id
        """
        params = [
            bigquery.ScalarQueryParameter("client_id", "INT64", client_id),
            bigquery.ScalarQueryParameter("domain_id", "INT64", domain_id),
        ]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        self.bq.client.query(query, job_config=job_config).result()

    # ══════════════════════════════════════════════════════════════════════════
    # Project CRUD
    # ══════════════════════════════════════════════════════════════════════════

    def list_projects(
        self,
        client_id: Optional[int] = None,
        project_type: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Get projects, optionally filtered by client and/or type.

        Args:
            client_id: Filter to projects for this client
            project_type: Filter to projects of this type (e.g., 'seo_pipeline', 'kga')
        """
        params = []
        conditions = []

        if client_id is not None:
            conditions.append("client_id = @client_id")
            params.append(bigquery.ScalarQueryParameter("client_id", "INT64", client_id))

        if project_type is not None:
            conditions.append("project_type = @project_type")
            params.append(bigquery.ScalarQueryParameter("project_type", "STRING", project_type))

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT project_id, client_id, project_type, project_name, notes, created_at
            FROM `{self._project_id}.Meta.projects`
            {where_clause}
            ORDER BY project_id
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params) if params else None
        return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

    def list_project_domains(self, project_id: int) -> pd.DataFrame:
        """
        Get domains in a project with their roles and priorities.

        Args:
            project_id: The project identifier

        Returns:
            DataFrame with domain_id, domain, domain_name, role, priority
        """
        query = f"""
            SELECT pd.domain_id, d.domain, d.domain_name, pd.role, pd.priority
            FROM `{self._project_id}.Meta.project_domains` pd
            JOIN `{self._project_id}.Meta.domains` d ON pd.domain_id = d.domain_id
            WHERE pd.project_id = @project_id
            ORDER BY pd.role, d.domain
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("project_id", "INT64", project_id)]
        )
        return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

    def add_project(
        self,
        project_id: int,
        client_id: int,
        project_type: str,
        project_name: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """
        Register a new project.

        Args:
            project_id: Unique project identifier
            client_id: Owning client
            project_type: Type of project ('seo_pipeline', 'kga', 'wqa', etc.)
            project_name: Optional display name
            notes: Optional notes
        """
        query = f"""
            INSERT INTO `{self._project_id}.Meta.projects`
            (project_id, client_id, project_type, project_name, notes)
            VALUES (@project_id, @client_id, @project_type, @project_name, @notes)
        """

        params = [
            bigquery.ScalarQueryParameter("project_id", "INT64", project_id),
            bigquery.ScalarQueryParameter("client_id", "INT64", client_id),
            bigquery.ScalarQueryParameter("project_type", "STRING", project_type),
            bigquery.ScalarQueryParameter("project_name", "STRING", project_name),
            bigquery.ScalarQueryParameter("notes", "STRING", notes),
        ]

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        self.bq.client.query(query, job_config=job_config).result()

    # ══════════════════════════════════════════════════════════════════════════
    # Dataset catalog (source of truth for dataset metadata)
    # ══════════════════════════════════════════════════════════════════════════

    def scan_datasets(self, prefixes: dict = None, full: bool = False) -> dict:
        """Scan BQ datasets and update dataset_catalog with type and hostname.

        Args:
            prefixes: Dict mapping type names to list of prefixes.
                      e.g. {"ga4": ["analytics_"], "gsc": ["jepto_gsc_"]}
                      Defaults to DEFAULT_DATASET_PREFIXES.
            full: If True, scan ALL datasets (slow). If False, only scan
                  datasets matching the prefix patterns (fast).

        Returns:
            Dict mapping type names to lists of dataset info dicts.
            Includes "other" key for unrecognized datasets (only if full=True).
        """
        if prefixes is None:
            prefixes = self.DEFAULT_DATASET_PREFIXES

        # Build flat list of (prefix, type) for matching
        prefix_map = []
        for ds_type, prefix_list in prefixes.items():
            for prefix in prefix_list:
                prefix_map.append((prefix.lower(), ds_type))

        # Get all datasets from BQ
        all_datasets = list(self.bq.client.list_datasets())

        # Classify each dataset
        categorized = {}  # type -> list of {dataset, dataset_type, hostname}
        unrecognized = []

        for ds in all_datasets:
            dataset_id = ds.dataset_id
            dataset_lower = dataset_id.lower()

            matched_type = None
            for prefix, ds_type in prefix_map:
                if dataset_lower.startswith(prefix):
                    matched_type = ds_type
                    break

            if matched_type:
                categorized.setdefault(matched_type, []).append(dataset_id)
            elif full:
                unrecognized.append(dataset_id)

        # Resolve hostnames for GA4 datasets (queries INFORMATION_SCHEMA per dataset)
        ga4_hostnames = {}
        if "ga4" in categorized:
            ga4_hostnames = self.bq.get_ga4_dataset_hostnames()

        # Build rows for MERGE
        discovered = []

        for ds_type, dataset_ids in categorized.items():
            for dataset_id in dataset_ids:
                hostname = None

                if ds_type == "ga4":
                    hostname = ga4_hostnames.get(dataset_id)
                    # Skip error strings from hostname resolution
                    if hostname and hostname.startswith("Error:"):
                        hostname = None

                elif ds_type == "gsc":
                    # Extract hostname from GSC dataset naming conventions
                    dataset_lower = dataset_id.lower()
                    if "_sc_domain_" in dataset_lower:
                        parts = dataset_id.split("_sc_domain_")
                        if len(parts) > 1:
                            hostname = parts[1].replace("_", ".").lower().replace("www.", "")
                    elif dataset_lower.startswith("jepto_gsc_"):
                        hostname = dataset_id[len("jepto_gsc_"):].replace("_", ".").lower()
                    elif dataset_lower.startswith("searchconsole_"):
                        hostname = dataset_id[len("searchconsole_"):].replace("_", ".").lower()

                discovered.append({
                    "dataset": dataset_id,
                    "dataset_type": ds_type,
                    "hostname": hostname,
                })

        if full:
            for dataset_id in unrecognized:
                discovered.append({
                    "dataset": dataset_id,
                    "dataset_type": "other",
                    "hostname": None,
                })

        # MERGE each discovered dataset into dataset_catalog
        for ds_info in discovered:
            merge_query = f"""
                MERGE `{self._project_id}.Meta.dataset_catalog` T
                USING (SELECT @dataset AS dataset) S
                ON T.dataset = S.dataset
                WHEN MATCHED THEN UPDATE SET
                    dataset_type = @dataset_type,
                    hostname = COALESCE(@hostname, T.hostname),
                    active = TRUE,
                    updated_at = CURRENT_TIMESTAMP()
                WHEN NOT MATCHED THEN INSERT
                    (dataset, dataset_type, hostname, active, updated_at)
                    VALUES (@dataset, @dataset_type, @hostname, TRUE, CURRENT_TIMESTAMP())
            """
            merge_params = [
                bigquery.ScalarQueryParameter("dataset", "STRING", ds_info["dataset"]),
                bigquery.ScalarQueryParameter("dataset_type", "STRING", ds_info["dataset_type"]),
                bigquery.ScalarQueryParameter("hostname", "STRING", ds_info["hostname"]),
            ]
            merge_config = bigquery.QueryJobConfig(query_parameters=merge_params)
            self.bq.client.query(merge_query, job_config=merge_config).result()

        # Remove datasets from catalog that no longer exist in BQ
        all_bq_dataset_names = {ds.dataset_id for ds in all_datasets}
        if full:
            # Full scan: delete any catalog entry not found in BQ
            delete_query = f"""
                DELETE FROM `{self._project_id}.Meta.dataset_catalog`
                WHERE dataset NOT IN UNNEST(@datasets)
            """
        else:
            # Quick scan: only delete entries matching our prefixes that are gone
            discovered_names = [d["dataset"] for d in discovered]
            # Get all catalog entries matching our prefixes
            prefix_conditions = " OR ".join(
                [f"LOWER(dataset) LIKE '{p}%'" for p, _ in prefix_map]
            )
            delete_query = f"""
                DELETE FROM `{self._project_id}.Meta.dataset_catalog`
                WHERE ({prefix_conditions})
                AND dataset NOT IN UNNEST(@datasets)
            """
            all_bq_dataset_names = {d for d in all_bq_dataset_names}

        delete_params = [
            bigquery.ArrayQueryParameter("datasets", "STRING", list(all_bq_dataset_names))
        ]
        delete_config = bigquery.QueryJobConfig(query_parameters=delete_params)
        self.bq.client.query(delete_query, job_config=delete_config).result()

        # Build return dict grouped by type
        result = {}
        for ds_info in discovered:
            ds_type = ds_info["dataset_type"]
            result.setdefault(ds_type, []).append(ds_info)

        return result

    def get_dataset_catalog(
        self,
        dataset_type: Optional[str] = None,
        unassigned_only: bool = False,
    ) -> pd.DataFrame:
        """Get datasets from the catalog, optionally filtered.

        Args:
            dataset_type: Filter by type (ga4, gsc, gmb, facebook, etc.)
            unassigned_only: If True, exclude datasets already in client_datasets
        """
        params = []
        conditions = []

        if dataset_type is not None:
            conditions.append("dc.dataset_type = @dataset_type")
            params.append(bigquery.ScalarQueryParameter("dataset_type", "STRING", dataset_type))

        if unassigned_only:
            conditions.append(f"""
                dc.dataset NOT IN (
                    SELECT dataset_id FROM `{self._project_id}.Meta.client_datasets`
                )
            """)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT dc.dataset, dc.dataset_type, dc.hostname,
                   dc.is_standardized, dc.owner, dc.active, dc.updated_at
            FROM `{self._project_id}.Meta.dataset_catalog` dc
            {where_clause}
            ORDER BY dc.dataset_type, dc.dataset
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params) if params else None
        return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

    # ══════════════════════════════════════════════════════════════════════════
    # Dataset client linking (Meta.client_datasets)
    # ══════════════════════════════════════════════════════════════════════════

    def get_client_datasets(
        self,
        client_id: Optional[int] = None,
        dataset_type: Optional[str] = None,
        active_only: bool = True,
    ) -> pd.DataFrame:
        """
        Get cached dataset mappings from Meta.client_datasets.

        Joins with dataset_catalog to pull dataset_type and hostname
        (source of truth for dataset metadata).

        Args:
            client_id: Filter to datasets for this client
            dataset_type: Filter by type ('ga4', 'gsc', 'gmb', 'facebook', etc.)
            active_only: Only return active datasets (default True)

        Returns:
            DataFrame with: client_id, domain_id, dataset_id, dataset_type,
                            hostname, is_active, notes, created_at
        """
        params = []
        conditions = []

        if client_id is not None:
            conditions.append("cd.client_id = @client_id")
            params.append(bigquery.ScalarQueryParameter("client_id", "INT64", client_id))

        if dataset_type is not None:
            conditions.append("dc.dataset_type = @dataset_type")
            params.append(bigquery.ScalarQueryParameter("dataset_type", "STRING", dataset_type))

        if active_only:
            conditions.append("cd.is_active = TRUE")

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT cd.client_id, cd.domain_id, cd.dataset_id,
                   dc.dataset_type, dc.hostname,
                   cd.is_active, cd.notes, cd.created_at
            FROM `{self._project_id}.Meta.client_datasets` cd
            LEFT JOIN `{self._project_id}.Meta.dataset_catalog` dc
                ON cd.dataset_id = dc.dataset
            {where_clause}
            ORDER BY cd.client_id, dc.dataset_type, cd.dataset_id
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params) if params else None
        return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

    def check_dataset_assignment(self, dataset_id: str) -> Optional[dict]:
        """Check if a dataset is already assigned to any client.

        Returns:
            dict with client_id and client_name if assigned, None if unassigned.
        """
        query = f"""
            SELECT cd.client_id, c.client_name
            FROM `{self._project_id}.Meta.client_datasets` cd
            JOIN `{self._project_id}.Meta.clients` c ON cd.client_id = c.client_id
            WHERE cd.dataset_id = @dataset_id
            LIMIT 1
        """
        params = [bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id)]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        df = self.bq.client.query(query, job_config=job_config).result().to_dataframe()
        if df.empty:
            return None
        row = df.iloc[0]
        return {"client_id": int(row["client_id"]), "client_name": row["client_name"]}

    def add_client_dataset(
        self,
        client_id: int,
        dataset_id: str,
        dataset_type: str,
        hostname: Optional[str] = None,
        domain_id: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Add a dataset mapping for a client.

        Ensures the dataset exists in dataset_catalog (with type/hostname),
        then inserts the client_datasets link row.

        Args:
            client_id: The client that owns this dataset
            dataset_id: The BQ dataset ID (e.g., 'analytics_123456789')
            dataset_type: Type of dataset ('ga4', 'gsc', 'gmb', 'facebook', etc.)
            hostname: The hostname associated with this dataset
            domain_id: Optional domain ID from Meta.domains
            notes: Optional notes

        Returns:
            dict with 'status' and optional 'warning' if dataset was already assigned
        """
        # Check if already assigned
        existing = self.check_dataset_assignment(dataset_id)
        warning = None
        if existing:
            warning = f"Dataset already assigned to client {existing['client_id']} ({existing['client_name']})"

        # 1. MERGE into dataset_catalog so metadata lives there
        merge_query = f"""
            MERGE `{self._project_id}.Meta.dataset_catalog` T
            USING (SELECT @dataset AS dataset) S
            ON T.dataset = S.dataset
            WHEN MATCHED THEN UPDATE SET
                dataset_type = @dataset_type,
                hostname = @hostname,
                active = TRUE,
                updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT
                (dataset, dataset_type, hostname, active, updated_at)
                VALUES (@dataset, @dataset_type, @hostname, TRUE, CURRENT_TIMESTAMP())
        """
        merge_params = [
            bigquery.ScalarQueryParameter("dataset", "STRING", dataset_id),
            bigquery.ScalarQueryParameter("dataset_type", "STRING", dataset_type),
            bigquery.ScalarQueryParameter("hostname", "STRING", hostname),
        ]
        merge_config = bigquery.QueryJobConfig(query_parameters=merge_params)
        self.bq.client.query(merge_query, job_config=merge_config).result()

        # 2. Insert the link row into client_datasets
        insert_query = f"""
            INSERT INTO `{self._project_id}.Meta.client_datasets`
            (client_id, domain_id, dataset_id, notes)
            VALUES (@client_id, @domain_id, @dataset_id, @notes)
        """
        insert_params = [
            bigquery.ScalarQueryParameter("client_id", "INT64", client_id),
            bigquery.ScalarQueryParameter("domain_id", "INT64", domain_id),
            bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id),
            bigquery.ScalarQueryParameter("notes", "STRING", notes),
        ]
        insert_config = bigquery.QueryJobConfig(query_parameters=insert_params)
        self.bq.client.query(insert_query, job_config=insert_config).result()

        return {"status": "added", "warning": warning}

    def update_client_dataset(self, client_id: int, dataset_id: str,
                              hostname: Optional[str] = None,
                              is_active: Optional[bool] = None,
                              notes: Optional[str] = None) -> None:
        """Update fields on a client dataset mapping.

        hostname updates go to dataset_catalog (source of truth).
        is_active and notes update client_datasets (the link row).
        """
        # Update hostname in dataset_catalog if provided
        if hostname is not None:
            catalog_query = f"""
                UPDATE `{self._project_id}.Meta.dataset_catalog`
                SET hostname = @hostname, updated_at = CURRENT_TIMESTAMP()
                WHERE dataset = @dataset_id
            """
            catalog_params = [
                bigquery.ScalarQueryParameter("hostname", "STRING", hostname),
                bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id),
            ]
            catalog_config = bigquery.QueryJobConfig(query_parameters=catalog_params)
            self.bq.client.query(catalog_query, job_config=catalog_config).result()

        # Update link-level fields in client_datasets if provided
        set_clauses = []
        params = [
            bigquery.ScalarQueryParameter("client_id", "INT64", client_id),
            bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id),
        ]
        if is_active is not None:
            set_clauses.append("is_active = @is_active")
            params.append(bigquery.ScalarQueryParameter("is_active", "BOOL", is_active))
        if notes is not None:
            set_clauses.append("notes = @notes")
            params.append(bigquery.ScalarQueryParameter("notes", "STRING", notes))
        if not set_clauses:
            return
        query = f"""
            UPDATE `{self._project_id}.Meta.client_datasets`
            SET {', '.join(set_clauses)}
            WHERE client_id = @client_id AND dataset_id = @dataset_id
        """
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        self.bq.client.query(query, job_config=job_config).result()

    def delete_client_dataset(self, client_id: int, dataset_id: str) -> None:
        """Remove a dataset mapping entirely."""
        query = f"""
            DELETE FROM `{self._project_id}.Meta.client_datasets`
            WHERE client_id = @client_id AND dataset_id = @dataset_id
        """
        params = [
            bigquery.ScalarQueryParameter("client_id", "INT64", client_id),
            bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id),
        ]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        self.bq.client.query(query, job_config=job_config).result()

    def deactivate_client_dataset(self, dataset_id: str) -> None:
        """
        Mark a dataset as inactive (soft delete).

        Args:
            dataset_id: The dataset to deactivate
        """
        query = f"""
            UPDATE `{self._project_id}.Meta.client_datasets`
            SET is_active = FALSE
            WHERE dataset_id = @dataset_id
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id)
            ]
        )
        self.bq.client.query(query, job_config=job_config).result()

    def scan_and_match_datasets(self) -> dict:
        """
        Read from dataset_catalog and propose client/domain matches (does NOT auto-save).

        Reads datasets that have been cataloged by scan_datasets() and proposes
        matches based on hostname similarity to known client domains.
        Use approve_scanned_datasets() to save approved matches.

        Returns:
            dict with keys:
                - 'proposed': list of proposed matches with suggested domain
                - 'unmatched': list of datasets that couldn't be matched
                - 'already_cached': list of dataset_ids already saved
        """
        results = {
            "proposed": [],
            "unmatched": [],
            "already_cached": [],
        }

        # Get existing cached datasets (already linked to a client)
        existing_df = self.get_client_datasets(active_only=False)
        existing_ids = set(existing_df["dataset_id"].tolist()) if not existing_df.empty else set()

        # Get all non-competitor domains mapped to clients WITH domain_id
        domain_query = f"""
            SELECT d.domain_id, d.domain, cd.client_id
            FROM `{self._project_id}.Meta.client_domains` cd
            JOIN `{self._project_id}.Meta.domains` d ON cd.domain_id = d.domain_id
            WHERE cd.is_competitor = FALSE AND d.is_active = TRUE
        """
        domains_df = self.bq.client.query(domain_query).result().to_dataframe()

        # Build hostname → (client_id, domain_id) mapping
        domain_lookup = {}  # normalized_hostname → {client_id, domain_id, domain}
        if not domains_df.empty:
            for _, row in domains_df.iterrows():
                domain = row["domain"]
                if domain:
                    normalized = domain.lower().replace("www.", "")
                    domain_lookup[normalized] = {
                        "client_id": int(row["client_id"]),
                        "domain_id": int(row["domain_id"]),
                        "domain": domain,
                    }

        def _match_hostname(hostname: str):
            """Try to match a hostname to a known domain.

            1. Exact match: "busbank.com" → "busbank.com"
            2. Fuzzy match: "busbank" → best domain starting with "busbank"
               (picks longest match to avoid "bus" matching "busbank.com")
            """
            if not hostname:
                return None
            normalized = hostname.lower().replace("www.", "")

            # Exact match
            if normalized in domain_lookup:
                return domain_lookup[normalized]

            # Fuzzy: hostname might be missing TLD (e.g. "busbank" from jepto_gsc_busbank)
            # Find all domains where the domain name part starts with the hostname
            candidates = []
            for known_domain, info in domain_lookup.items():
                # Check if known domain starts with the hostname
                # e.g. "busbank.com" starts with "busbank", "busbank.com.au" starts with "busbank"
                domain_name_part = known_domain.split(".")[0]
                if domain_name_part == normalized or known_domain.startswith(normalized + "."):
                    candidates.append((known_domain, info))

            if not candidates:
                return None

            # Pick the shortest domain (most specific match)
            # e.g. "busbank.com" over "busbank.com.au" if both exist
            candidates.sort(key=lambda x: len(x[0]))
            return candidates[0][1]

        def _match_dataset_name(dataset_id: str):
            """Fallback: check if any known domain appears in the dataset name."""
            dataset_lower = dataset_id.lower().replace("_", ".")
            # Sort by domain length descending so longer domains match first
            # (prevents "bushire.com.au" matching before "minibushire.com.au")
            for known_domain in sorted(domain_lookup.keys(), key=len, reverse=True):
                if known_domain in dataset_lower:
                    return domain_lookup[known_domain], known_domain
            return None, None

        # Read from dataset_catalog instead of live BQ scanning
        catalog_df = self.get_dataset_catalog()

        if catalog_df.empty:
            return results

        for _, row in catalog_df.iterrows():
            dataset_id = row["dataset"]
            ds_type = row.get("dataset_type") or ""
            hostname = row.get("hostname") or None

            if dataset_id in existing_ids:
                results["already_cached"].append(dataset_id)
                continue

            match = None

            # For GA4 datasets, match on hostname
            if ds_type == "ga4":
                clean_host = hostname.lower().replace("www.", "") if hostname else None
                match = _match_hostname(clean_host) if clean_host else None

                if match:
                    results["proposed"].append({
                        "dataset_id": dataset_id,
                        "dataset_type": "ga4",
                        "hostname": clean_host,
                        "client_id": match["client_id"],
                        "suggested_domain_id": match["domain_id"],
                        "suggested_domain": match["domain"],
                    })
                else:
                    results["unmatched"].append({
                        "dataset_id": dataset_id,
                        "dataset_type": "ga4",
                        "hostname": hostname,
                    })

            # For GSC datasets, try hostname then dataset name patterns
            elif ds_type == "gsc":
                if hostname:
                    match = _match_hostname(hostname.lower().replace("www.", ""))

                if not match:
                    # Try to extract hostname from dataset_id
                    if "_sc_domain_" in dataset_id:
                        parts = dataset_id.split("_sc_domain_")
                        if len(parts) > 1:
                            extracted = parts[1].replace("_", ".").lower().replace("www.", "")
                            match = _match_hostname(extracted)
                            if not hostname:
                                hostname = extracted

                    # Try prefix patterns: jepto_gsc_<domain_with_underscores>
                    if not match and dataset_id.lower().startswith("jepto_gsc_"):
                        raw = dataset_id[len("jepto_gsc_"):].replace("_", ".")
                        extracted = raw.lower()
                        match = _match_hostname(extracted)
                        if not hostname:
                            hostname = extracted

                    # Fallback: substring match (longest domain first)
                    if not match:
                        match, hostname = _match_dataset_name(dataset_id)

                if match:
                    results["proposed"].append({
                        "dataset_id": dataset_id,
                        "dataset_type": "gsc",
                        "hostname": match["domain"],
                        "client_id": match["client_id"],
                        "suggested_domain_id": match["domain_id"],
                        "suggested_domain": match["domain"],
                    })
                else:
                    results["unmatched"].append({
                        "dataset_id": dataset_id,
                        "dataset_type": "gsc",
                        "hostname": hostname,
                    })

            # For other types, try hostname match then dataset name match
            else:
                if hostname:
                    match = _match_hostname(hostname.lower().replace("www.", ""))
                if not match:
                    match, hostname = _match_dataset_name(dataset_id)

                if match:
                    results["proposed"].append({
                        "dataset_id": dataset_id,
                        "dataset_type": ds_type or "other",
                        "hostname": match["domain"],
                        "client_id": match["client_id"],
                        "suggested_domain_id": match["domain_id"],
                        "suggested_domain": match["domain"],
                    })
                else:
                    results["unmatched"].append({
                        "dataset_id": dataset_id,
                        "dataset_type": ds_type or "other",
                        "hostname": hostname,
                    })

        return results

    def approve_scanned_datasets(self, approvals: list) -> int:
        """Save approved dataset matches in a single bulk insert.

        Also MERGEs metadata (dataset_type, hostname) into dataset_catalog
        so that it becomes the source of truth.

        Args:
            approvals: List of dicts with keys:
                - dataset_id (str): BQ dataset name
                - dataset_type (str): "ga4", "gsc", etc.
                - hostname (str, optional)
                - client_id (int): which client to link to
                - domain_id (int, optional): which domain to link to (None = client-level)

        Returns:
            Number of datasets saved
        """
        if not approvals:
            return 0

        # 1. MERGE metadata into dataset_catalog for each dataset
        for item in approvals:
            merge_query = f"""
                MERGE `{self._project_id}.Meta.dataset_catalog` T
                USING (SELECT @dataset AS dataset) S
                ON T.dataset = S.dataset
                WHEN MATCHED THEN UPDATE SET
                    dataset_type = @dataset_type,
                    hostname = @hostname,
                    active = TRUE,
                    updated_at = CURRENT_TIMESTAMP()
                WHEN NOT MATCHED THEN INSERT
                    (dataset, dataset_type, hostname, active, updated_at)
                    VALUES (@dataset, @dataset_type, @hostname, TRUE, CURRENT_TIMESTAMP())
            """
            merge_params = [
                bigquery.ScalarQueryParameter("dataset", "STRING", item["dataset_id"]),
                bigquery.ScalarQueryParameter("dataset_type", "STRING", item["dataset_type"]),
                bigquery.ScalarQueryParameter("hostname", "STRING", item.get("hostname")),
            ]
            merge_config = bigquery.QueryJobConfig(query_parameters=merge_params)
            self.bq.client.query(merge_query, job_config=merge_config).result()

        # 2. Bulk insert link rows into client_datasets (pure linking table)
        link_rows = []
        for item in approvals:
            link_rows.append({
                "client_id": item["client_id"],
                "domain_id": item.get("domain_id"),
                "dataset_id": item["dataset_id"],
                "notes": None,
            })

        df = pd.DataFrame(link_rows)
        # Ensure int columns are correct dtype
        df["client_id"] = df["client_id"].astype("Int64")
        df["domain_id"] = df["domain_id"].astype("Int64")

        table_ref = f"{self._project_id}.Meta.client_datasets"
        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
        self.bq.client.load_table_from_dataframe(df, table_ref, job_config=job_config).result()

        return len(link_rows)

    def get_ga4_datasets_cached(self, client_id: Optional[int] = None) -> pd.DataFrame:
        """
        Get GA4 datasets from cache (fast).

        Args:
            client_id: Optional filter by client

        Returns:
            DataFrame with: client_id, dataset_id, hostname
        """
        df = self.get_client_datasets(client_id=client_id, dataset_type="ga4")
        if df.empty:
            return pd.DataFrame(columns=["client_id", "dataset_id", "hostname"])
        return df[["client_id", "dataset_id", "hostname"]]

    def get_gsc_datasets_cached(self, client_id: Optional[int] = None) -> pd.DataFrame:
        """
        Get GSC datasets from cache (fast).

        Args:
            client_id: Optional filter by client

        Returns:
            DataFrame with: client_id, dataset_id, hostname
        """
        df = self.get_client_datasets(client_id=client_id, dataset_type="gsc")
        if df.empty:
            return pd.DataFrame(columns=["client_id", "dataset_id", "hostname"])
        return df[["client_id", "dataset_id", "hostname"]]
