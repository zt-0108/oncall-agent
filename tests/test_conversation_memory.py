from typing import Any

import pytest
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import app.services.rag_agent_service as rag_module
from app.services.rag_agent_service import (
    SUMMARY_SOURCE,
    RagAgentService,
    SlidingWindowMiddleware,
)


def test_sliding_window_keeps_latest_summary_and_recent_turns():
    summary = HumanMessage(
        content="历史摘要",
        additional_kwargs={"lc_source": SUMMARY_SOURCE},
    )
    messages = [summary]
    for index in range(1, 5):
        messages.extend(
            [
                HumanMessage(content=f"问题 {index}"),
                AIMessage(content=f"回答 {index}"),
            ]
        )

    selected = SlidingWindowMiddleware(turns=2).select_messages(messages)

    assert [message.content for message in selected] == [
        "历史摘要",
        "问题 3",
        "回答 3",
        "问题 4",
        "回答 4",
    ]


def test_sliding_window_does_not_drop_unsummarized_history():
    messages = []
    for index in range(1, 5):
        messages.extend(
            [
                HumanMessage(content=f"问题 {index}"),
                AIMessage(content=f"回答 {index}"),
            ]
        )

    selected = SlidingWindowMiddleware(turns=2).select_messages(messages)

    assert selected == messages


def test_sliding_window_does_not_split_tool_call_sequence():
    summary = HumanMessage(
        content="历史摘要",
        additional_kwargs={"lc_source": SUMMARY_SOURCE},
    )
    messages = [
        summary,
        HumanMessage(content="旧问题"),
        AIMessage(content="旧回答"),
        HumanMessage(content="查询当前告警"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_alerts",
                    "args": {},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="无活动告警", tool_call_id="call-1"),
        AIMessage(content="当前无活动告警"),
    ]

    selected = SlidingWindowMiddleware(turns=1).select_messages(messages)

    assert selected == [summary, *messages[3:]]


@pytest.mark.asyncio
async def test_summary_middleware_replaces_old_history_and_keeps_recent_messages():
    middleware = SummarizationMiddleware(
        FakeListChatModel(responses=["压缩后的历史摘要"]),
        trigger=("messages", 4),
        keep=("messages", 2),
        summary_prompt="{messages}",
    )
    messages = [
        HumanMessage(content="问题 1"),
        AIMessage(content="回答 1"),
        HumanMessage(content="问题 2"),
        AIMessage(content="回答 2"),
        HumanMessage(content="问题 3"),
        AIMessage(content="回答 3"),
    ]

    update = await middleware.abefore_model({"messages": messages}, None)

    assert update is not None
    replacement = update["messages"]
    assert replacement[1].additional_kwargs["lc_source"] == SUMMARY_SOURCE
    assert "压缩后的历史摘要" in replacement[1].content
    assert replacement[-2:] == messages[-2:]


@pytest.mark.asyncio
async def test_agent_registers_summary_and_sliding_window_middlewares(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_load_mcp_tools_independently():
        return [], {}, {}

    def fake_create_agent(model, **kwargs):
        captured["model"] = model
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        rag_module,
        "load_mcp_tools_independently",
        fake_load_mcp_tools_independently,
    )
    monkeypatch.setattr(rag_module, "create_agent", fake_create_agent)

    service = RagAgentService(streaming=False)
    await service._initialize_agent()

    assert captured["system_prompt"] == service.system_prompt
    assert captured["middleware"] == [
        service.summary_middleware,
        service.sliding_window_middleware,
    ]
    assert isinstance(service.summary_middleware, SummarizationMiddleware)
    assert service.summary_middleware.trigger == [
        ("tokens", rag_module.config.conversation_summary_trigger_tokens),
        ("messages", rag_module.config.conversation_summary_trigger_messages),
    ]
    assert service.summary_middleware.keep == (
        "messages",
        rag_module.config.conversation_summary_keep_messages,
    )
