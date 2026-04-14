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
        except (RuntimeError, ValueError) as e:
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


@meta.command("get-domain")
@click.option("--domain", required=True, help="Domain to look up (exact match).")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "table"]), help="Output format.")
def meta_get_domain(domain, fmt):
    """Look up a single domain by exact match."""
    hub = _get_hub()
    result = hub.get_domain(domain)
    if result is None:
        click.echo(f"Domain '{domain}' not found", err=True)
        raise click.exceptions.Exit(1)
    if fmt == "json":
        import json as _json
        click.echo(_json.dumps(result, indent=2, default=str))
    else:
        for key, value in result.items():
            click.echo(f"{key}: {value}")


@meta.command("search-domains")
@click.option("--query", required=True, help="Search query (partial match against domains).")
@click.option("--limit", default=10, type=int, help="Max results to return.")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "table"]), help="Output format.")
def meta_search_domains(query, limit, fmt):
    """Fuzzy search for domains by partial match."""
    hub = _get_hub()
    df = hub.search_domains(query, limit=limit)
    if fmt == "json":
        click.echo(df.to_json(orient="records", indent=2))
    else:
        click.echo(df.to_string(index=False))


_PRIORITY_CHOICES = click.Choice(
    ["VERY LOW", "LOW", "NORMAL", "HIGH", "VERY HIGH"],
    case_sensitive=False,
)


@meta.command("add-domain")
@click.option("--domain", required=True, help="Domain to add (single).")
@click.option("--client-id", default=None, type=int, help="Optional client ID to link the domain to.")
@click.option("--competitor", is_flag=True, default=False, help="Mark as competitor (only with --client-id).")
@click.option("--priority", default="NORMAL", type=_PRIORITY_CHOICES, help="Priority (only with --client-id).")
def meta_add_domain(domain, client_id, competitor, priority):
    """Add a single domain, optionally linking to a client."""
    hub = _get_hub()
    domain_id = hub.add_domain(
        domain, client_id=client_id, is_competitor=competitor, priority=priority.upper(),
    )
    click.echo(f"Domain ID: {domain_id}")


@meta.command("add-domains")
@click.option("--client-id", default=None, type=int, help="Optional client ID to link the domains to.")
@click.option("--domains", required=True, help="Comma-separated list of domains.")
@click.option("--competitor", is_flag=True, default=False, help="Mark as competitor domains (only with --client-id).")
@click.option("--priority", default="NORMAL", type=_PRIORITY_CHOICES, help="Priority (only with --client-id).")
def meta_add_domains(client_id, domains, competitor, priority):
    """Bulk-add domains, optionally linking to a client."""
    hub = _get_hub()
    domain_list = [d.strip() for d in domains.split(",")]
    result = hub.add_domains(domains=domain_list, client_id=client_id, is_competitor=competitor, priority=priority.upper())
    click.echo(f"Added {len(result)} domain(s)")


@meta.command("list-projects")
@click.option("--client-id", default=None, type=int, help="Filter by client ID.")
@click.option("--status", default=None, help="Filter by status (active, complete, deactivated, cancelled).")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"]), help="Output format.")
def meta_list_projects(client_id, status, fmt):
    """List projects."""
    hub = _get_hub()
    df = hub.list_projects(client_id=client_id, status=status)
    if fmt == "json":
        click.echo(df.to_json(orient="records", indent=2))
    else:
        click.echo(df.to_string(index=False))


@meta.command("add-project")
@click.option("--client-id", required=True, type=int, help="Client ID.")
@click.option("--type", "project_type", required=True, help="Project type (seo_pipeline, kga, wqa, etc.).")
@click.option("--name", "project_name", default=None, help="Project display name.")
def meta_add_project(client_id, project_type, project_name):
    """Add a new project."""
    hub = _get_hub()
    project_id = hub.add_project(client_id=client_id, project_type=project_type, project_name=project_name)
    click.echo(f"Created project {project_id}")


@meta.command("deactivate-project")
@click.option("--id", "project_id", required=True, type=int, help="Project ID.")
def meta_deactivate_project(project_id):
    """Deactivate a project."""
    hub = _get_hub()
    hub.deactivate_project(project_id=project_id)
    click.echo(f"Deactivated project {project_id}")


