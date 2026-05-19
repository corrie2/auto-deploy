# Auto Deploy Agent

A local LangGraph deployment agent. Run `deploy`, paste a GitHub web project URL, and the agent will clone the repo locally, read the README and key project files, ask DeepSeek to generate an install/build/start plan, execute the plan, and start the web service.

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

## Flow

1. Check local prerequisites such as `git`.
2. Clone or update the GitHub repository under `.deployments/<repo-name>`.
3. Read README and key files such as `package.json`, `Dockerfile`, and `requirements.txt`.
4. Ask DeepSeek to generate a structured deployment plan.
5. Block clearly dangerous commands.
6. Execute install/build commands locally.
7. Start the web service as a background process.
