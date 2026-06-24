import asyncio
from uuid import uuid4

from aide.agent.context import Context
from aide.agent.llm import LLM, Message, RequestyAPIError
from aide.agent.tool import ToolSet
from aide.agent.utils import get_final_answer


class Agent:
    """The loaded agent."""

    name: str
    system_prompt: str
    llm: LLM
    toolset: ToolSet
    context: Context

    work_dir: str

    def __init__(
        self,
        name: str,
        system_prompt: str,
        llm: LLM,
        toolset: ToolSet,
        trajectory_file: str,
        work_dir: str,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm
        self.toolset = toolset
        self.context = Context(trajectory_file)
        self.work_dir = work_dir
        self.run_id = str(uuid4())
        self.n_turn = 0

    async def _agent_loop(self, prompt: str, max_turns: int | None, verbose: bool, citation: bool) -> str:
        # user promp -> tool calls -> tool results -> tool calls ... -> assistant final answer
        n_turn = 0
        await self.context.add(Message(role="system", content=self.system_prompt))
        await self.context.add(Message(role="user", content=prompt))

        while True:
            n_turn += 1

            # Force final answer when budget is exhausted
            force_final = False
            if max_turns is not None and n_turn > max_turns:
                # On the turn right after the forced prompt, if LLM still made tool calls,
                # we call again WITHOUT tools so it must respond with text.
                # The forced prompt was already injected the previous iteration.
                if n_turn > max_turns + 1:
                    force_final = True
                elif n_turn == max_turns + 1:
                    await self.context.add(
                        Message(
                            role="user",
                            content="Max number of turns reached. Please provide the final answer based on the information you have gathered.",
                        )
                    )

            # call LLM to get next action
            try:
                step_msg = await asyncio.wait_for(
                    self.llm.acall(
                        messages=self.context.get_messages(),
                        tools=None if force_final else self.toolset.schema_list(),
                    ),
                    timeout=120,
                )
            except (RequestyAPIError, asyncio.TimeoutError) as e:
                error_msg = f"LLM API call failed. So stopping the agent.\nError details:\n{str(e)}"
                await self.context.add(Message(role="assistant", content=error_msg))
                return error_msg
            self.n_turn = n_turn
            await self.context.add(step_msg)
            if verbose:
                print(f"Turn {n_turn}: \n {step_msg.to_dict()} \n")

            if force_final:
                if citation:
                    return get_final_answer(step_msg.content)
                return step_msg.content

            if step_msg.tool_calls:
                tools_result_msg = await self.toolset.call(step_msg)
                await self.context.add(tools_result_msg)
            else:
                if citation:
                    return get_final_answer(step_msg.content)
                return step_msg.content

    async def run(self, prompt: str, max_turns: int | None = 4, verbose: bool = False, citation: bool = False) -> str:
        if verbose:
            print("=== Agent Runtime Info ===")
            print(f"Agent: {self.name}")
            print(f"LLM: {self.llm.model}")
            print(f"Working Directory: {self.work_dir}")
            print("Agent Tools: " + " / ".join(self.toolset._tool_dict.keys()))
            print(f"User prompt:\n{prompt}\n")
            print("=== Agent Trajectory ===")
        return await self._agent_loop(prompt, max_turns, verbose, citation)
