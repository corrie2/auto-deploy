from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from auto_deploy_agent.local import (
    check_local_prerequisites,
    clone_or_update_repo,
    inspect_project,
    resolve_command_cwd,
    run_command,
    start_background_command,
)
from auto_deploy_agent.models import AgentState
from auto_deploy_agent.planner import DEFAULT_LLM_MODEL, build_deploy_plan
from auto_deploy_agent.safety import validate_local_command


def _state(data: AgentState | dict[str, Any]) -> AgentState:
    if isinstance(data, AgentState):
        return data
    return AgentState.model_validate(data)


def check_local(state_data: AgentState | dict[str, Any]) -> dict[str, Any]:
    state = _state(state_data)
    try:
        check_local_prerequisites()
        return {}
    except Exception as exc:
        return {"errors": state.errors + [f"Local prerequisite check failed: {exc}"]}


def checkout_project(state_data: AgentState | dict[str, Any]) -> dict[str, Any]:
    state = _state(state_data)
    try:
        clone_or_update_repo(state.repo, Path(state.project_dir), state.branch)
        return {}
    except Exception as exc:
        return {"errors": state.errors + [f"Repository checkout failed: {exc}"]}


def inspect_local_project(state_data: AgentState | dict[str, Any]) -> dict[str, Any]:
    state = _state(state_data)
    try:
        project = inspect_project(Path(state.project_dir))
        return {"project": project.model_dump()}
    except Exception as exc:
        return {"errors": state.errors + [f"Project inspection failed: {exc}"]}


def plan_deployment(state_data: AgentState | dict[str, Any]) -> dict[str, Any]:
    state = _state(state_data)
    if state.project is None:
        return {"errors": state.errors + ["Project inspection is missing."]}

    model = os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL)
    try:
        plan = build_deploy_plan(
            model=model,
            project_dir=state.project_dir,
            project=state.project,
            healthcheck_url=state.healthcheck_url,
        )
        return {"plan": plan.model_dump()}
    except Exception as exc:
        return {"errors": state.errors + [f"Deployment planning failed: {exc}"]}


def validate_plan(state_data: AgentState | dict[str, Any]) -> dict[str, Any]:
    state = _state(state_data)
    if state.plan is None:
        return {"errors": state.errors + ["Deployment plan is missing."]}

    errors = list(state.errors)
    for command in state.plan.commands:
        try:
            validate_local_command(command.command)
        except ValueError as exc:
            errors.append(str(exc))
        try:
            resolve_command_cwd(Path(state.project_dir), command.cwd)
        except ValueError as exc:
            errors.append(str(exc))
    return {"errors": errors}


def execute_plan(state_data: AgentState | dict[str, Any]) -> dict[str, Any]:
    state = _state(state_data)
    if state.plan is None:
        return {"errors": state.errors + ["Deployment plan is missing."]}
    if state.dry_run or not state.force_execute or state.errors:
        return {}

    results = list(state.results)
    errors = list(state.errors)
    project_dir = Path(state.project_dir)
    command_env = state.plan.environment

    try:
        for command in state.plan.commands:
            cwd = resolve_command_cwd(project_dir, command.cwd)
            if command.phase == "start":
                result = start_background_command(command.command, cwd, command.name, env=command_env)
            else:
                result = run_command(command.command, cwd, command.name, command.timeout_seconds, env=command_env)
            results.append(result)
            if result.exit_status != 0:
                errors.append(
                    f"Command failed [{command.name}] exit={result.exit_status}: "
                    f"{result.stderr or result.stdout}"
                )
                break
    except Exception as exc:
        errors.append(f"Deployment execution failed: {exc}")

    return {
        "results": [result.model_dump() for result in results],
        "errors": errors,
    }


def has_errors(state_data: AgentState | dict[str, Any]) -> str:
    state = _state(state_data)
    return "stop" if state.errors else "continue"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("check_local", check_local)
    graph.add_node("checkout_project", checkout_project)
    graph.add_node("inspect_project", inspect_local_project)
    graph.add_node("plan_deployment", plan_deployment)
    graph.add_node("validate_plan", validate_plan)
    graph.add_node("execute_plan", execute_plan)

    graph.add_edge(START, "check_local")
    graph.add_conditional_edges("check_local", has_errors, {"continue": "checkout_project", "stop": END})
    graph.add_conditional_edges("checkout_project", has_errors, {"continue": "inspect_project", "stop": END})
    graph.add_conditional_edges("inspect_project", has_errors, {"continue": "plan_deployment", "stop": END})
    graph.add_conditional_edges("plan_deployment", has_errors, {"continue": "validate_plan", "stop": END})
    graph.add_conditional_edges("validate_plan", has_errors, {"continue": "execute_plan", "stop": END})
    graph.add_edge("execute_plan", END)
    return graph.compile()
