from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from importlib import import_module

import click
from rich.console import Console
from rich.table import Table

from aicosts import config, reports
from aicosts.paths import db_path, projects_toml

PROVIDERS = ["anthropic", "openai", "gcp", "twilio", "github"]

console = Console()


@click.group()
def main() -> None:
    """aicosts — track API spend across providers."""


@main.command()
@click.option("--provider", "providers", multiple=True, type=click.Choice(PROVIDERS),
              help="Provider(s) to pull. Default: all.")
@click.option("--since", type=click.DateTime(formats=["%Y-%m-%d"]),
              help="Start date (inclusive). Default: 30 days ago.")
@click.option("--until", type=click.DateTime(formats=["%Y-%m-%d"]),
              help="End date (inclusive). Default: today.")
def pull(providers: tuple[str, ...], since: datetime | None, until: datetime | None) -> None:
    """Fetch usage/cost data from each provider into the local database."""
    selected = list(providers) if providers else PROVIDERS
    since_d = since.date() if since else date.today() - timedelta(days=30)
    until_d = until.date() if until else date.today()

    for name in selected:
        mod = import_module(f"aicosts.providers.{name}")
        try:
            result = mod.pull(since_d, until_d)
            console.print(
                f"[green]{name}[/green]: {result.rows_inserted} new, {result.rows_updated} updated"
                f" ({since_d}..{until_d})"
            )
        except SystemExit as e:
            console.print(f"[yellow]{name}[/yellow]: {e}")


@main.command()
@click.option("--period", default="month",
              type=click.Choice(["today", "yesterday", "week", "month", "30d"]))
@click.option("--by", default="provider",
              type=click.Choice(["provider", "project", "project-model", "key", "model"]))
def report(period: str, by: str) -> None:
    """Summarize spend over a window."""
    rows = reports.summarize(period, by)
    if not rows:
        console.print(f"No usage data for period={period}. Run [bold]aicosts pull[/bold] first.")
        return

    total = sum(r.cost_usd for r in rows)
    table = Table(title=f"Spend — {period} by {by}")
    table.add_column(by.capitalize())
    table.add_column("USD", justify="right")
    table.add_column("%", justify="right")
    for r in rows:
        pct = (r.cost_usd / total * 100) if total else 0
        label = f"{r.label}{'*' if r.estimated else ''}"
        table.add_row(label, f"${r.cost_usd:,.2f}", f"{pct:5.1f}%")
    table.add_section()
    table.add_row("TOTAL", f"${total:,.2f}", "")
    console.print(table)
    if any(r.estimated for r in rows):
        console.print("[dim]* = includes estimated values from token counts (replaces when finalized cost data lands)[/dim]")


@main.command()
def status() -> None:
    """One-line summary of today's spend (use in daily briefings)."""
    click.echo(reports.status_line())


@main.command()
def paths() -> None:
    """Show where aicosts stores data."""
    console.print(f"DB:           {db_path()}")
    console.print(f"projects.toml: {projects_toml()}")