@meta.command("reactivate-project")
@click.option("--id", "project_id", required=True, type=int, help="Project ID.")
def meta_reactivate_project(project_id):
    """Reactivate a project (set status back to active)."""
    hub = _get_hub()
    hub.update_project(project_id=project_id, status="active")
    click.echo(f"Reactivated project {project_id}")


@meta.command("complete-project")
@click.option("--id", "project_id", required=True, type=int, help="Project ID.")
def meta_complete_project(project_id):
    """Mark a project as complete."""
    hub = _get_hub()
    hub.complete_project(project_id=project_id)
    click.echo(f"Completed project {project_id}")


@meta.command("add-project-domains")
@click.option("--project-id", required=True, type=int, help="Project ID.")
@click.option("--domain-ids", required=True, help="Comma-separated domain IDs.")
@click.option("--role", default="client", help="Role (client or competitor).")
@click.option("--priority", default="NORMAL", help="Priority level.")
def meta_add_project_domains(project_id, domain_ids, role, priority):
    """Add domains to a project."""
    hub = _get_hub()
    ids = [int(d.strip()) for d in domain_ids.split(",")]
    count = hub.add_project_domains(project_id=project_id, domain_ids=ids, role=role, priority=priority)
    click.echo(f"Added {count} domain(s) to project {project_id}")


@meta.command("remove-project-domains")
@click.option("--project-id", required=True, type=int, help="Project ID.")
@click.option("--domain-ids", required=True, help="Comma-separated domain IDs.")
def meta_remove_project_domains(project_id, domain_ids):
    """Remove domains from a project."""
    hub = _get_hub()
    ids = [int(d.strip()) for d in domain_ids.split(",")]
    hub.remove_project_domains(project_id=project_id, domain_ids=ids)
    click.echo(f"Removed {len(ids)} domain(s) from project {project_id}")


# ══════════════════════════════════════════════════════════════════════════
# LLM group
# ══════════════════════════════════════════════════════════════════════════

@cli.group()
def llm():
    """LLM cost calculation utilities."""
    pass


@llm.command("call")
@click.option("--provider", required=True, type=click.Choice(["openai", "gemini", "perplexity", "anthropic", "grok"]), help="LLM provider.")
@click.option("--model", required=True, help="Model name.")
@click.option("--message", required=True, help="User message.")
@click.option("--system", default=None, help="System prompt.")
@click.option("--temperature", default=None, type=float, help="Sampling temperature.")
@click.option("--max-tokens", default=None, type=int, help="Max output tokens.")
@click.option("--api-key", default=None, help="API key (overrides env var).")
def llm_call(provider, model, message, system, temperature, max_tokens, api_key):
    """Make a single LLM call and print the response."""
    from skyward.llm import get_provider, calculate_cost, format_cost

    p = get_provider(provider, api_key=api_key)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": message})

    kwargs = {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    result, in_tok, out_tok = p.call(messages, model, **kwargs)

    click.echo(result)
    click.echo(f"\n--- {in_tok} in / {out_tok} out | {format_cost(calculate_cost(in_tok, out_tok, model, provider))} ---")


@llm.command("chat")
@click.option("--provider", required=True, type=click.Choice(["openai", "gemini", "perplexity", "anthropic", "grok"]), help="LLM provider.")
@click.option("--model", required=True, help="Model name.")
@click.option("--system", default=None, help="System prompt.")
@click.option("--api-key", default=None, help="API key (overrides env var).")
@click.option("--summarize-tokens", default=50000, type=int, help="Summarize after N tokens (0 to disable).")
def llm_chat(provider, model, system, api_key, summarize_tokens):
    """Start an interactive chat session. Type 'quit' to exit."""
    from skyward.llm import get_provider
    from skyward.llm.session import LLMSession

    p = get_provider(provider, api_key=api_key)
    session = LLMSession(
        p,
        system_prompt=system,
        summarize_after_tokens=summarize_tokens if summarize_tokens > 0 else None,
    )

    click.echo(f"Chat with {provider}/{model}. Type 'quit' to exit.\n")

    while True:
        try:
            user_input = click.prompt("You", prompt_suffix="> ")
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.strip().lower() in ("quit", "exit"):
            break

        result = session.send(user_input, model=model)
        click.echo(f"\n{result}")
        click.echo(
            f"  [{session.total_input_tokens} in / {session.total_output_tokens} out | "
            f"{len(session.messages)} messages]\n"
        )


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
