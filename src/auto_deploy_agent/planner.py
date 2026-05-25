from __future__ import annotations

import json
import os
import platform
import re

from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from auto_deploy_agent.models import DeployPlan, ProjectInspection


DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"


SYSTEM_PROMPT = """You are a senior deployment engineer.
Create a conservative local deployment plan for a web project on the user's machine.

Rules:
- Use only commands that can run non-interactively.
- Prefer project README instructions.
- Prefer existing package managers and lockfiles.
- Generate commands for the current local operating system.
- Do not join commands with &&, ;, pipes, redirects, or shell-specific control operators.
- Return one command object per install/build/start step.
- The runner starts commands with phase=start as background processes, so start commands do not need nohup.
- For start commands, use the normal foreground dev/server command such as npm run dev, pnpm dev, python app.py, or docker compose up.
- Do not generate curl, wget, or shell-based HTTP healthcheck commands.
- Put the local service healthcheck endpoint in healthcheck_url instead of a verify command.
- Do not use destructive commands.
- Do not install random global packages unless the README or detected stack needs them.
- Do not use conda activate. On Windows, use conda run -n <env> python -m pip install ... and conda run -n <env> python script.py.
- Do not create global Conda environments unless the project has a real Conda-only dependency such as environment.yml or conda-only packages.
- If the README asks to create a generic Python environment, prefer python -m venv .deploy-agent\\venv inside the project directory.
- Prefer python -m pip install over bare pip install when running inside a Python or Conda environment.
- If a pip command uses --index-url or --extra-index-url for a special package index, install only packages from that index in that command.
- For PyTorch CPU wheels, install torch/torchvision/torchaudio with https://download.pytorch.org/whl/cpu in a separate command from normal PyPI packages such as numpy, jupyterlab, fastapi, uvicorn, or python-multipart.
- If the project uses a checked-in .venv, use .venv\\Scripts\\python.exe on Windows or .venv/bin/python on Unix.
- If the project uses uv, prefer uv sync for project dependencies and uv run <command> for runtime commands.
- If the project uses Poetry, prefer poetry install for project dependencies and poetry run <command> for runtime commands.
- If environment variables are needed but not provided, list them in assumptions and use safe placeholders.
- Return commands in execution order.
- Return only valid JSON. Do not wrap the JSON in markdown fences.
"""


def build_deploy_plan(
    *,
    model: str,
    project_dir: str,
    project: ProjectInspection,
    healthcheck_url: str | None = None,
) -> DeployPlan:
    base_url = os.getenv("OPENAI_BASE_URL", DEFAULT_LLM_BASE_URL)
    api_key = os.getenv(DEEPSEEK_API_KEY_ENV) or os.getenv("OPENAI_API_KEY")
    llm = ChatOpenAI(model=model, temperature=0, base_url=base_url, api_key=api_key)
    prompt = f"""
Local operating system: {platform.system()} {platform.release()}
Project directory: {project_dir}
Preferred healthcheck URL: {healthcheck_url or "not provided"}

Return a JSON object matching this schema:
{{
  "summary": "short deployment summary",
  "detected_stack": "detected framework/runtime/package manager",
  "assumptions": ["assumption 1"],
  "environment": {{"ENV_NAME": "placeholder_or_value"}},
  "commands": [
    {{
      "name": "install dependencies",
      "command": "command to run locally",
      "cwd": "{project_dir}",
      "phase": "install",
      "timeout_seconds": 600
    }}
  ],
  "healthcheck_url": "http://localhost:8000/health or null"
}}

Allowed command phases: install, build, start, verify, other.
Do not include verify commands for HTTP healthchecks. The runner verifies healthcheck_url itself.

README:
{project.readme}

Detected file tree:
{project.tree}

Key files:
{project.files}
"""
    response = llm.invoke(
        [
            ("system", SYSTEM_PROMPT),
            ("human", prompt),
        ]
    )
    content = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_plan(content)


def _parse_plan(content: str) -> DeployPlan:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"LLM did not return JSON: {content[:500]}")
        data = json.loads(match.group(0))

    try:
        return DeployPlan.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"LLM returned invalid deployment plan: {exc}") from exc
