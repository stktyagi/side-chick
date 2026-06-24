from typing import Any, Literal

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageToolCall
from pydantic import BaseModel, model_serializer


class RequestyAPIError(Exception):
    """Exception for Requesty LLM API errors."""


type Role = Literal[
    "system",
    "user",
    "assistant",
    "tool",
]


class FunctionCall(BaseModel):
    id: str
    name: str
    arguments: str

    @model_serializer(mode="wrap")
    def serialize_call(self, handler, info):
        return {
            "id": self.id,
            "type": "function",
            "function": {"arguments": self.arguments, "name": self.name},
        }


class Message(BaseModel):
    id: str | None = None
    role: Role
    content: str | None = None
    reasoning_content: str | None = None

    # [{"name": name, "arguments": arguments, "id": id} ... ]
    tool_calls: list[dict | FunctionCall] | None = None
    tool_call_id: str | None = None
    model: str | None = None
    usage: dict | None = None

    def to_dict(self, exclude_none: bool = True) -> dict:
        return self.model_dump(exclude_none=exclude_none)


class LLM:
    def __init__(self, model: str | None, api_key: str | None, base_url: str | None, **kwargs) -> None:
        self.model = model
        self.base_url = base_url
        default_headers = kwargs.get("default_headers", None)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, default_headers=default_headers)
        self.max_tokens = kwargs.get("max_tokens")
        self.temperature = kwargs.get("temperature", 1.0)
        self.top_p = kwargs.get("top_p", 0.95)
        self.debug = kwargs.get("debug", False)

    async def acall(
        self,
        messages: list[dict | Message],
        tools: list[dict[str, Any]] | None,
    ) -> Message:

        if isinstance(messages[0], Message):
            messages = [message.to_dict(exclude_none=True) for message in messages]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
        }
        if self.max_tokens is not None:
            payload["max_completion_tokens"] = self.max_tokens
        if self.model and "qwen" in self.model:
            payload["extra_body"] = {
                "top_k": 20,
                "chat_template_kwargs": {"enable_thinking": False},
            }

        if tools:
            payload["tools"] = tools

        if self.debug:
            print("LLM Payload:", payload)

        try:
            if self.debug:
                print(f"DEBUG LLM: calling {self.model} with {len(messages)} msgs, tools={'yes' if tools else 'no'}")
            response = await self.client.chat.completions.create(**payload)
            if self.debug:
                print(f"DEBUG LLM: got response, choices={len(response.choices)}")
            usage = response.usage.to_dict() if response.usage else None
            content = None
            reasoning_content = None
            tool_calls: list[ChatCompletionMessageToolCall] = []
            role = response.choices[0].message.role

            if len(response.choices) == 1:
                content = response.choices[0].message.content
                if hasattr(response.choices[0].message, "reasoning_content"):
                    reasoning_content = response.choices[0].message.reasoning_content
                elif hasattr(response.choices[0].message, "reasoning_text"):
                    reasoning_content = response.choices[0].message.reasoning_text
                tool_calls = response.choices[0].message.tool_calls
            elif len(response.choices) == 2:
                msg = response.choices[0].message
                if hasattr(msg, "reasoning_text"):
                    reasoning_content = msg.reasoning_text
                content = msg.content
                for choice in response.choices:
                    if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                        tool_calls.extend(choice.message.tool_calls)
            elif len(response.choices) > 2:
                raise ValueError(f"Unexpected number of choices returned: {len(response.choices)}")
            else:
                raise ValueError("No choices returned from LLM API call.")

            if tool_calls:
                function_calls = [
                    FunctionCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments) for tc in tool_calls
                ]
                return Message(
                    role=role,
                    content=content,
                    reasoning_content=reasoning_content,
                    tool_calls=function_calls,
                    tool_call_id=tool_calls[0].id if len(tool_calls) == 1 else None,
                    model=self.model,
                    usage=usage,
                )
            return Message(
                role=role, content=content, reasoning_content=reasoning_content, model=self.model, usage=usage
            )
        except Exception as e:
            raise RequestyAPIError(f"LLM API call failed: {str(e)}") from e
