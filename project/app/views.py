import json
import os
import shlex
import subprocess
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import Workspace


@require_GET
def index(request):
    """Redirect / to /home/ if logged in, else /login/."""
    if request.user.is_authenticated:
        return redirect("home")
    return redirect("login")


def _ensure_workspace_dir_exists(user_id: str, project_id: str) -> Path:
    """Create workspace directory if it doesn't exist. Return path."""
    base = getattr(settings, "AI_WORKSPACE_DIR", None) or Path("workspace")
    base = Path(base).resolve()
    path = (base / str(user_id) / str(project_id)).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- API ----------

@require_POST
@login_required
def chat_api(request):
    """POST /api/chat/  Body: {"project_id": "slug", "message": "user prompt"}  -> {"content": "..."}"""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    project_id = (body.get("project_id") or "").strip()
    message = (body.get("message") or "").strip()
    if not project_id or not message:
        return JsonResponse({"error": "project_id and message required"}, status=400)

    try:
        user_id = str(request.user.pk)
        _ensure_workspace_dir_exists(user_id, project_id)

        from AI.agents import get_cursor_agent
        from langchain_core.runnables import RunnableConfig
        from AI.prompts import PDD_USE_TOOLS_INSTRUCTION

        config = {
            "configurable": {
                "user_id": user_id,
                "project_id": project_id,
                "run_id": str(uuid.uuid4()),
                "thread_id": str(uuid.uuid4()),
            },
            "recursion_limit": 120,  # Allow many steps for "Django + frontend" in one prompt
        }
        runnable_config = RunnableConfig(configurable=config["configurable"], recursion_limit=config.get("recursion_limit", 120))

        # Nudge model to use tools for action-like requests (create, run, add, etc.)
        action_hint = ""
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in ("create", "add", "run", "build", "django", "crud", "project", "app", "todo", "frontend")):
            action_hint = (
                "You MUST respond with tool calls only—do not reply with explanatory text instead of calling tools. "
                f"Start by calling create_workspace(project_id='{project_id}'). "
                "Then call run_command, write_file, etc. as needed. Actually create the files and run the commands.\n\n"
            )
        user_content = (
            f"Current workspace project_id: {project_id}. "
            f"You MUST use project_id='{project_id}' in create_workspace and in all tool config. "
            f"All file paths are relative to this workspace root.\n\n"
            + action_hint
            + PDD_USE_TOOLS_INSTRUCTION
            + message
        )

        agent = get_cursor_agent()
        try:
            response = agent.invoke(
                {"messages": [{"role": "user", "content": user_content}]},
                runnable_config,
            )

            messages = response.get("messages", [])
            last = messages[-1] if messages else None
            content = getattr(last, "content", None) or str(last) if last else ""

            return JsonResponse({"content": content, "ok": True})
        except Exception as e:
            err_msg = str(e)
            # Handle recursion / step limit (LangGraph can say "need more steps to process this request")
            is_step_limit = (
                "recursion_limit" in err_msg.lower()
                or "GraphRecursionError" in str(type(e).__name__)
                or "need more steps" in err_msg.lower()
                or "need more step" in err_msg.lower()
            )
            if is_step_limit:
                # Try to extract what was done so far
                try:
                    partial_messages = []
                    if hasattr(e, "messages"):
                        partial_messages = getattr(e, "messages", [])
                    if partial_messages:
                        last_msg = partial_messages[-1] if partial_messages else None
                        partial_content = getattr(last_msg, "content", None) or str(last_msg) if last_msg else ""
                        if partial_content and len(partial_content) > 50:
                            err_msg = (
                                f"The request used all allowed steps before finishing.\n\n"
                                f"Last response: {partial_content[:400]}…\n\n"
                                f"**What to do:**\n"
                                f"1. **Split into two prompts** (recommended):\n"
                                f"   - First: \"Create a Django todo project\"\n"
                                f"   - Then: \"Add a frontend using JavaScript\"\n"
                                f"2. Or try a shorter single request."
                            )
                        else:
                            err_msg = (
                                f"The request needed more steps than allowed (complex tasks like "
                                f"\"Django project + frontend\" use many tool calls).\n\n"
                                f"**What to do:**\n"
                                f"1. **Split into two prompts** (recommended):\n"
                                f"   - First: \"Create a Django todo project\"\n"
                                f"   - Then: \"Add a frontend using JavaScript\"\n"
                                f"2. Or try a shorter single request."
                            )
                    else:
                        err_msg = (
                            f"The request needed more steps than allowed.\n\n"
                            f"**What to do:** Split your request into smaller steps. Example:\n"
                            f"- First prompt: \"Create a Django todo project\"\n"
                            f"- Second prompt: \"Add a frontend using JavaScript\""
                        )
                except Exception:
                    err_msg = (
                        "This request needed more steps than allowed. Try splitting it: "
                        "e.g. first \"Create a Django todo project\", then \"Add a frontend using JavaScript\"."
                    )
            
            if getattr(settings, "DEBUG", False):
                err_msg += "\n\n" + traceback.format_exc()
            return JsonResponse({"error": err_msg}, status=500)

    except Exception as e:
        err_msg = str(e)
        if getattr(settings, "DEBUG", False):
            err_msg += "\n\n" + traceback.format_exc()
        return JsonResponse({"error": err_msg}, status=500)


