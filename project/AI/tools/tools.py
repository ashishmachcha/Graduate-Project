import os 
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from asgiref.sync import async_to_sync
from django.conf import settings
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool


USE_PERMIT = True
try:
    from mypermit import permit_client as permit
except Exception:
    USE_PERMIT = False
    permit = None

def _get_workspace_dir() -> Path:
    """Lazy access to workspace directory to avoid Django settings access at import time."""
    try:
        workspace_path = getattr(settings, "AI_WORKSPACE_DIR", None)
        if workspace_path is None:
            workspace_path = os.getenv("AI_WORKSPACE_DIR", "workspace")
        return Path(workspace_path).resolve()
    except Exception:
        # Fallback if Django settings not configured yet
        return Path(os.getenv("AI_WORKSPACE_DIR", "workspace")).resolve()

MAX_LIMIT_FILES = 1000
MAX_READ_BYTES = 500_000

ALLOWED_PREFIX = (
    "python","python3", "pip", "pip3","django-admin",
    "pytest","ruff","black","mypy",
    "node","npm", "yarn", "bun", "pnpm",
    "git", 
)

BLOCKED_TOKENS = ("&&", "||", ";", ">", "<", "|", "&", "`", "$(", "${")


def _get_configurable(config: RunnableConfig) -> Dict[str, Any]:
    configurable = (config or {}).get("configurable") or (config or {}).get("metadata") or {}
    return dict(configurable)

def _require(config: RunnableConfig, key:str) -> str:
    
    cfg = _get_configurable(config)
    val = cfg.get(key)
    if val is None or str(val).strip() == "":
        raise Exception(f"Missing '{key}' in config.configurable/metadata")
    return str(val)

def _normalize_id(x: str) -> str:
    # Safe folder names only
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(x)).strip("_")
    if not safe:
        raise Exception("Invalid id.")
    return safe

def _workspace_root(user_id: str, project_id: str) -> Path:
    u = _normalize_id(user_id)
    p = _normalize_id(project_id)
    workspace_dir = _get_workspace_dir()
    root = (workspace_dir / u / p).resolve()
    # Ensure under base dir
    if workspace_dir not in root.parents and root != workspace_dir:
        raise Exception("Workspace path escaped base directory.")
    return root

def _safe_path(user_id: str, project_id: str, rel_path: str) -> Path:
    rel_path = (rel_path or "").strip()
    if rel_path == "":
        rel_path = "."
    # Block absolute paths / Windows drives
    if rel_path.startswith(("/", "\\")) or ":" in rel_path:
        raise Exception("Absolute paths are not allowed.")
    root = _workspace_root(user_id, project_id)
    target = (root / rel_path).resolve()
    # Block traversal / symlink escape
    if root != target and root not in target.parents:
        raise Exception("Path escapes workspace.")
    return target

def _check_perms(user_id: str, action: str, resource: str) -> None:
    """
    Permit-style permission check (same pattern as your prototype).
    If you don't use Permit, USE_PERMIT=False and this becomes a no-op.
    """
    if not USE_PERMIT:
        return
    ok = async_to_sync(permit.check)(f"{user_id}", action, resource)
    if not ok:
        raise Exception(f"You do not have permission to {action} {resource}.")

def _is_command_allowed(command: str) -> None:
    cmd = (command or "").strip()
    if not cmd:
        raise Exception("Command is empty.")

    for tok in BLOCKED_TOKENS:
        if tok in cmd:
            raise Exception(f"Blocked shell token detected: {tok}")

    parts = shlex.split(cmd)
    if not parts:
        raise Exception("Invalid command.")

    if parts[0] not in ALLOWED_PREFIX:
        raise Exception(f"Command not allowed: `{parts[0]}`. Add to allowlist if you trust it.")


def _run_subprocess(cmd: str, cwd: Path, timeout: int = 120, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    _is_command_allowed(cmd)
    start = time.time()
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})

    try:
        proc = subprocess.run(
            shlex.split(cmd),
            cwd=str(cwd),
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "command": cmd,
            "cwd": str(cwd),
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-200_000:],  # cap output
            "stderr": proc.stderr[-200_000:],
            "duration_s": round(time.time() - start, 3),
        }
    except subprocess.TimeoutExpired as e:
        return {
            "command": cmd,
            "cwd": str(cwd),
            "exit_code": -1,
            "stdout": (e.stdout or "")[-200_000:],
            "stderr": (e.stderr or "Command timed out")[-200_000:],
            "duration_s": round(time.time() - start, 3),
            "timed_out": True,
        }

