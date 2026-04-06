"""Skyward CLI — command-line interface for shared infrastructure."""
import click

BigQueryClient = None
DataHub = None
load_config = None


def _get_hub():
    """Bootstrap config and return a DataHub instance."""
    global load_config, BigQueryClient, DataHub
    if load_config is None:
        from skyward.config import load_config as _lc
        from skyward.data.bigquery import BigQueryClient as _bq
        from skyward.data.hub import DataHub as _dh
        load_config = _lc
        BigQueryClient = _bq
        DataHub = _dh
    cfg = load_config()
    bq = BigQueryClient(project_id=cfg.datahub_project_id, credentials_info=cfg.datahub_credentials or None)
    return DataHub(bq)


class SkywardCLI(click.Group):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except click.exceptions.Exit:
            raise
        except RuntimeError as e:
            raise click.ClickException(str(e))


@click.group(cls=SkywardCLI)
def cli():
    """Skyward shared infrastructure CLI."""
    pass


@cli.group()
def meta():
    """Manage Meta tables (clients, domains, projects, datasets)."""
    pass


@meta.command("list-clients")
@click.option("--search", default=None, help="Filter clients by name.")
@click.option("--counts", is_flag=True, help="Include domain/project counts.")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"]), help="Output format.")
def meta_list_clients(search, counts, fmt):
    """List all clients."""
    hub = _get_hub()
    df = hub.list_clients(search=search, include_counts=counts)
    if fmt == "json":
        click.echo(df.to_json(orient="records", indent=2))
    else:
        click.echo(df.to_string(index=False))


@meta.command("add-client")
@click.option("--name", required=True, help="Client name.")
@click.option("--abbreviation", default=None, help="Client abbreviation.")
def meta_add_client(name, abbreviation):
    """Add a new client."""
    hub = _get_hub()
    client_id = hub.add_client(client_name=name, abbreviation=abbreviation)
    click.echo(f"Created client {client_id}")


@meta.command("deactivate-client")
@click.option("--id", "client_id", required=True, type=int, help="Client ID.")
@click.option("--cascade", is_flag=True, help="Also deactivate related datasets.")
def meta_deactivate_client(client_id, cascade):
    """Deactivate a client."""
    hub = _get_hub()
    hub.deactivate_client(client_id=client_id, cascade=cascade)
    click.echo(f"Deactivated client {client_id}")


@meta.command("list-domains")
@click.option("--client-id", required=True, type=int, help="Client ID.")
@click.option("--competitors-only", is_flag=True, default=False, help="Only show competitor domains.")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"]), help="Output format.")
def meta_list_domains(client_id, competitors_only, fmt):
    """List domains for a client."""
    hub = _get_hub()
    is_competitor = True if competitors_only else None
    df = hub.get_client_domains(client_id=client_id, is_competitor=is_competitor)
    if fmt == "json":
        click.echo(df.to_json(orient="records", indent=2))
    else:
        click.echo(df.to_string(index=False))


@meta.command("add-domains")
@click.option("--client-id", required=True, type=int, help="Client ID.")
@click.option("--domains", required=True, help="Comma-separated list of domains.")
@click.option("--competitor", is_flag=True, default=False, help="Mark as competitor domains.")
@click.option("--priority", default="NORMAL", help="Priority level.")
def meta_add_domains(client_id, domains, competitor, priority):
    """Add domains to a client."""
    hub = _get_hub()
    domain_list = [d.strip() for d in domains.split(",")]
    result = hub.add_domains(domains=domain_list, client_id=client_id, is_competitor=competitor, priority=priority)
    click.echo(f"Added {len(result)} domain(s)")


@meta.command("list-projects")
@click.option("--client-id", default=None, type=int, help="Filter by client ID.")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"]), help="Output format.")
def meta_list_projects(client_id, fmt):
    """List projects."""
    hub = _get_hub()
    df = hub.list_projects(client_id=client_id)
    if fmt == "json":
        click.echo(df.to_json(orient="records", indent=2))
    else:
        click.echo(df.to_string(index=False))


# ══════════════════════════════════════════════════════════════════════════
# LLM group
# ══════════════════════════════════════════════════════════════════════════

@cli.group()
def llm():
    """LLM cost calculation utilities."""
    pass


@llm.command("cost")
@click.option("--provider", required=True, help="LLM provider (openai, gemini, perplexity).")
@click.option("--model", required=True, help="Model name.")
@click.option("--input", "input_tokens", required=True, type=int, help="Input token count.")
@click.option("--output", "output_tokens", required=True, type=int, help="Output token count.")
def llm_cost(provider, model, input_tokens, output_tokens):
    """Calculate cost for a specific token usage."""
    from skyward.llm import calculate_cost, format_cost
    cost = calculate_cost(input_tokens, output_tokens, model, provider)
    click.echo(format_cost(cost))


@llm.command("estimate")
@click.option("--provider", default="openai", help="LLM provider.")
@click.option("--model", default="gpt-4o", help="Model name.")
@click.option("--items", "num_items", required=True, type=int, help="Number of items to process.")
@click.option("--input-per", "avg_input", default=3000, type=int, help="Avg input tokens per item.")
@click.option("--output-per", "avg_output", default=1000, type=int, help="Avg output tokens per item.")
def llm_estimate(provider, model, num_items, avg_input, avg_output):
    """Estimate batch cost."""
    from skyward.llm import estimate_batch_cost, format_cost
    result = estimate_batch_cost(
        num_items=num_items,
        avg_input_tokens=avg_input,
        avg_output_tokens=avg_output,
        model=model,
        provider=provider,
    )
    click.echo(f"Total: {format_cost(result['total_cost'])}  |  Per item: {format_cost(result['cost_per_item'])}")


# ══════════════════════════════════════════════════════════════════════════
# BQ group
# ══════════════════════════════════════════════════════════════════════════

@cli.group()
def bq():
    """BigQuery operations and upload log queries."""
    pass


@bq.command("search-uploads")
@click.option("--client-id", default=None, help="Filter by client ID.")
@click.option("--job-id", default=None, help="Filter by job ID.")
@click.option("--table", default=None, help="Filter by table name.")
@click.option("--dataset", default=None, help="Filter by dataset name.")
@click.option("--limit", default=100, type=int, help="Max rows to return.")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"]), help="Output format.")
def bq_search_uploads(client_id, job_id, table, dataset, limit, fmt):
    """Search upload event logs."""
    hub = _get_hub()
    df = hub.search_uploads(client_id=client_id, job_id=job_id, table=table, dataset=dataset, limit=limit)
    if fmt == "json":
        click.echo(df.to_json(orient="records", indent=2))
    else:
        click.echo(df.to_string(index=False))


@meta.command("list-datasets")
@click.option("--client-id", default=None, type=int, help="Filter by client ID.")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"]), help="Output format.")
def meta_list_datasets(client_id, fmt):
    """List dataset assignments."""
    hub = _get_hub()
    df = hub.get_client_datasets(client_id=client_id)
    if fmt == "json":
        click.echo(df.to_json(orient="records", indent=2))
    else:
        click.echo(df.to_string(index=False))
