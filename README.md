# Prompt Driven Development (PDD)

**Prompt Driven Development (PDD)** places AI-assisted code generation—via structured natural language prompts—at the center of the development lifecycle. Developers craft prompts to orchestrate an LLM/agent that generates or modifies code, architecture, and documentation.

## Documentation

- **[Fundamentals of PDD](docs/PDD_fundamentals.md)** – What PDD is, how it fits the lifecycle, and core components.
- **[Detailed examples of coding using PDD](docs/PDD_examples.md)** – Concrete prompts and what the agent did (tools, files).

## How to run

1. **Environment**
   - Python 3.10+
   - Set `GROQ_API_KEY` (e.g. `export GROQ_API_KEY=your_key`).

2. **Install and migrate**
   ```bash
   cd project
   pip install -r ../requirements.txt   # or from repo root: pip install -r requirements.txt
   python manage.py migrate
   python manage.py createsuperuser     # optional, for admin
   ```

3. **Start server**
   ```bash
   python manage.py runserver
   ```

4. **Use the app**
   - Open **http://127.0.0.1:8000/** → redirects to login (or chat if logged in).
   - Log in, go to **Chat**, create a **project** (name + slug), then type a prompt (e.g. “Create a Python file hello.py that prints Hello, World!”). The PDD agent will run in that workspace.

## Jupyter (optional)

From repo root:
```bash
cd src
# Ensure Django project is on PYTHONPATH and cwd is project/
import setup; setup.init()
from AI.agents import get_cursor_agent
# ... invoke agent with config (user_id, project_id, etc.)
```

## Project layout

- **project/** – Django app (agent, tools, API, frontend).
- **project/app/** – Web app: Workspace model, chat API, login/chat views and templates.
- **project/AI/** – LLM, agent, tools, prompts.
- **docs/** – PDD fundamentals and examples.
- **src/** – Jupyter notebooks and setup for local testing.
_ **docker** - Docker