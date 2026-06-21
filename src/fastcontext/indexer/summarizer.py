"""Generates token-efficient summaries for files and directories using LLM."""

import hashlib
import os
import subprocess
from pathlib import Path

from fastcontext.agent.llm import LLM, Message


SMALL_FILE_LINES = 100
UBER_LARGE_FILE_LINES = 2000
DIFF_LINE_THRESHOLD = 20  # max diff lines for partial update


def _get_llm() -> LLM:
    return LLM(
        model=os.getenv("MODEL"),
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("BASE_URL"),
    )


def _is_binary(path: Path) -> bool:
    """Check if a file is binary by scanning for null bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
            return b"\0" in chunk
    except Exception:
        return True


def _compute_file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()[:16]


def _compute_dir_hash(file_list: list[str]) -> str:
    return hashlib.sha256("".join(sorted(file_list)).encode()).hexdigest()[:16]


def _file_header(path: Path, rel_path: str, lines: list[str]) -> str:
    """Extract key structural info from file without LLM."""
    imports = [l.strip() for l in lines if l.strip().startswith(("import ", "from "))]

    sigs = []
    for l in lines:
        stripped = l.strip()
        if stripped.startswith(("def ", "class ", "async def ")):
            sigs.append(stripped.rstrip(":"))

    parts = [f"FILE: {rel_path}"]
    parts.append(f"LINES: {len(lines)}")
    if imports:
        parts.append(f"IMPORTS: {'; '.join(imports[:10])}")
    if sigs:
        parts.append(f"DEFS: {'; '.join(sigs[:20])}")
    return "\n".join(parts)


def _get_git_diff(path: Path) -> str | None:
    """Get unstaged + staged diff for a file via git.

    Returns the diff text if available and non-empty, None otherwise.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--", str(path)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _diff_change_lines(diff: str) -> int:
    """Count lines that were added or removed (excludes headers)."""
    lines = diff.splitlines()
    return sum(1 for l in lines if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))


_SKIP_DIRS = frozenset({"__pycache__", ".git", ".hg", ".svn", "node_modules", ".venv", "venv", ".fastcontext", "__pycache__", ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


def _dir_max_mtime(path: Path) -> float:
    """Get the latest mtime across all source files in a directory (recursive)."""
    latest = 0.0
    for entry in path.rglob("*"):
        # Skip hidden files/dirs, cache dirs, and anything under them
        if entry.name.startswith("."):
            continue
        # Check if any part of the path is in SKIP_DIRS
        if any(part in _SKIP_DIRS for part in entry.relative_to(path).parts):
            continue
        if entry.is_file():
            try:
                mtime = entry.stat().st_mtime
                if mtime > latest:
                    latest = mtime
            except Exception:
                pass
    return latest


FILE_SUMMARY_SYSTEM_PROMPT = """You are a codebase indexing assistant. Produce an extremely token-efficient summary of the given file for consumption by another coding agent.

Output format (keep it under 10 lines total):
FILE: <relative_path>
PURPOSE: <one line description of what this file does>
KEYS: <key functions/classes and what they do, semicolon separated>
DEPS: <key dependencies>
NOTE: <anything non-obvious>

Be terse. Use abbreviations. Omit obvious things."""


DIR_SUMMARY_SYSTEM_PROMPT = """You are a codebase indexing assistant. Produce an extremely token-efficient structural summary of the given directory for consumption by another coding agent.

Output format (keep it under 15 lines):
DIR: <relative_path>
FILES: <count>
STRUCTURE:
  <filename.ext> — <one-line purpose>
  <subdir/> — <one-line purpose>
KEY: <most architecturally significant files>

Be terse. Omit trivial/obvious entries."""


async def summarize_file(path: Path, rel_path: str) -> str:
    """Generate a token-efficient summary of a file.

    For small files (<= SMALL_FILE_LINES) returns raw code.
    For regular files, uses LLM to create a structural summary.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    # Small file: return raw
    if len(lines) <= SMALL_FILE_LINES:
        return f"```{rel_path}\n" + "\n".join(lines) + "\n```"

    # Uber-large file: return header only
    if len(lines) > UBER_LARGE_FILE_LINES:
        h = _file_header(path, rel_path, lines)
        return h + "\nNOTE: File too large for full summary. Structural header only."

    # Use LLM for summarization
    header = _file_header(path, rel_path, lines)
    content_sample = "\n".join(lines[:200])  # first 200 lines

    llm = _get_llm()
    msgs = [
        Message(role="system", content=FILE_SUMMARY_SYSTEM_PROMPT),
        Message(role="user", content=f"{header}\n\n---\n\n{content_sample}"),
    ]
    result = await llm.acall(msgs, None)
    return result.content


async def summarize_dir(path: Path, rel_path: str) -> str:
    """Generate a token-efficient structural summary of a directory."""
    children = []
    for entry in sorted(path.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            children.append(f"  {entry.name}/")
        elif entry.is_file():
            try:
                size = entry.stat().st_size
                if size > 1024 * 1024:  # > 1MB
                    children.append(f"  {entry.name}  ({size // 1024}KB) [large]")
                else:
                    children.append(f"  {entry.name}")
            except Exception:
                children.append(f"  {entry.name}")

    file_list = "\n".join(children)

    # If very few children, return structure directly without LLM
    if len(children) <= 20 and rel_path != ".":
        return f"DIR: {rel_path}\nFILES: {len(children)}\n{file_list}"

    llm = _get_llm()
    msgs = [
        Message(role="system", content=DIR_SUMMARY_SYSTEM_PROMPT),
        Message(role="user", content=f"DIR: {rel_path}\n\n{file_list}"),
    ]
    result = await llm.acall(msgs, None)
    return result.content
