import json
from pathlib import Path

from .tool import Tool


class GrepTool(Tool):
    name = "Grep"
    description: str = Tool.load_desc(Path(__file__).parent / "grep.md")
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search.",
            },
            "path": {
                "type": "string",
                "description": "File/dir to search (rg pattern -- PATH). Default: cwd.",
            },
            "glob": {
                "type": "string",
                "description": 'Glob filter (e.g. "*.js", "*.{ts,tsx}") → rg --glob',
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count_matches"],
                "description": 'content=matching lines, files_with_matches=paths only, count_matches=match counts',
            },
            "-B": {
                "type": "number",
                "description": "Lines before match (rg -B). Requires output_mode: content.",
            },
            "-A": {
                "type": "number",
                "description": "Lines after match (rg -A). Requires output_mode: content.",
            },
            "-C": {
                "type": "number",
                "description": "Context lines before+after (rg -C). Requires output_mode: content.",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers (rg -n). Requires output_mode: content.",
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive (rg -i).",
            },
            "type": {
                "type": "string",
                "description": "File type filter (rg --type): js, py, rust, go, java, etc.",
            },
            "head_limit": {
                "type": "number",
                "minimum": 0,
                "description": 'Limit output to first N lines/files/counts = "| head -N".',
            },
            "multiline": {
                "type": "boolean",
                "description": "Multiline mode, . matches newlines (rg -U --multiline-dotall).",
            },
        },
        "required": ["pattern"],
    }

    # Adjust this path if ripgrep is not in your system PATH
    _rg_path = "/usr/bin/rg"

    async def call(self, parameters: str, **kwargs) -> str:
        params: dict = json.loads(parameters)
        cwd = kwargs.get("cwd", Path.cwd().as_posix())
        # ripgrep parameters
        pattern = params.get("pattern")
        path = params.get("path", cwd)
        glob = params.get("glob")
        output_mode = params.get("output_mode")
        before_context = params.get("-B")
        after_context = params.get("-A")
        context = params.get("-C")
        line_number = params.get("-n", True)
        ignore_case = params.get("-i", False)
        file_type = params.get("type")
        head_limit = params.get("head_limit")
        multiline = params.get("multiline")

        resolved_path = Path(path)
        if not resolved_path.is_absolute():
            resolved_path = Path(cwd, path)
        if not resolved_path.resolve().is_relative_to(Path(cwd).resolve()):
            return f"Permission error: `{path}` is not within the working directory `{cwd}`."

        search_path = str(resolved_path.resolve())
        output = run_rg(
            self._rg_path,
            pattern,
            search_path,
            glob=glob,
            output_mode=output_mode,
            before_context=before_context,
            after_context=after_context,
            context=context,
            line_number=line_number,
            ignore_case=ignore_case,
            type=file_type,
            multiline=multiline,
        )
        if not output:
            return "No matches found"

        lines = output.splitlines()
        if head_limit is not None and head_limit > 0 and len(lines) > head_limit:
            output = "\n".join(lines[:head_limit])
            output += f"\nResults truncated to first {head_limit} lines"
        return output


def run_rg(rg_path: str, pattern: str, path: str, **kwargs) -> str:
    import subprocess

    command = [rg_path]
    command.append(pattern)
    if path:
        command.append(path)
    if kwargs.get("glob"):
        command.append("--glob")
        command.append(kwargs["glob"])
    if kwargs.get("ignore_case"):
        command.append("--ignore-case")
    if kwargs.get("type"):
        command.append("--type")
        command.append(kwargs["type"])
    if kwargs.get("multiline"):
        command.append("--multiline")
        command.append("--multiline-dotall")
    output_mode = kwargs.get("output_mode")
    if output_mode == "content":
        if kwargs.get("before_context") is not None:
            command.append("-B")
            command.append(str(kwargs["before_context"]))
        if kwargs.get("after_context") is not None:
            command.append("-A")
            command.append(str(kwargs["after_context"]))
        if kwargs.get("context") is not None:
            command.append("-C")
            command.append(str(kwargs["context"]))
        if kwargs.get("line_number"):
            command.append("-n")
    elif output_mode == "files_with_matches":
        command.append("--files-with-matches")
    elif output_mode == "count_matches":
        command.append("--count-matches")

    # --heading and --color never
    command.append("--heading")
    command.append("--color")
    command.append("never")

    cwd = Path.cwd().as_posix()
    output = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if output.returncode == 0:
        output_text = output.stdout if isinstance(output.stdout, str) else output.stdout.decode("utf-8")
    else:
        output_text = output.stderr if isinstance(output.stderr, str) else output.stderr.decode("utf-8")
    return output_text