def _lock_file_path(user_id: str, project_id: str) -> Path:
    root = _workspace_root(user_id, project_id)
    return root / ".cursor.lock"




# -----------------------------
# 1) create_workspace
# -----------------------------
@tool
def create_workspace(project_id: str, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Create workspace directory for (user_id, project_id).

    Requires:
    - config.configurable.user_id
    """
    user_id = _require(config, "user_id")
    _check_perms(user_id, "create", "workspace")

    root = _workspace_root(user_id, project_id)
    root.mkdir(parents=True, exist_ok=True)
    return {"workspace_path": str(root)}


# -----------------------------
# 2) list_files
# -----------------------------
@tool
def list_files(path: str = ".", max_depth: int = 4, limit: int = 200, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    List files under workspace/project, relative to `path`.
    Returns relative paths.

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "read", "workspace")

    if limit > MAX_LIMIT_FILES:
        limit = MAX_LIMIT_FILES
    if max_depth < 0:
        max_depth = 0

    root = _workspace_root(user_id, project_id)
    base = _safe_path(user_id, project_id, path)
    if not base.exists():
        raise Exception("Path not found.")

    results: List[str] = []
    base_rel = base.relative_to(root)

    if base.is_file():
        return {"files": [str(base_rel)]}

    # Walk with depth
    for p in base.rglob("*"):
        if len(results) >= limit:
            break
        try:
            rel = p.relative_to(base)
        except Exception:
            continue
        # depth check
        if len(rel.parts) > max_depth:
            continue
        if p.is_dir():
            continue
        results.append(str((base_rel / rel).as_posix()))

    return {"files": results, "count": len(results)}



# -----------------------------
# 3) read_file
# -----------------------------
@tool
def read_file(file_path: str, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Read a file content within workspace.

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "read", "workspace")

    p = _safe_path(user_id, project_id, file_path)
    if not p.exists() or not p.is_file():
        raise Exception("File not found.")

    if p.stat().st_size > MAX_READ_BYTES:
        raise Exception(f"File too large to read (> {MAX_READ_BYTES} bytes).")

    content = p.read_text(encoding="utf-8", errors="replace")
    return {"path": file_path, "content": content}




# -----------------------------
# 5) write_file
# -----------------------------
@tool
def write_file(file_path: str, content: str, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Create/overwrite a file within workspace.

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "write", "workspace")

    p = _safe_path(user_id, project_id, file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content or "", encoding="utf-8")
    return {"path": file_path, "bytes_written": len((content or "").encode("utf-8"))}


# -----------------------------
# 6) apply_patch (unified diff)
# -----------------------------
@tool
def apply_patch(file_path: str, unified_diff: str, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Apply a unified diff patch to a file in workspace.
    Uses system `patch` command (macOS/Linux).

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "write", "workspace")

    if not unified_diff or not unified_diff.strip():
        raise Exception("unified_diff is empty.")

    root = _workspace_root(user_id, project_id)
    target = _safe_path(user_id, project_id, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        # Create empty file so patch can apply additions
        target.write_text("", encoding="utf-8")

    # `patch` wants paths; easiest is run in root and patch relative path
    rel = target.relative_to(root).as_posix()

    # Write diff to temp file
    with subprocess.Popen(
        ["patch", "-p0", "--forward", "--batch"],
        cwd=str(root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        # Many diffs include --- a/path +++ b/path; ensure file matches our rel
        # We keep it simple: user should generate correct unified diff for that file.
        out, err = proc.communicate(unified_diff)

    exit_code = proc.returncode if proc.returncode is not None else -1
    if exit_code != 0:
        raise Exception(f"Patch failed (exit {exit_code}). stderr:\n{err}\nstdout:\n{out}")

    new_content = target.read_text(encoding="utf-8", errors="replace")
    return {"path": rel, "applied": True, "new_content_preview": new_content[:1000]}


# -----------------------------
# 7) run_command
# -----------------------------
@tool
def run_command(command: str, timeout: int = 120, cwd: str = ".", config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Run an allowlisted command inside the workspace.

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "execute", "workspace")

    root = _workspace_root(user_id, project_id)
    workdir = _safe_path(user_id, project_id, cwd)
    if not workdir.exists() or not workdir.is_dir():
        raise Exception("cwd does not exist or is not a directory.")

    result = _run_subprocess(command, cwd=workdir, timeout=int(timeout))
    return result


# -----------------------------
# 8) git_diff
# -----------------------------
@tool
def git_diff(staged: bool = False, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Get git diff for current workspace repo.

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "read", "workspace")

    root = _workspace_root(user_id, project_id)
    args = "git diff --staged" if staged else "git diff"
    result = _run_subprocess(args, cwd=root, timeout=60)
    return {"diff": result.get("stdout", ""), "stderr": result.get("stderr", ""), "exit_code": result.get("exit_code")}


# -----------------------------
# 9) git_commit
# -----------------------------
@tool
def git_commit(message: str, add_all: bool = True, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Commit changes in workspace git repo.
    By default runs `git add -A` then `git commit -m ...`

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "write", "workspace")

    if not message or not message.strip():
        raise Exception("Commit message is required.")

    root = _workspace_root(user_id, project_id)

    if add_all:
        add_res = _run_subprocess("git add -A", cwd=root, timeout=60)
        if add_res["exit_code"] != 0:
            raise Exception(f"git add failed:\n{add_res['stderr']}")

    commit_res = _run_subprocess(f'git commit -m {shlex.quote(message)}', cwd=root, timeout=60)
    # git returns non-zero if nothing to commit
    return {
        "exit_code": commit_res.get("exit_code"),
        "stdout": commit_res.get("stdout", ""),
        "stderr": commit_res.get("stderr", ""),
    }


# -----------------------------
# 10) acquire/release_project_lock
# -----------------------------
@tool
def acquire_project_lock(config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Acquire a simple file-based lock for a (user_id, project_id).
    Prevents 2 runs from editing same workspace simultaneously.

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    - config.configurable.run_id (recommended)
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    run_id = _require(config, "run_id")
    _check_perms(user_id, "write", "workspace")

    root = _workspace_root(user_id, project_id)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_file_path(user_id, project_id)

    if lock_path.exists():
        existing = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
        raise Exception(f"Project is locked. locked_by_run_id={existing or 'unknown'}")

    lock_path.write_text(run_id, encoding="utf-8")
    return {"locked": True, "lock_path": str(lock_path), "run_id": run_id}


@tool
def release_project_lock(config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Release file-based lock for a (user_id, project_id).
    Only the same run_id can release (basic safety).

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    - config.configurable.run_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    run_id = _require(config, "run_id")
    _check_perms(user_id, "write", "workspace")

    lock_path = _lock_file_path(user_id, project_id)
    if not lock_path.exists():
        return {"locked": False, "message": "No lock present."}

    existing = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
    if existing and existing != run_id:
        raise Exception(f"Cannot release lock owned by another run. locked_by_run_id={existing}")

    lock_path.unlink(missing_ok=True)
    return {"locked": False, "message": "Lock released."}

# -----------------------------
# A) file_exists  ✅ (very important)
# -----------------------------
@tool
def file_exists(file_path: str, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Check if a file or directory exists inside workspace.

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "read", "workspace")

    p = _safe_path(user_id, project_id, file_path)
    return {
        "path": file_path,
        "exists": p.exists(),
        "is_file": p.is_file() if p.exists() else False,
        "is_dir": p.is_dir() if p.exists() else False,
    }


# -----------------------------
# B) make_dir ✅
# -----------------------------
@tool
def make_dir(dir_path: str, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Create a directory inside workspace (mkdir -p).

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "write", "workspace")

    p = _safe_path(user_id, project_id, dir_path)
    p.mkdir(parents=True, exist_ok=True)
    return {"created": True, "dir": dir_path}


# -----------------------------
# C) git_init ✅ (so git_diff/git_commit won't fail)
# -----------------------------
@tool
def git_init(config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Initialize a git repo in the workspace if not already initialized.

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "write", "workspace")

    root = _workspace_root(user_id, project_id)
    root.mkdir(parents=True, exist_ok=True)

    git_dir = root / ".git"
    if git_dir.exists():
        return {"initialized": True, "already": True}

    res = _run_subprocess("git init", cwd=root, timeout=60)
    if res["exit_code"] != 0:
        raise Exception(f"git init failed: {res['stderr']}")
    return {"initialized": True, "already": False, "stdout": res["stdout"]}


# -----------------------------
# D) git_status ✅ (agent needs to know what's changed)
# -----------------------------
@tool
def git_status(config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Get git status (porcelain + normal) in workspace repo.

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "read", "workspace")

    root = _workspace_root(user_id, project_id)

    res1 = _run_subprocess("git status --porcelain", cwd=root, timeout=60)
    res2 = _run_subprocess("git status", cwd=root, timeout=60)

    return {
        "porcelain": res1.get("stdout", ""),
        "status": res2.get("stdout", ""),
        "exit_code": res2.get("exit_code", 0),
        "stderr": res2.get("stderr", ""),
    }


# -----------------------------
# E) Atomic lock (REPLACE your lock functions with this) ✅✅
# -----------------------------
def _atomic_create_lockfile(lock_path: Path, content: str) -> None:
    """
    Create lock file atomically using O_EXCL so two runs can't create simultaneously.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    # 0o644 permissions
    fd = os.open(str(lock_path), flags, 0o644)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)