@main.group(invoke_without_command=True)
@click.pass_context
def projects(ctx: click.Context) -> None:
    """Manage project label mappings."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(_projects_list)


@projects.command("list")
def _projects_list() -> None:
    """Show configured projects and unmapped IDs in the database."""
    from aicosts.config import load_projects, project_label_for
    from aicosts.storage import db

    projects_doc = load_projects()
    configured = projects_doc.get("project", [])

    if configured:
        table = Table(title=f"Configured projects ({projects_toml()})")
        table.add_column("Label")
        table.add_column("anthropic_workspace_ids")
        table.add_column("anthropic_project_ids")
        table.add_column("anthropic_api_key_ids")
        table.add_column("openai_project_ids")
        table.add_column("openai_api_key_ids")
        table.add_column("gcp_project_ids")
        table.add_column("twilio_subaccount_sids")
        table.add_column("github_orgs")
        for p in configured:
            table.add_row(
                p.get("label", ""),
                ", ".join(p.get("anthropic_workspace_ids", [])),
                ", ".join(p.get("anthropic_project_ids", [])),
                ", ".join(p.get("anthropic_api_key_ids", [])),
                ", ".join(p.get("openai_project_ids", [])),
                ", ".join(p.get("openai_api_key_ids", [])),
                ", ".join(p.get("gcp_project_ids", [])),
                ", ".join(p.get("twilio_subaccount_sids", [])),
                ", ".join(p.get("github_orgs", [])),
            )
        console.print(table)
    else:
        console.print(f"[yellow]No projects configured.[/yellow] Create: {projects_toml()}")

    sql = """
        SELECT DISTINCT provider, workspace_id, project_id, api_key_id
        FROM usage_events
        ORDER BY provider, workspace_id, project_id, api_key_id
    """
    with db.session() as conn:
        rows = conn.execute(sql).fetchall()

    if not rows:
        console.print("[dim]No usage data in database yet.[/dim]")
        return

    unmapped = [
        r for r in rows
        if project_label_for(
            projects_doc,
            provider=r["provider"],
            workspace_id=r["workspace_id"] or None,
            project_id=r["project_id"] or None,
            api_key_id=r["api_key_id"] or None,
        ) is None
    ]

    if unmapped:
        console.print()
        table = Table(title="Unmapped IDs in database (use 'aicosts projects add' to map)")
        table.add_column("Provider")
        table.add_column("workspace_id")
        table.add_column("project_id")
        table.add_column("api_key_id")
        for r in unmapped:
            table.add_row(
                r["provider"],
                r["workspace_id"] or "[dim](none)[/dim]",
                r["project_id"] or "[dim](none)[/dim]",
                r["api_key_id"] or "[dim](none)[/dim]",
            )
        console.print(table)
    else:
        console.print("[green]All usage IDs are mapped to project labels.[/green]")


@projects.command("add")
@click.argument("label")
@click.option("--anthropic-workspace", "anthropic_workspaces", multiple=True, metavar="ID",
              help="Anthropic workspace ID (repeat for multiple).")
@click.option("--anthropic-project", "anthropic_projects", multiple=True, metavar="ID",
              help="Anthropic project ID (repeat for multiple).")
@click.option("--anthropic-api-key", "anthropic_api_keys", multiple=True, metavar="ID",
              help="Anthropic API key ID (repeat for multiple).")
@click.option("--openai-project", "openai_projects", multiple=True, metavar="ID",
              help="OpenAI project ID (repeat for multiple).")
@click.option("--openai-api-key", "openai_api_keys", multiple=True, metavar="ID",
              help="OpenAI API key ID (repeat for multiple).")
@click.option("--gcp-project", "gcp_projects", multiple=True, metavar="ID",
              help="GCP project ID (repeat for multiple).")
@click.option("--twilio-subaccount", "twilio_subaccounts", multiple=True, metavar="SID",
              help="Twilio Account/Subaccount SID (repeat for multiple).")
@click.option("--github-org", "github_orgs", multiple=True, metavar="ORG",
              help="GitHub organization name (repeat for multiple).")
@click.option("--anthropic-catch-all", "anthropic_catch_all", is_flag=True, default=False,
              help="Match any Anthropic usage with no workspace/project/key ID (e.g. the Default workspace).")
def projects_add(
    label: str,
    anthropic_workspaces: tuple[str, ...],
    anthropic_projects: tuple[str, ...],
    anthropic_api_keys: tuple[str, ...],
    openai_projects: tuple[str, ...],
    openai_api_keys: tuple[str, ...],
    gcp_projects: tuple[str, ...],
    twilio_subaccounts: tuple[str, ...],
    github_orgs: tuple[str, ...],
    anthropic_catch_all: bool,
) -> None:
    """Add or update a project label mapping in projects.toml.

    Creates the file if it doesn't exist. If LABEL already exists, merges the
    new IDs into the existing entry without duplicating.

    \b
    Examples:
      aicosts projects add my-agent --anthropic-project proj_abc123
      aicosts projects add my-agent --anthropic-api-key apikey_abc123
      aicosts projects add my-agent --openai-project proj_def456
      aicosts projects add my-agent --gcp-project my-gcp-project
      aicosts projects add my-agent --twilio-subaccount ACxxxxx
      aicosts projects add my-agent --github-org my-company
      aicosts projects add default-workspace --anthropic-catch-all
    """
    import tomlkit

    if not any([anthropic_workspaces, anthropic_projects, anthropic_api_keys, openai_projects, openai_api_keys, gcp_projects, twilio_subaccounts, github_orgs, anthropic_catch_all]):
        raise click.UsageError(
            "Provide at least one ID option (--anthropic-workspace, --anthropic-project, "
            "--anthropic-api-key, --openai-project, --openai-api-key, --gcp-project, "
            "--twilio-subaccount, --github-org, or --anthropic-catch-all)."
        )

    p = projects_toml()
    doc = tomlkit.parse(p.read_text()) if p.exists() else tomlkit.document()

    entries: list = doc.get("project", [])  # type: ignore[assignment]
    existing = next((e for e in entries if e.get("label") == label), None)

    if anthropic_catch_all and existing is None:
        conflict = next((e for e in entries if e.get("anthropic_catch_all") and e.get("label") != label), None)
        if conflict:
            raise click.UsageError(
                f"A catch-all already exists for label '{conflict.get('label')}'. "
                "Only one catch-all is allowed."
            )

    if existing is None:
        entry = tomlkit.table()
        entry.add("label", label)
        if anthropic_workspaces:
            entry.add("anthropic_workspace_ids", list(anthropic_workspaces))
        if anthropic_projects:
            entry.add("anthropic_project_ids", list(anthropic_projects))
        if anthropic_api_keys:
            entry.add("anthropic_api_key_ids", list(anthropic_api_keys))
        if openai_projects:
            entry.add("openai_project_ids", list(openai_projects))
        if openai_api_keys:
            entry.add("openai_api_key_ids", list(openai_api_keys))
        if gcp_projects:
            entry.add("gcp_project_ids", list(gcp_projects))
        if twilio_subaccounts:
            entry.add("twilio_subaccount_sids", list(twilio_subaccounts))
        if github_orgs:
            entry.add("github_orgs", list(github_orgs))
        if anthropic_catch_all:
            entry.add("anthropic_catch_all", True)
        if "project" not in doc:
            doc.add("project", tomlkit.aot())
        doc["project"].append(entry)
    else:
        def _merge(key: str, new_ids: tuple[str, ...]) -> None:
            if not new_ids:
                return
            current: list = existing.setdefault(key, [])
            for id_ in new_ids:
                if id_ not in current:
                    current.append(id_)

        _merge("anthropic_workspace_ids", anthropic_workspaces)
        _merge("anthropic_project_ids", anthropic_projects)
        _merge("anthropic_api_key_ids", anthropic_api_keys)
        _merge("openai_project_ids", openai_projects)
        _merge("openai_api_key_ids", openai_api_keys)
        _merge("gcp_project_ids", gcp_projects)
        _merge("twilio_subaccount_sids", twilio_subaccounts)
        _merge("github_orgs", github_orgs)
        if anthropic_catch_all:
            existing["anthropic_catch_all"] = True

    p.write_text(tomlkit.dumps(doc))
    console.print(f"[green]✓[/green] saved [bold]{label}[/bold] → {p}")


@main.group()
def keys() -> None:
    """Manage credentials in macOS Keychain."""


@keys.command("set")
@click.argument("name")
@click.option("--value", help="Value to store. If omitted, prompts.")
@click.option("--file", "file_path", type=click.Path(exists=True), help="Read value from file (useful for JSON keys).")
def keys_set(name: str, value: str | None, file_path: str | None) -> None:
    if file_path:
        value = open(file_path).read().strip()
    elif value is None:
        value = click.prompt(f"Value for {name}", hide_input=True)
    config.set_secret(name, value)
    console.print(f"[green]✓[/green] stored {name} in keychain (service={config.SERVICE})")


@keys.command("get")
@click.argument("name")
def keys_get(name: str) -> None:
    v = config.get_secret(name)
    if v is None:
        console.print(f"[yellow]not set:[/yellow] {name}")
        sys.exit(1)
    console.print(v)


@keys.command("delete")
@click.argument("name")
def keys_delete(name: str) -> None:
    config.delete_secret(name)
    console.print(f"[green]✓[/green] deleted {name}")


if __name__ == "__main__":
    main()
