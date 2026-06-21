"""Info tool — quick file/dir summaries via LLM, no raw read needed."""

from pathlib import Path

from fastcontext.agent.tool.tool import Tool
from fastcontext.indexer.summarizer import summarize_file, summarize_dir

_INFO_DESC = (
    "Summarize file or directory. Returns purpose, key functions/classes, deps. "
    "Use instead of Read for quick understanding. "
    "Path: relative to work dir. "
    "File: structural overview. Dir: lists contents + purpose."
)

_INFO_PARAMS = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Relative path to file or directory",
        },
    },
    "required": ["path"],
}


class InfoTool(Tool):
    name = "info"
    description = _INFO_DESC
    parameters = _INFO_PARAMS

    async def call(self, parameters: str, **kwargs) -> str:
        import json

        args = json.loads(parameters)
        path = args["path"]
        cwd = Path(kwargs.get("cwd", ".")).resolve()
        target = (cwd / path).resolve()

        if not str(target).startswith(str(cwd)):
            return f"Error: path outside work dir: {path}"
        if not target.exists():
            return f"Error: path not found: {path}"

        if target.is_file():
            return await summarize_file(target, path)
        else:
            return await summarize_dir(target, path)