@require_GET
@login_required
def workspace_list_api(request):
    """GET /api/workspaces/  -> list of {id, name, slug} for current user."""
    workspaces = Workspace.objects.filter(user=request.user).values("id", "name", "slug")
    return JsonResponse({"workspaces": list(workspaces)})


def _get_workspace_root_for_user(request, slug: str) -> Optional[Path]:
    """Return workspace Path if current user owns this slug, else None."""
    if not Workspace.objects.filter(user=request.user, slug=slug).exists():
        return None
    return _ensure_workspace_dir_exists(str(request.user.pk), slug)


@require_POST
@login_required
def workspace_create_file_api(request, slug: str):
    """POST /api/workspaces/<slug>/create-file/  Body: {"path": "rel/path", "content": "", "is_dir": false}  -> create file or folder."""
    root = _get_workspace_root_for_user(request, slug)
    if root is None:
        return JsonResponse({"error": "Workspace not found"}, status=404)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    rel = (body.get("path") or "").strip()
    if not rel or rel.startswith(("/", "\\")) or ".." in rel or ":" in rel:
        return JsonResponse({"error": "Invalid path"}, status=400)
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        return JsonResponse({"error": "Path outside workspace"}, status=400)
    if target.exists():
        return JsonResponse({"error": "Path already exists"}, status=400)
    is_dir = bool(body.get("is_dir"))
    content = body.get("content", "") if not is_dir else None
    try:
        if is_dir:
            target.mkdir(parents=True, exist_ok=False)
            return JsonResponse({"path": rel, "created": "dir"})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content is not None else "", encoding="utf-8")
        return JsonResponse({"path": rel, "created": "file"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
@login_required
def workspace_upload_file_api(request, slug: str):
    """POST /api/workspaces/<slug>/upload/  multipart: file=... optional path=... (folder). Saves file into workspace."""
    root = _get_workspace_root_for_user(request, slug)
    if root is None:
        return JsonResponse({"error": "Workspace not found"}, status=404)
    upload_file = request.FILES.get("file")
    if not upload_file:
        return JsonResponse({"error": "No file provided"}, status=400)
    rel_dir = (request.POST.get("path") or "").strip()
    if rel_dir and (rel_dir.startswith(("/", "\\")) or ".." in rel_dir or ":" in rel_dir):
        return JsonResponse({"error": "Invalid path"}, status=400)
    name = (upload_file.name or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return JsonResponse({"error": "Invalid file name"}, status=400)
    target_dir = root if not rel_dir else (root / rel_dir).resolve()
    if root != target_dir and root not in target_dir.parents:
        return JsonResponse({"error": "Path outside workspace"}, status=400)
    if not target_dir.exists():
        return JsonResponse({"error": "Folder does not exist"}, status=404)
    if not target_dir.is_dir():
        return JsonResponse({"error": "Path is not a folder"}, status=400)
    target_file = target_dir / name
    if target_file.exists():
        return JsonResponse({"error": "File already exists"}, status=400)
    max_size = 5 * 1024 * 1024  # 5 MB
    if upload_file.size > max_size:
        return JsonResponse({"error": "File too large (max 5MB)"}, status=400)
    try:
        target_file.write_bytes(upload_file.read())
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    rel = str(target_file.relative_to(root).as_posix())
    return JsonResponse({"path": rel, "uploaded": True})


@require_GET
@login_required
def workspace_files_api(request, slug: str):
    """GET /api/workspaces/<slug>/files/?path=.  -> list of {path, name, is_dir}."""
    root = _get_workspace_root_for_user(request, slug)
    if root is None:
        return JsonResponse({"error": "Workspace not found"}, status=404)
    rel = (request.GET.get("path") or ".").strip()
    if rel.startswith(("/", "\\")) or ".." in rel or ":" in rel:
        return JsonResponse({"error": "Invalid path"}, status=400)
    target = (root / rel).resolve()
    if not target.exists():
        return JsonResponse({"error": "Path not found"}, status=404)
    if root != target and root not in target.parents:
        return JsonResponse({"error": "Path outside workspace"}, status=400)
    if target.is_file():
        return JsonResponse({"files": [{"path": rel, "name": target.name, "is_dir": False}]})
    entries = []
    for p in sorted(target.iterdir()):
        try:
            r = p.relative_to(root)
        except ValueError:
            continue
        entries.append({
            "path": str(r.as_posix()),
            "name": p.name,
            "is_dir": p.is_dir(),
        })
    return JsonResponse({"files": entries})


@require_http_methods(["GET", "POST"])
@login_required
def workspace_file_content_api(request, slug: str):
    """GET /api/workspaces/<slug>/content/?path=...  -> file content.  POST body: {"path": "...", "content": "..."}  -> save file."""
    root = _get_workspace_root_for_user(request, slug)
    if root is None:
        return JsonResponse({"error": "Workspace not found"}, status=404)

    if request.method == "POST":
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        rel = (body.get("path") or "").strip()
        if not rel or rel.startswith(("/", "\\")) or ".." in rel or ":" in rel:
            return JsonResponse({"error": "Invalid path"}, status=400)
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            return JsonResponse({"error": "Path outside workspace"}, status=400)
        content = body.get("content")
        if content is None:
            return JsonResponse({"error": "content required"}, status=400)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
        return JsonResponse({"path": rel, "saved": True})

    rel = (request.GET.get("path") or "").strip()
    if not rel or rel.startswith(("/", "\\")) or ".." in rel or ":" in rel:
        return JsonResponse({"error": "Invalid path"}, status=400)
    target = (root / rel).resolve()
    if not target.exists() or not target.is_file():
        return JsonResponse({"error": "File not found"}, status=404)
    if root not in target.parents and root != target:
        return JsonResponse({"error": "Path outside workspace"}, status=400)
    max_bytes = 500_000
    if target.stat().st_size > max_bytes:
        return JsonResponse({"error": "File too large"}, status=400)
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    return JsonResponse({"path": rel, "content": content})


@login_required
@require_POST
def workspace_create_api(request):
    """POST /api/workspaces/  Body: {"name": "...", "slug": "..."}  -> create workspace and dir."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    name = (body.get("name") or "").strip()
    slug = (body.get("slug") or "").strip()
    if not name or not slug:
        return JsonResponse({"error": "name and slug required"}, status=400)

    slug = "".join(c if c.isalnum() or c in "_-" else "_" for c in slug).strip("_") or "project"
    if Workspace.objects.filter(user=request.user, slug=slug).exists():
        return JsonResponse({"error": "Workspace with this slug already exists"}, status=400)

    workspace = Workspace.objects.create(user=request.user, name=name, slug=slug)
    workspace.ensure_directory_exists()
    return JsonResponse({"id": workspace.id, "name": workspace.name, "slug": workspace.slug})


@require_POST
@login_required
def workspace_unlock_api(request, slug: str):
    """POST /api/workspaces/<slug>/unlock/  -> remove .cursor.lock so next agent run can acquire lock."""
    root = _get_workspace_root_for_user(request, slug)
    if root is None:
        return JsonResponse({"error": "Workspace not found"}, status=404)
    lock_path = root / ".cursor.lock"
    if lock_path.exists():
        lock_path.unlink(missing_ok=True)
        return JsonResponse({"unlocked": True, "message": "Lock removed. You can run the agent again."})
    return JsonResponse({"unlocked": False, "message": "No lock present."})


@require_POST
@login_required
def workspace_run_api(request, slug: str):
    """POST /api/workspaces/<slug>/run/  Body: {"command": "...", "cwd": ".", "timeout": 120}  -> {stdout, stderr, exit_code}."""
    root = _get_workspace_root_for_user(request, slug)
    if root is None:
        return JsonResponse({"error": "Workspace not found"}, status=404)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    command = (body.get("command") or "").strip()
    if not command:
        return JsonResponse({"error": "command required"}, status=400)
    cwd = (body.get("cwd") or ".").strip()
    timeout = int(body.get("timeout") or 120)

    from AI.tools.tools import run_command, _is_command_allowed
    from langchain_core.runnables import RunnableConfig

    try:
        _is_command_allowed(command)
    except Exception as e:
        return JsonResponse({"error": str(e), "stdout": "", "stderr": "", "exit_code": -1}, status=400)

    # runserver never exits, so run it in background and return immediately
    if "runserver" in command.lower():
        try:
            run_env = os.environ.copy()
            run_env.pop("DJANGO_SETTINGS_MODULE", None)  # let workspace manage.py set myproject.settings
            proc = subprocess.Popen(
                shlex.split(command),
                cwd=str(root),
                env=run_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            time.sleep(1.5)
            first_out = ""
            if proc.stdout:
                try:
                    first_out = proc.stdout.read(2000) or ""
                except Exception:
                    pass
            if proc.poll() is not None:
                # Process exited (e.g. port in use, error)
                return JsonResponse({
                    "stdout": first_out.strip() or "(no output)",
                    "stderr": "",
                    "exit_code": proc.returncode or -1,
                })
            out_msg = first_out.strip() or "Server process started."
            return JsonResponse({
                "stdout": out_msg + "\n\n(Server is running in the background. Open http://127.0.0.1:8000/ on this host to use your app.)",
                "stderr": "",
                "exit_code": 0,
            })
        except Exception as e:
            return JsonResponse({"error": str(e), "stdout": "", "stderr": "", "exit_code": -1}, status=400)

    config = RunnableConfig(configurable={
        "user_id": str(request.user.pk),
        "project_id": slug,
    })
    try:
        result = run_command.invoke(
            {"command": command, "cwd": cwd, "timeout": timeout},
            config=config,
        )
    except Exception as e:
        return JsonResponse({"error": str(e), "stdout": "", "stderr": "", "exit_code": -1}, status=400)
    return JsonResponse({
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("exit_code", -1),
    })


# ---------- Frontend (HTML) ----------

@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect("home")
    else:
        form = AuthenticationForm(request)
    return render(request, "app/login.html", {"form": form})


@require_http_methods(["GET", "POST"])
def signup_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("home")
    else:
        form = UserCreationForm()
    return render(request, "app/signup.html", {"form": form})


@require_GET
@login_required
def home_view(request):
    """Home dashboard: create project CTA and list of workspaces."""
    workspaces = list(
        Workspace.objects.filter(user=request.user).values("id", "name", "slug", "created_at").order_by("-created_at")
    )
    return render(request, "app/home.html", {"workspaces": workspaces})


@require_GET
@login_required
def chat_page(request):
    """Chat UI: list workspaces, prompt input, submit via JS to API and show response."""
    workspaces = list(Workspace.objects.filter(user=request.user).values("id", "name", "slug"))
    return render(request, "app/chat.html", {"workspaces": workspaces})


@require_GET
@login_required
def ide_page(request):
    """IDE-like page: file tree (left), code viewer (center), AI chat (right)."""
    workspaces = list(Workspace.objects.filter(user=request.user).values("id", "name", "slug"))
    return render(request, "app/ide.html", {"workspaces": workspaces})
