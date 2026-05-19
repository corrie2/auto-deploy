from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from auto_deploy_agent.graph import build_graph
from auto_deploy_agent.models import AgentState
from auto_deploy_agent.planner import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, DEEPSEEK_API_KEY_ENV

app = typer.Typer(help="Local LangGraph deployment agent.")
console = Console()


@app.command()
def main(
    repo: str | None = typer.Argument(None, help="GitHub repository URL. If omitted, the agent prompts for it."),
    project_dir: Path | None = typer.Option(None, help="Local directory where the project will be cloned."),
    branch: str | None = typer.Option(None, help="Optional Git branch. Defaults to the repository default branch."),
    healthcheck_url: str | None = typer.Option(None, help="Optional URL the plan should verify after startup."),
    dry_run: bool = typer.Option(False, help="Only clone, inspect, and print the plan without executing commands."),
    llm_model: str | None = typer.Option(None, help=f"LLM model name. Defaults to {DEFAULT_LLM_MODEL}."),
    llm_base_url: str | None = typer.Option(None, help=f"OpenAI-compatible base URL. Defaults to {DEFAULT_LLM_BASE_URL}."),
    llm_api_key: str | None = typer.Option(None, help=f"LLM API key. Overrides {DEEPSEEK_API_KEY_ENV}."),
) -> None:
    load_dotenv()
    repo = repo or console.input("GitHub URL: ").strip()
    if not repo:
        console.print("[red]GitHub URL is required.[/red]")
        raise typer.Exit(code=1)

    if llm_model:
        os.environ["OPENAI_MODEL"] = llm_model
    if llm_base_url:
        os.environ["OPENAI_BASE_URL"] = llm_base_url
    if llm_api_key:
        os.environ[DEEPSEEK_API_KEY_ENV] = llm_api_key

    target_dir = project_dir or Path.cwd() / ".deployments" / _repo_slug(repo)
    state = AgentState(
        repo=repo,
        branch=branch,
        project_dir=str(target_dir.resolve()),
        healthcheck_url=healthcheck_url,
        dry_run=dry_run,
        force_execute=not dry_run,
    )

    console.print(Panel.fit(f"Repo: {repo}\nDirectory: {state.project_dir}", title="Deploy Agent"))
    graph = build_graph()
    final = AgentState.model_validate(graph.invoke(state))
    _print_result(final)

    if final.errors:
        raise typer.Exit(code=1)


def _repo_slug(repo: str) -> str:
    parsed = urlparse(repo)
    path = parsed.path if parsed.scheme else repo
    if not parsed.scheme and ":" in repo and "/" in repo:
        path = repo.split(":", 1)[1]

    parts = [part for part in path.strip("/").split("/") if part]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][:-4]
    if len(parts) >= 2:
        raw = "-".join(parts[-2:])
    elif parts:
        raw = parts[-1]
    else:
        raw = "web-project"

    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
    return slug or "web-project"


def _print_result(state: AgentState) -> None:
    if state.plan:
        console.print(Panel.fit(state.plan.summary, title="Deployment Plan"))
        console.print(f"Detected stack: [cyan]{state.plan.detected_stack}[/cyan]")
        if state.plan.assumptions:
            console.print("Assumptions:")
            for item in state.plan.assumptions:
                console.print(f"  - {item}")

        table = Table(title="Commands")
        table.add_column("Phase")
        table.add_column("Name")
        table.add_column("CWD")
        table.add_column("Command")
        for command in state.plan.commands:
            table.add_row(command.phase, command.name, command.cwd or state.project_dir, command.command)
        console.print(table)

    if state.results:
        results = Table(title="Execution Results")
        results.add_column("Name")
        results.add_column("Exit")
        results.add_column("Output")
        for result in state.results:
            output = (result.stdout or result.stderr).strip()
            results.add_row(result.name, str(result.exit_status), output[-500:])
        console.print(results)

    if state.plan and state.plan.healthcheck_url:
        console.print(f"Healthcheck: [cyan]{state.plan.healthcheck_url}[/cyan]")

    if state.errors:
        console.print("[red]Errors:[/red]")
        for error in state.errors:
            console.print(f"  - {error}")


if __name__ == "__main__":
    app()
