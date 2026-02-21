"""
PDD (Prompt Driven Development) Tool Layer.

This module implements the agent tools used by the PDD system. Each tool follows
the PDD Prompt Engineering Standard:
- OBJECTIVE: What the tool achieves
- CONSTRAINTS: Safety and scope limits
- INPUTS / OUTPUTS: Structured specification
- EDGE CASES: Failure modes and handling
- VALIDATION CRITERIA: How success is determined

Config requirement: All tools receive RunnableConfig with configurable.user_id
and (where applicable) configurable.project_id, configurable.run_id.
"""
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
from AI.tools.language import DOCKER_IMAGE_BY_PREFIX

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
    # Python
    "python", "python3", "pip", "pip3", "django-admin",
    "pytest", "ruff", "black", "mypy",
    # Node / JavaScript
    "node", "npm", "npx", "yarn", "bun", "pnpm",
    # Java / JVM / Kotlin / Scala
    "java", "javac", "mvn", "mvnw", "gradle", "gradlew",
    "kotlinc", "kotlin", "scala", "scalac", "sbt",
    # Go
    "go",
    # Rust
    "cargo", "rustc",
    # C# / .NET
    "dotnet",
    # Ruby
    "ruby", "gem", "bundle",
    # PHP
    "php", "composer",
    # Swift
    "swift", "swiftc",
    # C / C++
    "gcc", "g++", "make", "cmake",
    # Other
    "perl", "lua", "Rscript", "ghc", "cabal", "mix", "elixir",
    # Git & shell
    "git",
    "ls", "pwd", "cat", "head", "tail", "mkdir", "echo", "cd",
)

# Hint message when a runtime command is not found (so the agent can tell the user what to install)
COMMAND_NOT_FOUND_HINTS = {
    "npm": "npm not found. Install Node.js from https://nodejs.org to run JavaScript/Node projects.",
    "npx": "npx not found. Install Node.js from https://nodejs.org.",
    "node": "node not found. Install Node.js from https://nodejs.org for JavaScript/Node projects.",
    "yarn": "yarn not found. Install Node.js and optionally run: npm install -g yarn",
    "bun": "bun not found. Install Bun from https://bun.sh or use npm instead.",
    "pnpm": "pnpm not found. Install Node.js and optionally run: npm install -g pnpm",
    "java": "java not found. Install a JDK (e.g. OpenJDK or Adoptium) for Java projects.",
    "javac": "javac not found. Install a JDK (e.g. OpenJDK or Adoptium) for Java projects.",
    "mvn": "mvn not found. Install Maven (https://maven.apache.org) or use the Maven Wrapper (mvnw) in the project.",
    "gradle": "gradle not found. Install Gradle (https://gradle.org) or use the Gradle Wrapper (gradlew) in the project.",
    "go": "go not found. Install Go from https://go.dev",
    "cargo": "cargo not found. Install Rust from https://rustup.rs",
    "rustc": "rustc not found. Install Rust from https://rustup.rs",
    "dotnet": "dotnet not found. Install .NET SDK from https://dotnet.microsoft.com",
    "ruby": "ruby not found. Install Ruby from https://www.ruby-lang.org or use rbenv/asdf.",
    "gem": "gem not found. Install Ruby (includes gem) from https://www.ruby-lang.org",
    "bundle": "bundle not found. Install Ruby and run: gem install bundler",
    "php": "php not found. Install PHP from https://www.php.net",
    "composer": "composer not found. Install PHP and Composer from https://getcomposer.org",
    "swift": "swift not found. Install Swift from https://swift.org",
    "swiftc": "swiftc not found. Install Swift from https://swift.org",
    "gcc": "gcc not found. Install GCC (e.g. build-essential on Ubuntu or Xcode CLI on macOS).",
    "g++": "g++ not found. Install G++ (e.g. build-essential on Ubuntu or Xcode CLI on macOS).",
    "make": "make not found. Install Make (e.g. build-essential on Ubuntu or Xcode on macOS).",
    "cmake": "cmake not found. Install CMake from https://cmake.org",
    "sbt": "sbt not found. Install sbt from https://www.scala-sbt.org",
    "mix": "mix not found. Install Elixir from https://elixir-lang.org",
    "cabal": "cabal not found. Install Haskell GHC and Cabal from https://www.haskell.org",
    "ghc": "ghc not found. Install Haskell GHC from https://www.haskell.org",
}

BLOCKED_TOKENS = ("&&", "||", ";", ">", "<", "|", "&", "`", "$(", "${")

