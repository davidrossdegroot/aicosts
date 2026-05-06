from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from importlib import import_module

import click
from rich.console import Console
from rich.table import Table

from aicosts import config, reports
from aicosts.paths import db_path, projects_toml

PROVIDERS = ["anthropic", "openai"]

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
              type=click.Choice(["provider", "project", "key", "model"]))
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


@main.group()
def keys() -> None:
    """Manage credentials in macOS Keychain."""


@keys.command("set")
@click.argument("name")
@click.option("--value", help="Value to store. If omitted, prompts.")
def keys_set(name: str, value: str | None) -> None:
    if value is None:
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
