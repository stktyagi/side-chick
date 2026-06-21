import argparse
import asyncio
import os
from datetime import datetime

from fastcontext.agent.agent_factory import make_fastcontext_agent


def main():
    """FastContext Command Line Interface"""
    parser = argparse.ArgumentParser(
        description="FastContext CLI",
    )

    parser.add_argument("--query", "-q", type=str, help="query to ask the agent")
    parser.add_argument(
        "--traj",
        "-t",
        type=str,
        help="agent trajectory file",
        default=f".fastcontext/trajectory_{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.jsonl",
    )
    parser.add_argument("--max-turns", type=int, help="maximum number of turns", default=4)
    parser.add_argument("--verbose", action="store_true", help="whether to run in verbose mode")
    parser.add_argument("--citation", action="store_true", help="Only return the citations in the final answer")

    sub = parser.add_subparsers(dest="command", title="commands")
    mcp_p = sub.add_parser("mcp", help="Start MCP server over HTTP/SSE (task + info tools)")
    mcp_p.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    mcp_p.add_argument("--port", "-p", type=int, default=8931, help="Port (default: 8931)")
    mcp_p.add_argument("--work-dir", "-w", default=None, help="Working directory (default: cwd)")
    mcp_p.add_argument("--verbose", action="store_true", help="Debug logging")

    args = parser.parse_args()

    if args.command == "mcp":
        from fastcontext.mcp_server import run_server

        run_server(host=args.host, port=args.port, work_dir=args.work_dir, verbose=args.verbose)
        return

    work_dir = os.getcwd()
    agent = make_fastcontext_agent(trajectory_file=args.traj, work_dir=work_dir)

    prompt = args.query
    final_output = asyncio.run(
        agent.run(prompt=prompt, max_turns=args.max_turns, verbose=args.verbose, citation=args.citation)
    )
    print(final_output)


if __name__ == "__main__":
    main()