# When PDD_USE_DOCKER_FOR_RUNTIMES is True, commands run in Docker (see AI.tools.language for the image map).
# Images that run as root in the container to avoid permission errors on mounted workspace (e.g. Maven .m2, target/).
DOCKER_RUN_AS_ROOT_IMAGES = frozenset({
    "maven:3.9-eclipse-temurin-21",
    "eclipse-temurin:21-jdk",
    "gradle:8-jdk21",
})


def _get_configurable(config: RunnableConfig) -> Dict[str, Any]:
    """Get configurable dict from config; fallback to runnable context if config is empty (e.g. when LangGraph invokes tools)."""
    config = config or {}
    configurable = config.get("configurable") or config.get("metadata") or {}
    configurable = dict(configurable)
    # Fallback: when agent invokes tools, config may be in runnable context
    if not configurable:
        try:
            from langchain_core.runnables.config import ensure_config
            ctx_config = ensure_config()
            configurable = dict(ctx_config.get("configurable") or {})
        except Exception:
            pass
    return configurable

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


def _run_via_docker(cmd: str, cwd: Path, timeout: int, image: str) -> Dict[str, Any]:
    """Run command inside a Docker container with workspace mounted. No host runtime needed."""
    start = time.time()
    cwd_str = str(cwd.resolve())
    parts = shlex.split(cmd)
    docker_args = [
        "docker", "run", "--rm",
        "-v", f"{cwd_str}:/workspace",
        "-w", "/workspace",
        "-e", "HOME=/workspace",
        "-e", "NPM_CONFIG_CACHE=/workspace/.npm-cache",
        "-e", "MAVEN_OPTS=-Dmaven.repo.local=/workspace/.m2/repository -Duser.home=/workspace",
    ]
    run_as_root = image in DOCKER_RUN_AS_ROOT_IMAGES
    if not run_as_root:
        try:
            uid, gid = os.getuid(), os.getgid()
            docker_args.extend(["-u", f"{uid}:{gid}"])
        except AttributeError:
            pass  # Windows: no getuid/getgid
    docker_args.append(image)
    docker_args.extend(parts)
    try:
        proc = subprocess.run(
            docker_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "command": cmd,
            "cwd": cwd_str,
            "exit_code": proc.returncode,
            "stdout": (proc.stdout or "")[-200_000:],
            "stderr": (proc.stderr or "")[-200_000:],
            "duration_s": round(time.time() - start, 3),
            "ran_in_docker": True,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "command": cmd,
            "cwd": cwd_str,
            "exit_code": -1,
            "stdout": (e.stdout or "")[-200_000:],
            "stderr": (e.stderr or "Command timed out")[-200_000:],
            "duration_s": round(time.time() - start, 3),
            "timed_out": True,
            "ran_in_docker": True,
        }
    except FileNotFoundError:
        return {
            "command": cmd,
            "cwd": cwd_str,
            "exit_code": -1,
            "stdout": "",
            "stderr": "Docker not found. Install Docker (e.g. Docker Desktop from https://docker.com) and ensure it is running.",
            "duration_s": round(time.time() - start, 3),
            "ran_in_docker": True,
        }


