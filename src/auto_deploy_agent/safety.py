from __future__ import annotations

import os
import re

from auto_deploy_agent.command_analysis import (
    executable_name,
    parse_conda_run,
    split_command,
    validate_package_source_compatibility,
)


ALLOWED_EXECUTABLES = {
    "bun",
    "conda",
    "docker",
    "docker-compose",
    "flask",
    "npm",
    "npx",
    "node",
    "pip",
    "pip3",
    "pipenv",
    "pnpm",
    "poetry",
    "py",
    "python",
    "python3",
    "streamlit",
    "uv",
    "uvicorn",
    "vllm",
    "yarn",
}

BLOCKED_EXECUTABLES = {
    "bash",
    "cmd",
    "curl",
    "del",
    "erase",
    "format",
    "mkfs",
    "powershell",
    "pwsh",
    "rd",
    "rm",
    "rmdir",
    "sh",
    "sudo",
    "wget",
}

SHELL_CONTROL_PATTERN = re.compile(r"(\|\||&&|[|;&<>]|\$\(|`|\r|\n)")


DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\s+/",
    r"\bsudo\s+rm\s+-rf\b",
    r"\brmdir\s+/s\s+/q\s+[a-z]:\\",
    r"\bdel\s+/[a-z]*s[a-z]*\s+[a-z]:\\",
    r"\bformat\s+[a-z]:",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{\s*:\|:",
    r">\s*/dev/sd[a-z]",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\binit\s+0\b",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bchown\s+-R\b.+\s+/",
]


def validate_local_command(command: str) -> None:
    normalized = command.strip().lower()
    if not normalized:
        raise ValueError("Blocked empty local command.")

    if SHELL_CONTROL_PATTERN.search(command):
        raise ValueError(f"Blocked shell control operator in local command: {command}")

    tokens = _split_command(command)
    executable = executable_name(tokens[0])
    if executable in BLOCKED_EXECUTABLES:
        raise ValueError(f"Blocked local command executable: {command}")
    if executable not in ALLOWED_EXECUTABLES:
        raise ValueError(f"Blocked unsupported local command executable: {command}")
    _validate_allowed_tool(executable, tokens, command)

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, normalized):
            raise ValueError(f"Blocked dangerous local command: {command}")


def _split_command(command: str) -> list[str]:
    try:
        return split_command(command)
    except ValueError as exc:
        raise ValueError(f"Blocked unparsable local command: {command}") from exc


def _validate_allowed_tool(executable: str, tokens: list[str], command: str) -> None:
    lowered = [token.strip("\"'").lower() for token in tokens]
    if executable in {"npm", "pnpm", "yarn", "bun"} and any(token in {"-g", "--global"} for token in lowered):
        raise ValueError(f"Blocked global package installation command: {command}")
    if executable in {"pip", "pip3", "python", "python3", "py"} and "install" in lowered:
        _validate_pip_install_structure(lowered, command)
        validate_package_source_compatibility(command)
    if executable in {"python", "python3", "py"} and len(lowered) >= 3 and lowered[1:3] == ["-m", "venv"]:
        return
    if executable == "conda":
        _validate_conda_command(tokens, command)
    if executable == "uv":
        _validate_uv_command(tokens, command)
    if executable == "poetry":
        _validate_poetry_command(tokens, command)
    if executable == "vllm" and (len(lowered) < 2 or lowered[1] != "serve"):
        raise ValueError(f"Blocked unsupported vLLM command: {command}")


def _validate_pip_install_structure(lowered: list[str], command: str) -> None:
    install_index = lowered.index("install")
    if not lowered[install_index + 1 :]:
        raise ValueError(f"Blocked unconstrained Python package installation command: {command}")


def _validate_conda_command(tokens: list[str], command: str) -> None:
    lowered = [token.strip("\"'").lower() for token in tokens]
    if len(lowered) < 2:
        raise ValueError(f"Blocked unsupported conda command: {command}")

    subcommand = lowered[1]
    if subcommand == "create":
        _validate_conda_create(lowered, command)
        return
    if subcommand == "run":
        parsed = parse_conda_run(tokens)
        if parsed is None:
            raise ValueError(f"Blocked unsupported conda run command: {command}")
        _, inner_tokens = parsed
        inner_executable = executable_name(inner_tokens[0])
        if inner_executable in BLOCKED_EXECUTABLES:
            raise ValueError(f"Blocked local command executable: {command}")
        if inner_executable not in ALLOWED_EXECUTABLES:
            raise ValueError(f"Blocked unsupported local command executable: {command}")
        if inner_executable == "conda":
            raise ValueError(f"Blocked nested conda command: {command}")
        _validate_allowed_tool(inner_executable, inner_tokens, command)
        return

    raise ValueError(f"Blocked unsupported conda command: {command}")


def _validate_conda_create(lowered: list[str], command: str) -> None:
    allowed_flags = {"-n", "--name", "-y", "--yes"}
    index = 2
    found_name = False
    found_python = False
    while index < len(lowered):
        token = lowered[index]
        if token in {"-n", "--name"}:
            if index + 1 >= len(lowered) or lowered[index + 1].startswith("-"):
                raise ValueError(f"Blocked unsupported conda create command: {command}")
            found_name = True
            index += 2
            continue
        if token in {"-y", "--yes"}:
            index += 1
            continue
        if token.startswith("python="):
            found_python = True
            index += 1
            continue
        if token.startswith("-") and token not in allowed_flags:
            raise ValueError(f"Blocked unsupported conda create command: {command}")
        raise ValueError(f"Blocked unsupported conda create package: {command}")

    if not found_name or not found_python:
        raise ValueError(f"Blocked unsupported conda create command: {command}")


def _validate_uv_command(tokens: list[str], command: str) -> None:
    lowered = [token.strip("\"'").lower() for token in tokens]
    if len(lowered) < 2:
        raise ValueError(f"Blocked unsupported uv command: {command}")
    subcommand = lowered[1]
    if subcommand in {"sync", "add"}:
        return
    if subcommand == "pip":
        if len(lowered) < 3 or lowered[2] != "install":
            raise ValueError(f"Blocked unsupported uv pip command: {command}")
        _validate_pip_install_structure(lowered[1:], command)
        validate_package_source_compatibility(command)
        return
    if subcommand == "run":
        if len(tokens) < 3:
            raise ValueError(f"Blocked unsupported uv run command: {command}")
        inner_executable = executable_name(tokens[2])
        if inner_executable in BLOCKED_EXECUTABLES or inner_executable not in ALLOWED_EXECUTABLES:
            raise ValueError(f"Blocked unsupported uv run command: {command}")
        _validate_allowed_tool(inner_executable, tokens[2:], command)
        return
    raise ValueError(f"Blocked unsupported uv command: {command}")


def _validate_poetry_command(tokens: list[str], command: str) -> None:
    lowered = [token.strip("\"'").lower() for token in tokens]
    if len(lowered) < 2:
        raise ValueError(f"Blocked unsupported poetry command: {command}")
    subcommand = lowered[1]
    if subcommand in {"install", "add"}:
        return
    if subcommand == "run":
        if len(tokens) < 3:
            raise ValueError(f"Blocked unsupported poetry run command: {command}")
        inner_executable = executable_name(tokens[2])
        if inner_executable in BLOCKED_EXECUTABLES or inner_executable not in ALLOWED_EXECUTABLES:
            raise ValueError(f"Blocked unsupported poetry run command: {command}")
        _validate_allowed_tool(inner_executable, tokens[2:], command)
        return
    raise ValueError(f"Blocked unsupported poetry command: {command}")
