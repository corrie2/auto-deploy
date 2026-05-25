# Auto Deploy Agent

A local LangGraph deployment agent. Run `deploy`, paste a GitHub web project URL, and the agent will clone the repo locally, read the README and key project files, ask DeepSeek to generate an install/build/start plan, execute the plan, and start the web service.

The agent is designed for local experimentation. It validates generated commands before execution, asks before installing Python packages, and keeps generated deployment artifacts under `.deployments/`.

## Default LLM

The default model provider is DeepSeek OpenAI-compatible API:

```text
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash
API key env var=DEEPSEEK_API_KEY
```

You still need to provide your DeepSeek API key:

```powershell
$env:DEEPSEEK_API_KEY="your_deepseek_api_key"
```

Or pass it when running:

```powershell
.\.venv\Scripts\deploy.exe https://github.com/corrie2/ppt-agent-V2 --llm-api-key "your_deepseek_api_key"
```

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## Usage

Interactive mode:

```powershell
.\.venv\Scripts\deploy.exe
```

Direct mode:

```powershell
.\.venv\Scripts\deploy.exe https://github.com/corrie2/ppt-agent-V2
```

Preview the plan without executing install/start commands:

```powershell
.\.venv\Scripts\deploy.exe https://github.com/corrie2/ppt-agent-V2 --dry-run
```

Override model settings:

```powershell
.\.venv\Scripts\deploy.exe https://github.com/corrie2/ppt-agent-V2 `
  --llm-api-key "your_key" `
  --llm-base-url "https://api.deepseek.com" `
  --llm-model "deepseek-v4-flash"
```

## Dependency Environments

The agent tries to avoid polluting global Python or Conda environments.

When the generated plan indicates that a project needs a fresh Python environment, the agent creates a project-local virtual environment:

```text
.deployments/<repo-name>/.deploy-agent/venv
```

Generated commands such as:

```powershell
conda create -n app python=3.11 -y
conda run -n app pip install fastapi
conda run -n app python start.py
```

are executed through the project-local venv instead:

```powershell
.deploy-agent\venv\Scripts\python.exe -m pip install fastapi
.deploy-agent\venv\Scripts\python.exe start.py
```

If the plan does not request a new environment, plain `pip` or `python` commands run through the current `auto-deploy-agent` Python environment.

Before any Python package installation, the agent prints the packages, requirement files, editable installs, package indexes, and install target. Installation continues only after you approve:

```text
Approve package installation? [y/N]:
```

## Safety Rules

The safety layer blocks obvious dangerous commands and shell composition, including:

```text
rm / del / format / shutdown
curl / wget
powershell / cmd / bash / sh
&& / ; / | / redirects
```

Python package installs are not controlled by a hard-coded package whitelist. Instead, they require explicit user approval before execution.

Special package indexes are validated. For example, PyTorch CPU wheels must be installed separately from normal PyPI packages:

```powershell
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install jupyterlab numpy
```

## Healthcheck

HTTP healthchecks are handled by the runner instead of generated shell commands. The plan should set `healthcheck_url`, and the runner will request it directly.

Only local healthcheck hosts are allowed:

```text
localhost
127.0.0.1
::1
```

HTTP `2xx` and `3xx` responses are treated as healthy.

## Cleanup

To remove dependencies installed for a deployed project, delete its generated venv:

```powershell
Remove-Item -Recurse -Force .\.deployments\<repo-name>\.deploy-agent\venv
```

To remove the whole cloned deployment:

```powershell
Remove-Item -Recurse -Force .\.deployments\<repo-name>
```

## Flow

1. Check local prerequisites such as `git`.
2. Clone or update the GitHub repository under `.deployments/<repo-name>`.
3. Read README and key files such as `package.json`, `Dockerfile`, and `requirements.txt`.
4. Ask DeepSeek to generate a structured deployment plan.
5. Validate generated commands and block clearly dangerous commands.
6. Ask for approval before Python package installation.
7. Normalize Python environment commands to the selected local environment.
8. Execute install/build commands locally.
9. Start the web service as a background process.
10. Run the local healthcheck URL when provided.
