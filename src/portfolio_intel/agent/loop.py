"""Tool-use loop over MCP: Claude picks tools, the MCP server computes every number."""

import json
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message, MessageParam, ToolParam
from langfuse import get_client, observe
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult

from portfolio_intel.config import settings

from .grounding import check_grounding
from .prompts import SYSTEM_PROMPT

_client: AsyncAnthropic | None = None


def anthropic_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic()
    return _client


def mcp_client(url: str | None = None, token: str | None = None) -> Client:
    headers = {"Authorization": f"Bearer {token or settings.mcp_token}"}
    transport = streamable_http_client(
        url or settings.mcp_url, http_client=create_mcp_http_client(headers=headers)
    )
    return Client(transport)


def _tool_text(result: CallToolResult) -> str:
    if result.structured_content:
        return json.dumps(result.structured_content)
    return "".join(c.text for c in result.content if c.type == "text")


def _answer(resp: Message) -> str:
    return "".join(b.text for b in resp.content if b.type == "text")


@observe(name="pm_question")
async def ask(
    question: str, history: list[MessageParam] | None = None, session: Client | None = None
) -> dict[str, Any]:
    """Answer one PM question. `session` lets tests pass an in-process MCP client."""
    if session is None:
        async with mcp_client() as client:
            return await _ask(client, question, history)
    return await _ask(session, question, history)


async def _ask(
    session: Client, question: str, history: list[MessageParam] | None
) -> dict[str, Any]:
    tools: list[ToolParam] = [
        {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
        for t in (await session.list_tools()).tools
    ]
    messages: list[MessageParam] = [*(history or []), {"role": "user", "content": question}]
    tool_outputs: list[str] = []
    calls: list[dict[str, Any]] = []

    resp = await call_model(messages, tools)
    for _ in range(settings.agent_max_steps):
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        results: list[Any] = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            r = await session.call_tool(block.name, dict(block.input))
            text = _tool_text(r)
            tool_outputs.append(text)
            calls.append({"name": block.name, "input": block.input, "is_error": r.is_error})
            results.append(
                {"type": "tool_result", "tool_use_id": block.id,
                 "content": text, "is_error": bool(r.is_error)}
            )  # fmt: skip
        messages.append({"role": "user", "content": results})  # all results in one message
        resp = await call_model(messages, tools)

    answer = _answer(resp)
    report = check_grounding(answer, tool_outputs, question)
    rewritten = False
    if report.ungrounded:
        messages.append({"role": "assistant", "content": resp.content})
        messages.append(
            {"role": "user", "content": (
                f"These numbers in your answer are not in any tool output: {report.ungrounded}. "
                "Rewrite the answer using only tool-provided numbers."
            )}
        )  # fmt: skip
        resp = await call_model(messages, tools, allow_tools=False)
        answer, rewritten = _answer(resp), True
        report = check_grounding(answer, tool_outputs, question)
    get_client().update_current_span(
        metadata={"grounded": not report.ungrounded, "rewritten": rewritten}
    )
    return {
        "answer": answer,
        "grounding": report.model_dump(),
        "tool_calls": calls,
        "stop_reason": resp.stop_reason,
    }


@observe(name="claude", as_type="generation")
async def call_model(
    messages: list[MessageParam], tools: list[ToolParam], allow_tools: bool = True
) -> Message:
    kwargs: dict[str, Any] = {"tools": tools} if tools else {}
    if tools and not allow_tools:
        kwargs["tool_choice"] = {"type": "none"}  # tools stay defined: history has tool_use
    resp: Message = await anthropic_client().messages.create(
        model=settings.anthropic_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=messages,
        **kwargs,
    )
    get_client().update_current_generation(
        model=settings.anthropic_model,
        usage_details={"input": resp.usage.input_tokens, "output": resp.usage.output_tokens},
    )
    return resp
