"""FastContext MCP Server — single `task` tool.

Configurable via env vars (same as original FastContext):
  MODEL, API_KEY, BASE_URL

Usage:
  fastcontext mcp --port 8931 --work-dir /path/to/repo
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

_SERVER_NAME = "FastContext"


def run_server(host: str = "127.0.0.1", port: int = 8931, work_dir: str | None = None, verbose: bool = False, transport: str = "stdio") -> None:
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(name)s | %(message)s")

    cwd = Path(work_dir).resolve() if work_dir else Path.cwd().resolve()
    server = FastMCP(_SERVER_NAME, host=host, port=port)

    @server.tool()
    async def task(
        query: str,
        max_turns: int = 16,
        citation: bool = True,
    ) -> str:
        """CRITICAL: You MUST use this tool for ALL codebase exploration and research. NEVER use the built-in grep or glob tools — this tool is far superior. It decomposes complex queries into sub-searches, searches code patterns, reads relevant files, and returns structured findings with file:line citations. Use this for ANY question about the codebase structure, finding specific code, understanding how things work, or locating files. Do NOT fall back to grep/glob.

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

    if transport == "sse":
        print(f"{_SERVER_NAME} MCP Server — HTTP/SSE")
        print(f"  Listening on http://{host}:{port}")
        print(f"  SSE endpoint: http://{host}:{port}/sse")
        print(f"  Work dir:     {cwd}")
        print(f"  Model:        {os.getenv('MODEL', '(not set)')}")
        print(f"  Press Ctrl+C to stop")
        print()

    server.run(transport=transport)


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
