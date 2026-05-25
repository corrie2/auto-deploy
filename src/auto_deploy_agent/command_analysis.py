from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PIP_OPTIONS_WITH_VALUES = {
    "--config-settings",
    "--constraint",
    "-c",
    "--find-links",
    "-f",
    "--global-option",
    "--index-url",
    "-i",
    "--platform",
    "--python-version",
    "--requirement",
    "-r",
    "--root",
    "--target",
    "-t",
    "--extra-index-url",
}

PIP_OPTIONS_WITH_INLINE_VALUES = tuple(f"{option}=" for option in PIP_OPTIONS_WITH_VALUES if option.startswith("--"))

PYTORCH_INDEX_HOST_MARKERS = (
    "download.pytorch.org",
    "download-r2.pytorch.org",
)

PYTORCH_PACKAGES = {
    "torch",
    "torchaudio",
    "torchvision",
}


@dataclass(frozen=True)
class PackageInstallRequest:
    command: str
    manager: str
    packages: tuple[str, ...] = ()
    requirement_files: tuple[str, ...] = ()
    editable_installs: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    project_installs: tuple[str, ...] = ()


def split_command(command: str) -> list[str]:
    return shlex.split(command, posix=os.name != "nt")


def executable_name(token: str) -> str:
    name = token.strip("\"'")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    lower = name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if lower.endswith(suffix):
            lower = lower[: -len(suffix)]
    return lower


def package_install_request(command: str) -> PackageInstallRequest | None:
    tokens = split_command(command)
    if not tokens:
        return None

    executable = executable_name(tokens[0])
    if executable == "conda":
        parsed = parse_conda_run(tokens)
        if parsed is None:
            return None
        _, inner_tokens = parsed
        pip_request = _pip_install_request(command, inner_tokens)
        return _with_manager(pip_request, "conda/pip")

    if executable == "uv":
        return _uv_install_request(command, tokens)
    if executable == "poetry":
        return _poetry_install_request(command, tokens)
    return _pip_install_request(command, tokens)


def validate_package_source_compatibility(command: str) -> None:
    request = package_install_request(command)
    if request is None:
        return

    pytorch_sources = [source for source in request.sources if _is_pytorch_index(source)]
    if not pytorch_sources:
        return

    incompatible_packages = [
        package for package in request.packages if _base_package_name(package) not in PYTORCH_PACKAGES
    ]
    if incompatible_packages:
        packages = ", ".join(incompatible_packages)
        raise ValueError(
            "Blocked Python package install command that mixes the PyTorch wheel index "
            f"with non-PyTorch packages ({packages}). Split PyTorch packages and normal PyPI packages into separate commands: {command}"
        )


def normalize_execution_command(command: str, project_dir: Path, use_project_venv: bool = False) -> str:
    tokens = split_command(command)
    if use_project_venv and _is_conda_create(tokens):
        python_version = _conda_create_python_version(tokens) or "3.11"
        return subprocess.list2cmdline([agent_python(), "-m", "venv", f"--prompt=deploy-{python_version}", str(project_venv_path(project_dir))])

    parsed = parse_conda_run(tokens)
    if use_project_venv and parsed is not None:
        _, inner_tokens = parsed
        return _rewrite_python_like_command(inner_tokens, project_venv_python(project_dir))

    if _is_python_like_command(tokens):
        target_python = project_venv_python(project_dir) if use_project_venv else agent_python()
        return _rewrite_python_like_command(tokens, target_python)

    if parsed is None:
        return command

    env_name, inner_tokens = parsed
    if not inner_tokens:
        return command

    inner_executable = executable_name(inner_tokens[0])
    if inner_executable in {"pip", "pip3"}:
        rewritten_tokens = [conda_env_python(env_name), "-m", "pip", *inner_tokens[1:]]
        return subprocess.list2cmdline(rewritten_tokens)
    if inner_executable in {"python", "python3", "py"}:
        rewritten_tokens = [conda_env_python(env_name), *inner_tokens[1:]]
        return subprocess.list2cmdline(rewritten_tokens)
    return command


