from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class DeployCommand(BaseModel):
    name: str = Field(description="Short label for this command.")
    command: str = Field(description="Shell command to execute locally.")
    cwd: str | None = Field(default=None, description="Working directory for this command.")
    phase: Literal["install", "build", "start", "verify", "other"] = "other"
    timeout_seconds: int = 600


class DeployPlan(BaseModel):
    summary: str
    detected_stack: str
    assumptions: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    commands: list[DeployCommand]
    healthcheck_url: str | None = None


class CommandResult(BaseModel):
    name: str
    command: str
    exit_status: int
    stdout: str
    stderr: str


class ProjectInspection(BaseModel):
    readme: str
    files: dict[str, str]
    tree: str


class AgentState(BaseModel):
    repo: str
    branch: str | None = None
    project_dir: str
    healthcheck_url: str | None = None
    dry_run: bool = False
    force_execute: bool = True
    project: ProjectInspection | None = None
    plan: DeployPlan | None = None
    results: list[CommandResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def project_path(self) -> Path:
        return Path(self.project_dir)