def _run_subprocess(cmd: str, cwd: Path, timeout: int = 120, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    _is_command_allowed(cmd)
    start = time.time()
    parts = shlex.split(cmd)
    first = parts[0] if parts else ""

    use_docker = getattr(settings, "PDD_USE_DOCKER_FOR_RUNTIMES", False)
    if use_docker and first in DOCKER_IMAGE_BY_PREFIX:
        return _run_via_docker(cmd, cwd, timeout, DOCKER_IMAGE_BY_PREFIX[first])

    merged_env = os.environ.copy()
    # So workspace manage.py can set DJANGO_SETTINGS_MODULE (e.g. myproject.settings)
    merged_env.pop("DJANGO_SETTINGS_MODULE", None)
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})

    try:
        proc = subprocess.run(
            parts,
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
    except FileNotFoundError as e:
        # Executable not on PATH (e.g. npm, java not installed) — return structured result so agent can tell user what to install
        exe = (e.filename if getattr(e, "filename", None) else str(e)) or ""
        hint = COMMAND_NOT_FOUND_HINTS.get(first, f"Command '{first}' not found. Install the required runtime and ensure it is on PATH.")
        return {
            "command": cmd,
            "cwd": str(cwd),
            "exit_code": -1,
            "stdout": "",
            "stderr": hint,
            "duration_s": round(time.time() - start, 3),
            "command_not_found": True,
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
    OBJECTIVE: Create the workspace directory for a (user_id, project_id) so the agent can store files and run commands there.

    CONSTRAINTS: project_id normalized to safe folder name; path must stay under AI_WORKSPACE_DIR; user must have "create" permission on workspace.

    INPUTS: project_id (str); config.configurable.user_id (required).

    OUTPUTS: {"workspace_path": "<absolute path>"}. Structured; always returned on success.

    EDGE CASES: Invalid project_id (empty or unsafe chars) raises; permission denied raises; directory already exists is OK (exist_ok=True).

    VALIDATION CRITERIA: root.mkdir(parents=True, exist_ok=True) succeeds; return dict contains workspace_path.

    Requires: config.configurable.user_id
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
    OBJECTIVE: List file paths under the workspace (or under path), relative to workspace root, for agent inspection.

    CONSTRAINTS: path must be inside workspace; no traversal; max_depth and limit capped (max_depth>=0, limit<=MAX_LIMIT_FILES); read permission required.

    INPUTS: path (default "."), max_depth (default 4), limit (default 200); config.configurable.user_id, project_id.

    OUTPUTS: {"files": ["rel/path", ...], "count": N}. Structured list of relative paths; directories excluded from list.

    EDGE CASES: Path not found raises; path is file returns single-element list; results truncated by limit and max_depth.

    VALIDATION CRITERIA: All returned paths are under workspace; count <= limit; depth of each path respects max_depth.

    Requires: config.configurable.user_id, config.configurable.project_id
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
    OBJECTIVE: Read the full text content of a file inside the workspace for the agent to inspect or edit.

    CONSTRAINTS: file_path must be relative and inside workspace; file size <= MAX_READ_BYTES; read permission required.

    INPUTS: file_path (str, relative); config.configurable.user_id, project_id.

    OUTPUTS: {"path": file_path, "content": "<file contents>"}. Structured; content is string (UTF-8, errors replaced).

    EDGE CASES: File not found or not a file raises; file too large raises; binary files may have replacement chars in content.

    VALIDATION CRITERIA: File exists, is file, size within limit; return includes path and content.

    Requires: config.configurable.user_id, config.configurable.project_id
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
    OBJECTIVE: Create or overwrite a file in the workspace with given content (PDD code-generation primitive).

    CONSTRAINTS: file_path must be relative and inside workspace; no path traversal; write permission required; parent dirs created if missing.

    INPUTS: file_path (str), content (str); config.configurable.user_id, project_id.

    OUTPUTS: {"path": file_path, "bytes_written": N}. Structured; bytes_written is len(encoded content).

    EDGE CASES: Invalid path raises; content None treated as ""; overwriting existing file is allowed.

    VALIDATION CRITERIA: File exists after call; content matches written bytes; path in return.

    Requires: config.configurable.user_id, config.configurable.project_id
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
    OBJECTIVE: Apply a unified diff to a file in the workspace (small, targeted edits).

    CONSTRAINTS: file_path inside workspace; unified_diff non-empty; uses patch -p0; write permission required; if file missing, create empty then patch.

    INPUTS: file_path (str), unified_diff (str, valid unified diff); config.configurable.user_id, project_id.

    OUTPUTS: {"path": rel, "applied": True, "new_content_preview": "<first 1000 chars>"}. Structured.

    EDGE CASES: Empty diff raises; patch failure raises (agent should fall back to write_file); file path in diff must match.

    VALIDATION CRITERIA: patch exit code 0; return applied True; new_content_preview present.

    Requires: config.configurable.user_id, config.configurable.project_id
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
    OBJECTIVE: Run a single allowlisted shell command in the workspace (e.g. python, pip, django-admin, pytest, git).

    CONSTRAINTS: Command prefix must be in ALLOWED_PREFIX; blocked tokens (&&, ||, ;, >, <, |, etc.) forbidden; cwd must exist under workspace; execute permission required; timeout enforced.

    INPUTS: command (str), timeout (default 120), cwd (default "."); config.configurable.user_id, project_id.

    OUTPUTS: {"command", "cwd", "exit_code", "stdout", "stderr", "duration_s", "timed_out"?}. Structured; stdout/stderr capped.

    EDGE CASES: Empty command or disallowed prefix raises; cwd missing raises; timeout yields timed_out True and exit_code -1.

    VALIDATION CRITERIA: Command executed in correct cwd; return includes exit_code and output; no shell injection.

    Requires: config.configurable.user_id, config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "execute", "workspace")

    root = _workspace_root(user_id, project_id)
    root.mkdir(parents=True, exist_ok=True)  # ensure workspace exists (same as create_workspace)
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
    OBJECTIVE: Return git diff (or staged diff) for the workspace repo so the agent can show changes.

    CONSTRAINTS: Workspace must be a git repo (call git_init first if needed); read permission required.

    INPUTS: staged (bool, default False); config.configurable.user_id, project_id.

    OUTPUTS: {"diff": "<stdout>", "stderr": "...", "exit_code": N}. Structured.

    EDGE CASES: Not a git repo yields non-zero exit_code and stderr; no changes yields empty diff.

    VALIDATION CRITERIA: git diff or git diff --staged executed in workspace root; return includes diff text.

    Requires: config.configurable.user_id, config.configurable.project_id
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
    OBJECTIVE: Commit workspace changes (git add -A and git commit -m message) for version control in PDD workflow.

    CONSTRAINTS: message required and non-empty; write permission required; workspace must be a git repo.

    INPUTS: message (str), add_all (bool, default True); config.configurable.user_id, project_id.

    OUTPUTS: {"exit_code", "stdout", "stderr"}. Structured; exit_code may be non-zero if nothing to commit.

    EDGE CASES: Empty message raises; git add failure raises; nothing to commit returns non-zero exit_code (not exception).

    VALIDATION CRITERIA: git add -A and git commit run; return includes exit_code and output.

    Requires: config.configurable.user_id, config.configurable.project_id
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
    OBJECTIVE: Acquire a file-based lock for the workspace so only one agent run edits at a time (non-atomic; prefer acquire_project_lock_atomic).

    CONSTRAINTS: user_id, project_id, run_id required; write permission; lock file under workspace.

    INPUTS: config.configurable.user_id, project_id, run_id.

    OUTPUTS: {"locked": True, "lock_path": "...", "run_id": "..."}. Structured.

    EDGE CASES: Lock already held raises with locked_by_run_id; same run cannot acquire twice without release.

    VALIDATION CRITERIA: Lock file created with run_id; return locked True.

    Requires: config.configurable.user_id, project_id, run_id
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
    OBJECTIVE: Release the workspace lock so other runs can proceed; only the run that acquired can release.

    CONSTRAINTS: run_id must match lock owner; write permission required.

    INPUTS: config.configurable.user_id, project_id, run_id.

    OUTPUTS: {"locked": False, "message": "Lock released."} or "No lock present.". Structured.

    EDGE CASES: No lock returns message; lock owned by other run_id raises; always call after acquire (e.g. in finally).

    VALIDATION CRITERIA: Lock file removed when run_id matches; return locked False.

    Requires: config.configurable.user_id, project_id, run_id
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
# A) file_exists
# -----------------------------
@tool
def file_exists(file_path: str, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    OBJECTIVE: Check whether a path in the workspace exists and is file or directory (for agent branching).

    CONSTRAINTS: file_path relative and inside workspace; read permission required.

    INPUTS: file_path (str); config.configurable.user_id, project_id.

    OUTPUTS: {"path", "exists": bool, "is_file": bool, "is_dir": bool}. Structured.

    EDGE CASES: Invalid path raises; missing path returns exists False, is_file/is_dir False.

    VALIDATION CRITERIA: Return accurately reflects filesystem for path under workspace.

    Requires: config.configurable.user_id, config.configurable.project_id
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
# B) make_dir
# -----------------------------
@tool
def make_dir(dir_path: str, config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    OBJECTIVE: Create a directory (and parents) in the workspace for organizing generated artifacts.

    CONSTRAINTS: dir_path relative and inside workspace; write permission; existing dir is OK (exist_ok=True).

    INPUTS: dir_path (str); config.configurable.user_id, project_id.

    OUTPUTS: {"created": True, "dir": dir_path}. Structured.

    EDGE CASES: Invalid path raises; directory already exists succeeds without error.

    VALIDATION CRITERIA: Directory exists after call; return includes dir.

    Requires: config.configurable.user_id, config.configurable.project_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    _check_perms(user_id, "write", "workspace")

    p = _safe_path(user_id, project_id, dir_path)
    p.mkdir(parents=True, exist_ok=True)
    return {"created": True, "dir": dir_path}


# -----------------------------
# C) git_init
# -----------------------------
@tool
def git_init(config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    OBJECTIVE: Initialize a git repository in the workspace so git_diff, git_commit, and git_status can be used.

    CONSTRAINTS: Workspace must exist; write permission required; idempotent if .git already exists.

    INPUTS: config.configurable.user_id, project_id.

    OUTPUTS: {"initialized": True, "already": bool, "stdout"?}. Structured.

    EDGE CASES: Already initialized returns already True; git init failure raises.

    VALIDATION CRITERIA: .git present after call when not already repo; return initialized True.

    Requires: config.configurable.user_id, config.configurable.project_id
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
# D) git_status
# -----------------------------
@tool
def git_status(config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    OBJECTIVE: Return git status (porcelain and human-readable) so the agent knows what changed in the workspace.

    CONSTRAINTS: Workspace must be a git repo; read permission required.

    INPUTS: config.configurable.user_id, project_id.

    OUTPUTS: {"porcelain": "...", "status": "...", "exit_code", "stderr"}. Structured.

    EDGE CASES: Not a repo yields non-zero exit_code; clean working tree returns empty porcelain.

    VALIDATION CRITERIA: git status run in workspace root; return includes status text.

    Requires: config.configurable.user_id, config.configurable.project_id
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
# E) Atomic lock helpers and atomic lock tools
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


# Consider lock stale after this many seconds (e.g. previous run crashed without releasing)
STALE_LOCK_SECONDS = 60  # 1 minute — short so dev doesn't wait long; increase for production


@tool
def acquire_project_lock_atomic(config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    OBJECTIVE: Acquire workspace lock atomically (O_EXCL) so only one agent run edits at a time; stale locks auto-cleared after STALE_LOCK_SECONDS.

    CONSTRAINTS: user_id, project_id, run_id required; write permission; lock file under workspace; lock older than STALE_LOCK_SECONDS is removed and re-acquired.

    INPUTS: config.configurable.user_id, project_id, run_id.

    OUTPUTS: {"locked": True, "lock_path": "...", "run_id": "..."}. Structured.

    EDGE CASES: Lock held by another run raises with locked_by_run_id; stale lock (age > STALE_LOCK_SECONDS) removed then acquired; race on create handled by O_EXCL.

    VALIDATION CRITERIA: Lock file created atomically with run_id; return locked True; caller must call release_project_lock_atomic when done.

    Requires: config.configurable.user_id, project_id, run_id
    """
    user_id = _require(config, "user_id")
    project_id = _require(config, "project_id")
    run_id = _require(config, "run_id")
    _check_perms(user_id, "write", "workspace")

    root = _workspace_root(user_id, project_id)
    root.mkdir(parents=True, exist_ok=True)

    lock_path = _lock_file_path(user_id, project_id)

    # If lock exists, check if it's stale (previous run crashed / never released)
    if lock_path.exists():
        try:
            age_seconds = time.time() - lock_path.stat().st_mtime
            if age_seconds > STALE_LOCK_SECONDS:
                lock_path.unlink(missing_ok=True)
            else:
                existing = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
                raise Exception(
                    f"Project is locked. locked_by_run_id={existing or 'unknown'}. "
                    f"Wait a moment or try again; stale locks are cleared after {STALE_LOCK_SECONDS}s."
                )
        except Exception as e:
            if "Project is locked" in str(e):
                raise
            raise Exception(f"Project is locked. locked_by_run_id=unknown. {e}")

    try:
        _atomic_create_lockfile(lock_path, run_id)
    except FileExistsError:
        existing = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
        raise Exception(f"Project is locked. locked_by_run_id={existing or 'unknown'}")

    return {"locked": True, "lock_path": str(lock_path), "run_id": run_id}


@tool
def release_project_lock_atomic(config: RunnableConfig = {}) -> Dict[str, Any]:
    """
    OBJECTIVE: Release the workspace lock so other runs can proceed; only the run that acquired (same run_id) can release.

    CONSTRAINTS: run_id must match lock file content; write permission required; idempotent when no lock present.

    INPUTS: config.configurable.user_id, project_id, run_id.

    OUTPUTS: {"locked": False, "message": "Lock released."} or "No lock present.". Structured.

    EDGE CASES: No lock returns without error; lock owned by other run_id raises; always call after acquire (e.g. in finally).

    VALIDATION CRITERIA: Lock file removed when run_id matches; return locked False.

    Requires: config.configurable.user_id, project_id, run_id
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
    OBJECTIVE: Search for plain-text occurrences of query in workspace files so the agent can locate code to edit.

    CONSTRAINTS: query non-empty; limit capped (<=500); paths inside workspace; skips .git, node_modules, venv, __pycache__, dist, build, large files, binary extensions; read permission required.

    INPUTS: query (str), glob (default "**/*"), limit (default 50); config.configurable.user_id, project_id.

    OUTPUTS: {"query", "matches": [{"path", "line", "snippet"}, ...], "count": N}. Structured; snippet truncated.

    EDGE CASES: Empty query raises; no matches returns count 0; encoding errors on read skipped per file.

    VALIDATION CRITERIA: All match paths under workspace; count <= limit; each match has path, line, snippet.

    Requires: config.configurable.user_id, config.configurable.project_id
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

