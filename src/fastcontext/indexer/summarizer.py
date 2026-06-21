"""Generates token-efficient summaries for files and directories using LLM."""

import hashlib
import os
import re
import subprocess
from pathlib import Path

from fastcontext.agent.llm import LLM, Message


SMALL_FILE_LINES = 100
UBER_LARGE_FILE_LINES = 2000
DIFF_LINE_THRESHOLD = 20  # max diff lines for partial update

# Chunking config — env overridable
_CHUNK_SIZE_TOKENS = int(os.getenv("FASTCONTEXT_CHUNK_SIZE", "3000"))
_CHUNK_OVERLAP_TOKENS = int(os.getenv("FASTCONTEXT_CHUNK_OVERLAP", "200"))
# Rough token estimate: ~4 chars/token for code
_CHARS_PER_TOKEN = 4


def _get_llm() -> LLM:
    return LLM(
        model=os.getenv("MODEL"),
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("BASE_URL"),
    )


def _est_tokens(text: str) -> int:
    """Rough token estimate for code: ~4 chars per token."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _find_chunk_split(lines: list[str], search_start: int, search_end: int) -> int | None:
    """Find best line to split at between search_start (inclusive) and search_end (exclusive).

    Prefers boundaries that keep logical units together.
    Returns the line index where the NEXT chunk should start, or None to split at search_end.
    """
    # Priority 1: Split BEFORE function/class/async def definitions
    for i in range(search_end - 1, search_start - 1, -1):
        stripped = lines[i].strip()
        if re.match(r"^(async\s+)?(def |class )", stripped):
            return i
    # Priority 2: Split BEFORE decorators
    for i in range(search_end - 1, search_start - 1, -1):
        if lines[i].strip().startswith("@"):
            return i
    # Priority 3: Split AFTER blank lines (paragraph boundaries)
    for i in range(search_end - 1, search_start - 1, -1):
        if not lines[i].strip():
            return i + 1
    # Priority 4: Split BEFORE comment blocks
    for i in range(search_end - 1, search_start - 1, -1):
        stripped = lines[i].strip()
        if stripped.startswith(("#", "//", "/*")):
            return i
    return None  # split at search_end (approximate boundary)


def chunk_text(text: str, rel_path: str, chunk_tokens: int | None = None, overlap_tokens: int | None = None) -> str:
    """Split file text into overlapping chunks at logical boundaries.

    Returns formatted output with all chunks, or raw code if file fits in one chunk.
    """
    chunk_tokens = chunk_tokens or _CHUNK_SIZE_TOKENS
    overlap_tokens = overlap_tokens or _CHUNK_OVERLAP_TOKENS

    lines = text.splitlines(keepends=True)
    # token estimate per line
    line_tokens = [max(1, len(l) // _CHARS_PER_TOKEN) for l in lines]
    total_tokens = sum(line_tokens)

    # Fits in one chunk — return raw
    if total_tokens <= chunk_tokens:
        return f"```{rel_path}\n{text}```"

    chunks_out: list[str] = []
    start_line = 0
    chunk_idx = 0

    while start_line < len(lines):
        chunk_idx += 1
        # Accumulate tokens for this chunk
        end_line = start_line
        accum = 0
        while end_line < len(lines) and accum < chunk_tokens:
            accum += line_tokens[end_line]
            end_line += 1

        # Find a better split point near the boundary
        if end_line < len(lines):
            search_start = max(start_line, end_line - min(30, max(1, end_line - start_line) // 4))
            best = _find_chunk_split(lines, search_start, end_line)
            if best is not None:
                end_line = best

        # Build chunk text
        chunk_str = "".join(lines[start_line:end_line])
        header = f"FILE: {rel_path}  (chunk {chunk_idx}, lines {start_line+1}-{end_line})"
        if overlap_tokens > 0 and start_line > 0:
            # Calculate overlap line count for annotation
            overlap_tok = 0
            ol_lines = 0
            for i in range(start_line, min(start_line + 50, len(lines))):
                overlap_tok += line_tokens[i]
                ol_lines += 1
                if overlap_tok >= overlap_tokens:
                    break
            header += f"  [overlap ~{ol_lines} lines with previous chunk]"
        chunks_out.append(f"{header}\n```{rel_path}:{start_line+1}-{end_line}\n{chunk_str}```")

        # Next chunk start: step back by overlap
        if end_line >= len(lines):
            break
        overlap_tok_needed = overlap_tokens
        ol_start = end_line - 1
        while ol_start > start_line and overlap_tok_needed > 0:
            overlap_tok_needed -= line_tokens[ol_start]
            ol_start -= 1
        start_line = max(start_line + 1, ol_start + 1)

    return "\n\n".join(chunks_out)


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

    Small files (<= SMALL_FILE_LINES) → raw code.
    Larger files → chunked at logical boundaries (no LLM).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # Small file: return raw
    if len(lines) <= SMALL_FILE_LINES:
        return f"```{rel_path}\n{text}```"

    # Use chunking instead of LLM summarization — avoids hallucination
    return chunk_text(text, rel_path)


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