@tool
def acquire_project_lock_atomic(config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Atomic lock acquisition (fixes race condition).

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    - config.configurable.run_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    run_id = _require(config, "run_id")
    _check_perms(user_id, "write", "workspace")

    root = _workspace_root(user_id, project_id)
    root.mkdir(parents=True, exist_ok=True)

    lock_path = _lock_file_path(user_id, project_id)

    try:
        _atomic_create_lockfile(lock_path, run_id)
    except FileExistsError:
        existing = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
        raise Exception(f"Project is locked. locked_by_run_id={existing or 'unknown'}")

    return {"locked": True, "lock_path": str(lock_path), "run_id": run_id}


@tool
def release_project_lock_atomic(config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    Atomic lock release (only same run_id can release).

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    - config.configurable.run_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    run_id = _require(config, "run_id")
    _check_perms(user_id, "write", "workspace")

    lock_path = _lock_file_path(user_id, project_id)
    if not lock_path.exists():
        return {"locked": False, "message": "No lock present."}

    existing = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
    if existing and existing != run_id:
        raise Exception(f"Cannot release lock owned by another run. locked_by_run_id={existing}")

    lock_path.unlink(missing_ok=True)
    return {"locked": False, "message": "Lock released."}

@tool
def search_text(
    query: str,
    glob: str = "**/*",
    limit: int = 50,
    config: RunnableConfig = {},
) -> Dict[str, Any]:
    """
    Search plain text occurrences in files in the workspace.

    Improvements:
    - skips common noisy folders (.git, node_modules, venv, __pycache__, dist, build)
    - skips large files
    - skips likely-binary files by extension
    - returns {path, line, snippet}

    Requires:
    - config.configurable.user_id
    - config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "read", "workspace")

    if not query or not query.strip():
        raise Exception("Query is empty.")

    if limit > 500:
        limit = 500

    root = _workspace_root(user_id, project_id)

    IGNORE_DIRS = {
        ".git", ".hg", ".svn",
        "node_modules",
        ".venv", "venv", "env",
        "__pycache__",
        "dist", "build",
        ".mypy_cache", ".pytest_cache",
        ".ruff_cache",
        ".idea", ".vscode",
    }

    # likely-binary or noisy extensions
    IGNORE_EXTS = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
        ".pdf", ".zip", ".gz", ".tar", ".tgz", ".7z",
        ".mp4", ".mov", ".mp3", ".wav",
        ".sqlite3", ".db",
        ".pyc", ".pyo", ".so", ".dylib",
        ".woff", ".woff2", ".ttf", ".eot",
    }

    MAX_FILE_BYTES = 2_000_000  # 2MB safety

    hits: List[Dict[str, Any]] = []

    def _should_ignore(path: Path) -> bool:
        # skip if inside ignored dir
        parts = set(path.relative_to(root).parts)
        if parts & IGNORE_DIRS:
            return True

        if path.suffix.lower() in IGNORE_EXTS:
            return True

        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                return True
        except Exception:
            return True

        return False

    # Walk candidates
    for fp in root.glob(glob):
        if len(hits) >= limit:
            break
        if not fp.is_file():
            continue
        if _should_ignore(fp):
            continue

        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # Find occurrences line-by-line
        for line_no, line in enumerate(text.splitlines(), start=1):
            if query in line:
                hits.append(
                    {
                        "path": str(fp.relative_to(root).as_posix()),
                        "line": line_no,
                        "snippet": line.strip()[:300],
                    }
                )
                if len(hits) >= limit:
                    break

    return {"query": query, "matches": hits, "count": len(hits)}



# -----------------------------
# Export list (like your document_tools)
# -----------------------------
cursor_tools = [
    create_workspace,
    list_files,
    read_file,
    write_file,
    apply_patch,
    run_command,
    git_diff,
    git_commit,
    acquire_project_lock,
    release_project_lock,
    file_exists,
    make_dir,
    git_init,
    git_status,
    acquire_project_lock_atomic,
    release_project_lock_atomic,
    search_text,
]

