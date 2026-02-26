# Prompt Driven Development (PDD)

Prompt Driven Development (PDD) is an **AI-assisted software development environment** where you build real applications by writing **natural-language prompts** instead of manually writing all the code.

A LangGraph-powered LLM agent operates inside an **isolated workspace**, using controlled tools (file I/O, command execution, git, search, locking) to **create, modify, and run a real codebase** — similar in spirit to tools like AI Cursor, but designed as an extensible research and engineering platform.

---

## What PDD Includes

### 🧩 Django Web Application

* Authentication (signup/login)
* Workspace and project management
* Chat API for interacting with the AI agent
* Web-based IDE (file tree, editor, command runner, git status)

### 🤖 AI Agent Layer

* LangGraph **ReAct-style agent**
* Rich toolset (`AI/tools/tools.py`)
* PDD-specific system prompts (`AI/prompts.py`)
* Workspace-aware execution with project-level locking

### 🖥️ Electron Desktop Application

* **Use My Computer** mode (local folder + terminal + chat)
* **Use PDD Website** mode (embedded browser UI)

### 🐳 Docker Setup

* Dockerfile + `compose.yml`
* Runs Django app and Postgres together
* Optional host directory mounting for local workspace access

---

## Key Features

### Prompt-Centric Workflow

Describe what you want in plain English. The agent:

* Reads and edits files
* Runs commands
* Uses git to track changes

### Workspace Isolation

* Each project is identified by a **slug**
* Each slug maps to a dedicated workspace directory on disk
* Prevents cross-project file interference

### Cursor-Like Tooling

The agent can use tools such as:

* `create_workspace`, `list_files`
* `read_file`, `write_file`, `apply_patch`
* `run_command` (allow-listed runtimes, optionally via Docker)
* `git_init`, `git_status`, `git_diff`, `git_commit`
* `file_exists`, `make_dir`
* Project-level locking for safety

### Web IDE

* File tree (left)
* Code editor (center)
* AI chat panel (right)
* “Run” API to execute commands in the workspace

### Desktop App

* **Use My Computer**: operate directly on a local folder
* **Use PDD Website**: embed `/chat` and `/ide` inside Electron

---

## How to Run (Python / Django)

### 1. Environment

* Python **3.10+**
* Install system tools you want the agent to use (e.g. `git`, `python`, `node`)

Set your OpenAI API key:

```bash
export OPENAI_API_KEY=sk-your-key-here
```

Or place it in `.env` at the repo root:

```env
OPENAI_API_KEY=sk-your-key-here
```

---

### 2. Install Dependencies and Migrate

From the repo root:

```bash
cd project
pip install -r ../requirements.txt   # or: pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser     # optional
```

---

### 3. Start the Server

```bash
python manage.py runserver
```

The app will be available at:

```
http://127.0.0.1:8000/
```

---

### 4. Use the Web App

1. Open `http://127.0.0.1:8000/`
2. Sign up / log in
3. Go to **Chat**
4. Create a project (name + slug)
5. Send a prompt, for example:

```
Create a Python file hello.py that prints Hello, World!
```

The PDD agent will:

* Create a workspace for the slug
* Write the file
* Show it in the Web IDE

---

## How to Run (Docker)

From the repo root:

```bash
docker compose up
```

This starts:

* `pdd-app` (Django) on port **8000**
* `pdd-db` (Postgres)

Environment variables are read from `.env` at the repo root.

### Local Workspace Access (Docker)

To support **Use My Computer** inside Docker, `compose.yml` already mounts:

```yaml
- /Users:/Users
```

This allows paths like `/Users/yourname/...` to exist inside the container.

---

## Desktop App (Electron)

From the repo root:

```bash
cd desktop
npm install
npm start
```

### Desktop Modes

#### Use My Computer

* Select a local folder
* Left: file tree
* Center: editor + integrated terminal
* Right: chat panel (talks to Django server)

#### Use PDD Website

* Embeds the web UI (`/chat`, `/ide`) inside Electron

### Requirements for Use My Computer

* Django server running locally:

```bash
python manage.py runserver
```

* Environment variable enabled:

```env
PDD_ALLOW_LOCAL_WORKSPACE_PATH=true
```

The selected folder path is passed as `local_path` and used as the workspace root.

---

## Jupyter / Programmatic Agent Usage (Optional)

Useful for notebooks and experiments.

From the repo root:

```python
cd src

# Ensure Django project is on PYTHONPATH and cwd is project/
import setup
setup.init()

from AI.agents import get_cursor_agent
from langchain_core.runnables import RunnableConfig

agent = get_cursor_agent()
config = RunnableConfig(configurable={
    "user_id": "1",
    "project_id": "example-project",
})

response = agent.invoke(
    {
        "messages": [
            {"role": "user", "content": "Create a Python file hello.py that prints Hello"}
        ]
    },
    config,
)
```

---

## Project Layout

```
project/            # Django project
  app/              # Workspace model, APIs, views, templates
  AI/               # LLM agent, tools, prompts
  project/          # Django settings, URLs, ASGI/WSGI

desktop/            # Electron desktop app
src/                # Jupyter notebooks & helper scripts
docs/               # Agent testing & documentation
compose.yml         # Docker Compose (app + Postgres)
Dockerfile          # App container image
```

---

## Next Steps

If you want, I can also generate:

* `docs/PDD_fundamentals.md
