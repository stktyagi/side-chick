"""FastContext MCP Server — single `task` tool.

Configurable via env vars (same as original FastContext):
  MODEL, API_KEY, BASE_URL, MAX_TOKENS, MAX_TURNS

Usage:
  fastcontext mcp --port 8931 --work-dir /path/to/repo
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from fastcontext.agent.utils import get_final_answer, load_dotenv

logger = logging.getLogger(__name__)

_SERVER_NAME = "FastContext"


def run_server(host: str = "127.0.0.1", port: int = 8931, work_dir: str | None = None, verbose: bool = False, transport: str = "stdio") -> None:
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(name)s | %(message)s")

    cwd = Path(work_dir).resolve() if work_dir else Path.cwd().resolve()
    load_dotenv()
    server = FastMCP(_SERVER_NAME, host=host, port=port)

    max_turns_default = int(os.getenv("MAX_TURNS", "16"))

    @server.tool()
    async def task(
        query: str,
        max_turns: int | None = max_turns_default,
        citation: bool = True,
        timeout: int | None = None,
        work_dir: str | None = None,
    ) -> str:
        """Fast file/code discovery tool. Use it to find relevant files, then read them yourself with Read.

        Best for: discovering where code lives, tracing call chains, finding definitions/usages.
        After this tool returns citations, use Read to view the actual file contents.

        Args:
            query: What to find — be specific (e.g. "Find the model, view, serializer for X").
            max_turns: Max exploration turns (default 16, or MAX_TURNS env).
            citation: If true, returns only the <final_answer> block with file:line citations.
            timeout: Max seconds before giving up (default: no limit).
            work_dir: Project directory to explore (default: server startup dir).
        """
        from fastcontext.agent.agent_factory import make_fastcontext_agent

        wd = Path(work_dir).resolve() if work_dir else cwd
        traj = f".fastcontext/mcp_trajectory_{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.jsonl"
        agent = make_fastcontext_agent(trajectory_file=traj, work_dir=str(wd))
        mt = max_turns if max_turns != 0 else None
        try:
            if timeout and timeout > 0:
                result = await asyncio.wait_for(
                    agent.run(prompt=query, max_turns=mt, verbose=verbose, citation=citation),
                    timeout=timeout,
                )
            else:
                result = await agent.run(prompt=query, max_turns=mt, verbose=verbose, citation=citation)
        except asyncio.TimeoutError:
            return "Task timed out. Try a more focused query, reduce max_turns, or increase the timeout."
        if citation:
            return get_final_answer(result)
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
