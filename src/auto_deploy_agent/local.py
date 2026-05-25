from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from auto_deploy_agent.models import CommandResult, ProjectInspection


KEY_FILES = [
    "README.md",
    "README.MD",
    "readme.md",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "bun.lockb",
    "Dockerfile",
    "docker-compose.yml",
    "compose.yml",
    "requirements.txt",
    "pyproject.toml",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "next.config.mjs",
]


def check_local_prerequisites() -> None:
    if shutil.which("git") is None:
        raise RuntimeError("git is not installed or not available in PATH.")


def clone_or_update_repo(repo: str, project_dir: Path, branch: str | None = None) -> None:
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    if (project_dir / ".git").exists():
        _ensure_expected_remote(project_dir, repo)
        _run_checked("git fetch --all --prune", project_dir, "fetch repository", 300)
        if branch:
            _ensure_clean_worktree(project_dir)
            _run_checked(f"git checkout {_quote(branch)}", project_dir, "checkout branch", 300)
            _run_checked(f"git reset --hard origin/{_quote(branch)}", project_dir, "reset branch", 300)
        else:
            _run_checked("git pull --ff-only", project_dir, "pull repository", 300)
        return

    command = f"git clone {_quote(repo)} {_quote(str(project_dir))}"
    if branch:
        command = f"git clone --branch {_quote(branch)} {_quote(repo)} {_quote(str(project_dir))}"
    _run_checked(command, project_dir.parent, "clone repository", 600)


def inspect_project(project_dir: Path) -> ProjectInspection:
    files: dict[str, str] = {}
    readme = ""

    for file_name in KEY_FILES:
        path = project_dir / file_name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            files[file_name] = text[-12000:]
            if file_name.lower().startswith("readme") and not readme:
                readme = text[-20000:]

    tree_lines: list[str] = []
    ignored = {".git", "node_modules", ".next", "dist", "build", ".venv", "__pycache__"}
    for root, dirs, file_names in os.walk(project_dir):
        dirs[:] = [name for name in dirs if name not in ignored]
        rel_root = Path(root).relative_to(project_dir)
        depth = 0 if str(rel_root) == "." else len(rel_root.parts)
        if depth > 2:
            dirs[:] = []
            continue
        for file_name in sorted(file_names):
            rel_path = rel_root / file_name
            tree_lines.append(str(rel_path).replace("\\", "/"))
            if len(tree_lines) >= 200:
                break
        if len(tree_lines) >= 200:
            break

    return ProjectInspection(
        readme=readme or "No README file was found.",
        files=files,
        tree="\n".join(tree_lines),
    )


def run_command(
    command: str,
    cwd: Path,
    name: str,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> CommandResult:
    return _run(command, cwd, name, timeout_seconds, env=env)


def start_background_command(
    command: str,
    cwd: Path,
    name: str,
    env: dict[str, str] | None = None,
    startup_check_seconds: float = 2.0,
) -> CommandResult:
    log_dir = cwd / ".deploy-agent" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(char if char.isalnum() else "-" for char in name.lower()).strip("-") or "start"
    stdout_path = log_dir / f"{safe_name}.out.log"
    stderr_path = log_dir / f"{safe_name}.err.log"

    stdout_file = stdout_path.open("ab")
    stderr_file = stderr_path.open("ab")
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        shell=True,
        stdout=stdout_file,
        stderr=stderr_file,
        env=_merged_env(env),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    time.sleep(startup_check_seconds)
    exit_status = process.poll()
    stdout_file.close()
    stderr_file.close()
    if exit_status is not None:
        return CommandResult(
            name=name,
            command=command,
            exit_status=exit_status,
            stdout=_read_tail(stdout_path),
            stderr=_read_tail(stderr_path),
        )

    return CommandResult(
        name=name,
        command=command,
        exit_status=0,
        stdout=(
            f"Started background process pid={process.pid}\n"
            f"stdout log: {stdout_path}\n"
            f"stderr log: {stderr_path}"
        ),
        stderr="",
    )


def check_healthcheck_url(url: str, timeout_seconds: int = 60) -> CommandResult:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Healthcheck URL must use http or https: {url}")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"Healthcheck URL must target localhost: {url}")

    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            request = Request(url, method="GET")
            with urlopen(request, timeout=5) as response:
                status = response.status
                if 200 <= status < 400:
                    return CommandResult(
                        name="healthcheck",
                        command=f"GET {url}",
                        exit_status=0,
                        stdout=f"HTTP {status}",
                        stderr="",
                    )
                last_error = f"HTTP {status}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)

    return CommandResult(
        name="healthcheck",
        command=f"GET {url}",
        exit_status=1,
        stdout="",
        stderr=f"Healthcheck failed for {url}: {last_error}",
    )


def resolve_command_cwd(project_dir: Path, command_cwd: str | None) -> Path:
    project_root = project_dir.resolve()
    cwd = project_root if command_cwd is None else Path(command_cwd)
    if not cwd.is_absolute():
        cwd = project_root / cwd
    resolved = cwd.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"Command cwd must stay inside project directory: {resolved}") from exc
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Command cwd does not exist or is not a directory: {resolved}")
    return resolved


def _run(
    command: str,
    cwd: Path,
    name: str,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
        env=_merged_env(env),
    )
    return CommandResult(
        name=name,
        command=command,
        exit_status=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _run_checked(command: str, cwd: Path, name: str, timeout_seconds: int) -> CommandResult:
    result = _run(command, cwd, name, timeout_seconds)
    if result.exit_status != 0:
        raise RuntimeError(f"{name} failed: {result.stderr or result.stdout}")
    return result


def _quote(value: str) -> str:
    return subprocess.list2cmdline([value])


def _ensure_clean_worktree(project_dir: Path) -> None:
    result = _run("git status --porcelain", project_dir, "check worktree", 60)
    if result.exit_status != 0:
        raise RuntimeError(f"Unable to check git worktree: {result.stderr or result.stdout}")
    if result.stdout.strip():
        raise RuntimeError(
            "Refusing to checkout/reset branch because the existing repository has local changes. "
            "Commit, stash, or remove those changes first."
        )


def _ensure_expected_remote(project_dir: Path, expected_repo: str) -> None:
    result = _run("git remote get-url origin", project_dir, "check repository remote", 60)
    if result.exit_status != 0:
        raise RuntimeError(f"Unable to check repository remote: {result.stderr or result.stdout}")

    actual = result.stdout.strip()
    if _normalized_repo_url(actual) != _normalized_repo_url(expected_repo):
        raise RuntimeError(
            "Refusing to update existing repository because origin does not match the requested repo. "
            f"origin={actual!r} requested={expected_repo!r}"
        )


def _normalized_repo_url(repo: str) -> str:
    value = repo.strip()
    if value.endswith(".git"):
        value = value[:-4]

    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path.lower()}"

    if ":" in value and "/" in value and not parsed.scheme:
        host, path = value.split(":", 1)
        if "@" in host:
            host = host.split("@", 1)[1]
        return f"ssh://{host.lower()}/{path.strip('/').lower()}"

    try:
        return str(Path(value).expanduser().resolve()).lower()
    except OSError:
        return value.rstrip("/\\").lower()


def _merged_env(extra: dict[str, str] | None) -> dict[str, str]:
    merged = os.environ.copy()
    merged.setdefault("PYTHONUTF8", "1")
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        merged.update({key: str(value) for key, value in extra.items()})
    return merged


def _read_tail(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()[-limit:]
    return data.decode("utf-8", errors="replace")