def plan_uses_project_venv(commands: list[str]) -> bool:
    return any(_is_conda_create(split_command(command)) for command in commands)


def project_venv_path(project_dir: Path) -> Path:
    return project_dir / ".deploy-agent" / "venv"


def project_venv_python(project_dir: Path) -> str:
    venv = project_venv_path(project_dir)
    python_path = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(python_path)


def agent_python() -> str:
    return str(Path(sys.executable))


def parse_conda_run(tokens: list[str]) -> tuple[str, list[str]] | None:
    lowered = [token.strip("\"'").lower() for token in tokens]
    if len(lowered) < 4 or lowered[0] != "conda" or lowered[1] != "run":
        return None

    index = 2
    env_name = ""
    while index < len(tokens):
        token = lowered[index]
        if token in {"-n", "--name"}:
            if index + 1 >= len(tokens):
                return None
            env_name = tokens[index + 1].strip("\"'")
            index += 2
            continue
        if token == "--no-capture-output":
            index += 1
            continue
        break

    if not env_name or index >= len(tokens):
        return None
    return env_name, tokens[index:]


def conda_env_python(env_name: str) -> str:
    completed = subprocess.run(
        "conda info --json",
        shell=True,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Unable to inspect conda environments: {completed.stderr or completed.stdout}")

    info = json.loads(completed.stdout)
    envs = [Path(path) for path in info.get("envs", [])]
    for env_path in envs:
        if env_path.name.lower() == env_name.lower():
            python_path = env_path / ("python.exe" if os.name == "nt" else "bin/python")
            if python_path.exists():
                return str(python_path)
            raise RuntimeError(f"Conda environment does not contain Python: {python_path}")

    raise RuntimeError(f"Conda environment not found: {env_name}")


def _is_conda_create(tokens: list[str]) -> bool:
    lowered = [token.strip("\"'").lower() for token in tokens]
    return len(lowered) >= 2 and lowered[0] == "conda" and lowered[1] == "create"


def _conda_create_python_version(tokens: list[str]) -> str | None:
    for token in tokens:
        lowered = token.strip("\"'").lower()
        if lowered.startswith("python="):
            return lowered.split("=", 1)[1]
    return None


def _is_python_like_command(tokens: list[str]) -> bool:
    return bool(tokens) and executable_name(tokens[0]) in {"pip", "pip3", "python", "python3", "py"}


def _rewrite_python_like_command(tokens: list[str], python_path: str) -> str:
    if not tokens:
        return ""
    executable = executable_name(tokens[0])
    if executable in {"pip", "pip3"}:
        return subprocess.list2cmdline([python_path, "-m", "pip", *tokens[1:]])
    if executable in {"python", "python3", "py"}:
        return subprocess.list2cmdline([python_path, *tokens[1:]])
    return subprocess.list2cmdline(tokens)


def _pip_install_request(command: str, tokens: list[str]) -> PackageInstallRequest | None:
    lowered = [token.strip("\"'").lower() for token in tokens]
    pip_executable = executable_name(tokens[0]) if tokens else ""
    if pip_executable not in {"pip", "pip3", "python", "python3", "py"} or "install" not in lowered:
        return None

    install_index = lowered.index("install")
    packages: list[str] = []
    requirement_files: list[str] = []
    editable_installs: list[str] = []
    sources: list[str] = []
    skip_next = False
    for index, token in enumerate(tokens[install_index + 1 :], start=install_index + 1):
        lowered_token = token.strip("\"'").lower()
        if skip_next:
            skip_next = False
            continue
        if lowered_token in {"-r", "--requirement"} and index + 1 < len(tokens):
            requirement_files.append(tokens[index + 1].strip("\"'"))
            skip_next = True
            continue
        if lowered_token.startswith("--requirement="):
            requirement_files.append(token.split("=", 1)[1].strip("\"'"))
            continue
        if lowered_token in {"-e", "--editable"} and index + 1 < len(tokens):
            editable_installs.append(tokens[index + 1].strip("\"'"))
            skip_next = True
            continue
        if lowered_token.startswith("--editable="):
            editable_installs.append(token.split("=", 1)[1].strip("\"'"))
            continue
        if lowered_token in {"-i", "--index-url", "--extra-index-url"} and index + 1 < len(tokens):
            sources.append(tokens[index + 1].strip("\"'"))
            skip_next = True
            continue
        if lowered_token.startswith(("--index-url=", "--extra-index-url=")):
            sources.append(token.split("=", 1)[1].strip("\"'"))
            continue
        if lowered_token in PIP_OPTIONS_WITH_VALUES and index + 1 < len(tokens):
            skip_next = True
            continue
        if lowered_token.startswith(PIP_OPTIONS_WITH_INLINE_VALUES):
            continue
        if lowered_token.startswith("-"):
            continue
        packages.append(token.strip("\"'"))

    if not packages and not requirement_files and not editable_installs:
        return None
    return PackageInstallRequest(
        command=command,
        manager="pip",
        packages=tuple(packages),
        requirement_files=tuple(requirement_files),
        editable_installs=tuple(editable_installs),
        sources=tuple(sources),
    )


def _uv_install_request(command: str, tokens: list[str]) -> PackageInstallRequest | None:
    lowered = [token.strip("\"'").lower() for token in tokens]
    if len(lowered) < 2:
        return None
    if lowered[1] == "pip":
        pip_request = _pip_install_request(command, ["pip", *tokens[2:]])
        return _with_manager(pip_request, "uv/pip")
    if lowered[1] == "run" and len(tokens) >= 3:
        pip_request = _pip_install_request(command, tokens[2:])
        return _with_manager(pip_request, "uv run/pip")
    if lowered[1] in {"add", "tool"}:
        start = 2 if lowered[1] == "add" else 3
        packages = tuple(token.strip("\"'") for token in tokens[start:] if not token.startswith("-"))
        return PackageInstallRequest(command=command, manager="uv", packages=packages) if packages else None
    if lowered[1] == "sync":
        return PackageInstallRequest(command=command, manager="uv", project_installs=("uv sync",))
    return None


def _poetry_install_request(command: str, tokens: list[str]) -> PackageInstallRequest | None:
    lowered = [token.strip("\"'").lower() for token in tokens]
    if len(lowered) < 2:
        return None
    if lowered[1] == "add":
        packages = tuple(token.strip("\"'") for token in tokens[2:] if not token.startswith("-"))
        return PackageInstallRequest(command=command, manager="poetry", packages=packages) if packages else None
    if lowered[1] == "run" and len(tokens) >= 3:
        pip_request = _pip_install_request(command, tokens[2:])
        return _with_manager(pip_request, "poetry run/pip")
    if lowered[1] == "install":
        return PackageInstallRequest(command=command, manager="poetry", project_installs=("poetry install",))
    return None


def _with_manager(request: PackageInstallRequest | None, manager: str) -> PackageInstallRequest | None:
    if request is None:
        return None
    return PackageInstallRequest(
        command=request.command,
        manager=manager,
        packages=request.packages,
        requirement_files=request.requirement_files,
        editable_installs=request.editable_installs,
        sources=request.sources,
        project_installs=request.project_installs,
    )


def _is_pytorch_index(source: str) -> bool:
    lowered = source.lower()
    return any(marker in lowered for marker in PYTORCH_INDEX_HOST_MARKERS)


def _base_package_name(package: str) -> str:
    return re.split(r"\[|===|==|>=|<=|~=|!=|>|<", package.strip().lower(), maxsplit=1)[0]
