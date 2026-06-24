import os

from aide.agent.agent import Agent
from aide.agent.llm import LLM
from aide.agent.tool.tool import ToolSet

from aide.agent.utils import load_system_prompt


def make_aide_agent(
    trajectory_file: str,
    work_dir: str,
    **kwargs,
) -> Agent:
    name = "Aide"
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

    from aide.agent.tool.glob import GlobTool
    from aide.agent.tool.grep import GrepTool
    from aide.agent.tool.read import ReadTool

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
