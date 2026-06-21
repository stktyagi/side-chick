import json
from pathlib import Path

import aiofiles

from .tool import Tool

MAX_LINE = 2000
MAX_LINE_LENGTH = 2000


class ReadTool(Tool):
    name = "Read"
    description: str = Tool.load_desc(Path(__file__).parent / "read.md")
    parameters = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path of file to read.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line to start from. Positive=1-indexed from start, negative=count from end. For large files.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of lines to read. For large files.",
                },
            },
            "required": ["path"],
        }

    async def call(self, parameters: str, **kwargs) -> str:
        params: dict = json.loads(parameters)
        file_path = params.get("path")
        offset = params.get("offset")
        limit = params.get("limit")

        if not file_path:
            return "Read Tool: file path is required."

        if not Path(file_path).exists():
            return f"Read Tool: file {file_path} does not exist."

        async with aiofiles.open(file_path, mode="r") as f:
            raw_lines = await f.readlines()

        if len(raw_lines) == 0:
            return "File is empty."

        end_line = -1
        if offset is None or offset < 0:
            offset = 1
        if limit is not None:
            end_line = offset + limit - 1
        if end_line == -1 or end_line > len(raw_lines):
            end_line = len(raw_lines)

        lines = []
        total_read_lines = end_line - offset + 1
        if total_read_lines > MAX_LINE:
            end_line = offset + MAX_LINE - 1
        for i in range(offset - 1, end_line):
            if len(raw_lines[i]) > MAX_LINE_LENGTH:
                line = raw_lines[i][:MAX_LINE_LENGTH] + "...\n"
            else:
                line = raw_lines[i]
            prefixed_line = f"{i+1}|{line}"
            lines.append(prefixed_line)
        if total_read_lines > MAX_LINE:
            lines.append("...")
        content = "".join(lines)
        output = f"```{file_path}:{offset}-{end_line}\n{content}\n```"
        return output
