import os

from fastcontext.agent.agent import Agent
from fastcontext.agent.llm import LLM
from fastcontext.agent.tool.tool import ToolSet

from fastcontext.agent.utils import load_system_prompt


def make_fastcontext_agent(
    trajectory_file: str,
    work_dir: str,
    **kwargs,
) -> Agent:
    name = "FastContext"
    system_prompt = kwargs.get("system_prompt", None)
    if system_prompt is None:
        system_prompt = load_system_prompt(work_dir)

    max_tokens = os.getenv("MAX_TOKENS")
    if max_tokens is not None:
        try:
            max_tokens = int(max_tokens)
        except ValueError:
            max_tokens = None

    llm = LLM(
        model=os.getenv("MODEL"),
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("BASE_URL"),
        max_tokens=max_tokens,
    )

    from fastcontext.agent.tool.glob import GlobTool
    from fastcontext.agent.tool.grep import GrepTool
    from fastcontext.agent.tool.read import ReadTool

    tools = [GlobTool(), GrepTool(), ReadTool()]

    toolset = ToolSet(tools, work_dir=work_dir)
    return Agent(
        name=name,
        system_prompt=system_prompt,
        llm=llm,
        toolset=toolset,
        trajectory_file=trajectory_file,
        work_dir=work_dir,
    )
