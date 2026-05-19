from __future__ import annotations

import os
import re
import shlex


ALLOWED_EXECUTABLES = {
    "bun",
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
    executable = _executable_name(tokens[0])
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
        return shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise ValueError(f"Blocked unparsable local command: {command}") from exc


def _executable_name(token: str) -> str:
    name = token.strip("\"'")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    lower = name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if lower.endswith(suffix):
            lower = lower[: -len(suffix)]
    return lower


def _validate_allowed_tool(executable: str, tokens: list[str], command: str) -> None:
    lowered = [token.strip("\"'").lower() for token in tokens]
    if executable in {"npm", "pnpm", "yarn", "bun"} and any(token in {"-g", "--global"} for token in lowered):
        raise ValueError(f"Blocked global package installation command: {command}")
    if executable in {"pip", "pip3", "python", "python3", "py"} and "install" in lowered:
        if not any(token in {"-r", "--requirement", "-e"} for token in lowered):
            raise ValueError(f"Blocked unconstrained Python package installation command: {command}")
