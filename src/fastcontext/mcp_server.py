"""FastContext MCP Server — exposes `task` + `info` tools over localhost HTTP/SSE.

Configurable via env vars (same as original FastContext):
  MODEL, API_KEY, BASE_URL

Usage:
  fastcontext mcp --port 8931 --work-dir /path/to/repo
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from fastcontext.indexer.db import IndexCache
from fastcontext.agent.llm import LLM, Message
from fastcontext.indexer.summarizer import (
    FILE_SUMMARY_SYSTEM_PROMPT,
    SMALL_FILE_LINES,
    _SKIP_DIRS,
    _compute_file_hash,
    _dir_max_mtime,
    _est_tokens,
    _get_git_diff,
    _diff_change_lines,
    DIFF_LINE_THRESHOLD,
    summarize_dir,
    summarize_file,
)

logger = logging.getLogger(__name__)

_SERVER_NAME = "FastContext"


def _file_list_hash(target: Path) -> str:
    file_list = []
    for p in target.rglob("*"):
        if p.name.startswith("."):
            continue
        rel = p.relative_to(target)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        file_list.append(str(rel))
    file_list.sort()
    return hashlib.sha256("|".join(file_list).encode()).hexdigest()


def run_server(host: str = "127.0.0.1", port: int = 8931, work_dir: str | None = None, verbose: bool = False, transport: str = "stdio") -> None:
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(name)s | %(message)s")

    cwd = Path(work_dir).resolve() if work_dir else Path.cwd().resolve()
    cache_path = cwd / ".fastcontext" / "index_cache.db"
    cache = IndexCache(cache_path)

    server = FastMCP(_SERVER_NAME, host=host, port=port)

    @server.tool()
    async def task(
        query: str,
        max_turns: int = 16,
        citation: bool = True,
    ) -> str:
        """Multi-step codebase exploration & research via sub-agent. Preferred over calling glob/grep/explore directly for any non-trivial question. Decomposes complex queries, searches patterns, reads relevant files, returns structured findings with file:line citations.

        Args:
            query: Natural language question about the codebase.
            max_turns: Max agent exploration turns (default 8).
            citation: If true, returns only the <final_answer> block.
        """
        from fastcontext.agent.agent_factory import make_fastcontext_agent

        traj = f".fastcontext/mcp_trajectory_{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.jsonl"
        agent = make_fastcontext_agent(trajectory_file=traj, work_dir=str(cwd))
        result = await agent.run(prompt=query, max_turns=max_turns, verbose=verbose, citation=citation)
        return result

    @server.tool()
    async def info(path: str) -> str:
        """AI-summarized file/dir overview. Small files: raw code. Large files: LLM-generated summary of purpose, key symbols, deps — not raw chunks. Directories: LLM-summarized structure. Preferred over read for understanding — use read only when exact line-by-line content needed (edits, patching)."""
        target = (cwd / path).resolve()
        if not str(target).startswith(str(cwd)):
            return f"Error: path must be within work directory"
        if not target.exists():
            return f"Error: path not found: {path}"
        if target.is_file():
            return await _handle_file_info(cache, path, target)
        else:
            return await _handle_dir_info(cache, path, target)

    if transport == "sse":
        print(f"{_SERVER_NAME} MCP Server — HTTP/SSE")
        print(f"  Listening on http://{host}:{port}")
        print(f"  SSE endpoint: http://{host}:{port}/sse")
        print(f"  Work dir:     {cwd}")
        print(f"  Model:        {os.getenv('MODEL', '(not set)')}")
        print(f"  Press Ctrl+C to stop")
        print()

    server.run(transport=transport)


_MAX_UPDATE_TOKENS = 20000  # max total tokens for diff-based summary update

_UPDATE_SUMMARY_PROMPT = """You are a codebase indexing assistant. Update the existing file summary to reflect the changes shown in the diff.

Existing summary:
{old_summary}

Diff of changes:
```diff
{diff}
```

Return an updated summary in the same format. If the diff removes or changes existing items, reflect that.
Keep it under 10 lines. Be terse."""


async def _update_summary_with_diff(old_summary: str, diff: str) -> str | None:
    """Use LLM to update a file summary from a diff. Returns None on failure."""
    old_tok = _est_tokens(old_summary)
    diff_tok = _est_tokens(diff)
    total_tok = old_tok + diff_tok + 500  # prompt overhead

    if total_tok > _MAX_UPDATE_TOKENS:
        return None

    llm = LLM(
        model=os.getenv("MODEL"),
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("BASE_URL"),
    )
    try:
        prompt = _UPDATE_SUMMARY_PROMPT.format(old_summary=old_summary, diff=diff)
        msgs = [
            Message(role="system", content=FILE_SUMMARY_SYSTEM_PROMPT),
            Message(role="user", content=prompt),
        ]
        result = await llm.acall(msgs, None)
        return result.content.strip()
    except Exception:
        return None


async def _handle_file_info(cache: IndexCache, path: str, target: Path) -> str:
    stat = target.stat()
    file_hash = _compute_file_hash(target)
    cached = cache.get_file_info(path)
    if cached and cached["file_hash"] == file_hash and cached["file_mtime"] == stat.st_mtime:
        return cached["summary"]
    if cached:
        diff = _get_git_diff(target)
        if diff:
            change_lines = _diff_change_lines(diff)
            if change_lines <= DIFF_LINE_THRESHOLD and change_lines > 0:
                updated = await _update_summary_with_diff(cached["summary"], diff)
                if updated:
                    cache.upsert_file_info(path, updated, file_hash, stat.st_size, stat.st_mtime)
                    return updated
                # LLM update failed or too large — fall through to full re-summarize
    summary = await summarize_file(target, path)
    cache.upsert_file_info(path, summary, file_hash, stat.st_size, stat.st_mtime)
    return summary


async def _handle_dir_info(cache: IndexCache, path: str, target: Path) -> str:
    file_list_hash = _file_list_hash(target)
    max_mtime = _dir_max_mtime(target)
    cached = cache.get_dir_info(path)
    if cached:
        if cached["file_list_hash"] == file_list_hash and cached["max_mtime"] >= max_mtime:
            return cached["summary"]
    summary = await summarize_dir(target, path)
    file_count = len([e for e in target.rglob("*") if e.is_file() and not e.name.startswith(".")])
    cache.upsert_dir_info(path, summary, file_list_hash, file_count, max_mtime)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="FastContext MCP Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", "-p", type=int, default=8931, help="Port (default: 8931)")
    parser.add_argument("--work-dir", "-w", default=None, help="Working directory (default: cwd)")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(name)s | %(message)s",
    )

    run_server(host=args.host, port=args.port, work_dir=args.work_dir, verbose=args.verbose)


if __name__ == "__main__":
    main()
