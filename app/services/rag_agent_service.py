"""RAG Agent 服务 - 基于 LangGraph 的智能代理

使用 langchain_qwq 的 ChatQwen 原生集成，
支持真正的流式输出和更好的模型适配。
"""

import asyncio
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from textwrap import dedent
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, SummarizationMiddleware
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_qwq import ChatQwen
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from loguru import logger
from typing_extensions import TypedDict

from app.agent.mcp_client import (
    format_exception_chain,
    load_mcp_tools_independently,
    suggest_mcp_transport,
)
from app.config import config
from app.tools import DEFAULT_LOCAL_AGENT_TOOLS

# 阿里千问大模型和langchain集成参考： https://docs.langchain.com/oss/python/integrations/chat/qwen
# 注意：需要配置环境变量 DASHSCOPE_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1 否则默认访问的是新加坡站点
# 同时也需要配置环境变量 DASHSCOPE_API_KEY=your_api_key


class AgentState(TypedDict):
    """Agent 状态"""

    messages: Annotated[Sequence[BaseMessage], add_messages]


SUMMARY_SOURCE = "summarization"

CONVERSATION_SUMMARY_PROMPT = dedent("""
    你负责压缩一段多轮对话，生成供后续 Agent 继续工作的上下文摘要。

    请保留：
    1. 用户的核心目标、明确要求、偏好和约束；
    2. 已确认的事实、重要实体、时间、标识符和参数；
    3. 已调用工具及其关键结果，尤其是告警、指标、日志和知识库证据；
    4. 已做出的决定、被否决的方案及原因；
    5. 尚未完成的事项、风险、错误和下一步。

    删除寒暄、重复内容和已经失去后续价值的细节。不得补充原对话中不存在的信息。
    使用结构化、紧凑的中文 Markdown 输出，并明确区分事实、推断和待验证项。

    <messages>
    {messages}
    </messages>
""").strip()


class SlidingWindowMiddleware(AgentMiddleware):
    """仅限制每次模型调用的可见历史，不删除检查点中的完整会话状态。"""

    def __init__(self, turns: int) -> None:
        super().__init__()
        self.turns = max(1, turns)

    @staticmethod
    def _is_summary(message: BaseMessage) -> bool:
        return message.additional_kwargs.get("lc_source") == SUMMARY_SOURCE

    def select_messages(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """保留最新摘要和最近 N 个用户轮次，避免截断 AI/Tool 消息组。"""
        message_list = list(messages)
        human_indices = [
            index
            for index, message in enumerate(message_list)
            if isinstance(message, HumanMessage) and not self._is_summary(message)
        ]
        if len(human_indices) <= self.turns:
            return message_list

        start_index = human_indices[-self.turns]
        recent_messages = message_list[start_index:]
        summaries = [message for message in message_list[:start_index] if self._is_summary(message)]
        if not summaries:
            return message_list
        return [summaries[-1], *recent_messages]

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[Any]],
    ) -> Any:
        window_messages = self.select_messages(request.messages)
        if len(window_messages) != len(request.messages):
            logger.debug(
                "模型输入应用滑动窗口: {} -> {} 条消息，保留最近 {} 个用户轮次",
                len(request.messages),
                len(window_messages),
                self.turns,
            )
            request = request.override(messages=window_messages)
        return await handler(request)


class RagAgentService:
    """RAG Agent 服务 - 使用 LangGraph + ChatQwen 原生集成"""

    def __init__(self, streaming: bool = True):
        """初始化 RAG Agent 服务

        Args:
            streaming: 是否启用流式输出，默认为 True
        """
        self.model_name = config.rag_model
        self.streaming = streaming
        self.system_prompt = self._build_system_prompt()

        self.model = ChatQwen(
            model=self.model_name,
            api_key=config.dashscope_api_secret,
            temperature=0.7,
            streaming=streaming,
        )

        self.summary_model = ChatQwen(
            model=self.model_name,
            api_key=config.dashscope_api_secret,
            temperature=0,
            streaming=False,
        )
        self.summary_middleware = SummarizationMiddleware(
            self.summary_model,
            trigger=[
                ("tokens", max(1, config.conversation_summary_trigger_tokens)),
                ("messages", max(2, config.conversation_summary_trigger_messages)),
            ],
            keep=("messages", max(1, config.conversation_summary_keep_messages)),
            summary_prompt=CONVERSATION_SUMMARY_PROMPT,
        )
        self.sliding_window_middleware = SlidingWindowMiddleware(config.conversation_window_turns)

        # 定义基础工具（与 AIOps Planner/Executor 使用同一套默认本地工具）
        self.tools = list(DEFAULT_LOCAL_AGENT_TOOLS)

        # MCP 客户端（延迟初始化，使用全局管理）
        self.mcp_tools: list = []

        # 创建内存检查点（用于会话管理）
        self.checkpointer = MemorySaver()

        # Agent 初始化（会在异步方法中完成）
        self.agent: Any = None
        self._agent_initialized = False
        self._initialization_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._recent_sessions: OrderedDict[str, None] = OrderedDict()

        logger.info(
            f"RAG Agent 服务初始化完成 (ChatQwen), model={self.model_name}, streaming={streaming}"
        )

    async def _initialize_agent(self) -> None:
        """并发安全地初始化 Agent 和 MCP 工具。"""
        if self._agent_initialized:
            return

        async with self._initialization_lock:
            if self._agent_initialized:
                return

            for name, server in config.mcp_servers.items():
                hint = suggest_mcp_transport(
                    str(server.get("url", "")),
                    str(server.get("transport", "")),
                )
                if hint:
                    logger.warning(f"MCP 配置 [{name}]: {hint}")

            mcp_tools, mcp_errors, _ = await load_mcp_tools_independently()
            self.mcp_tools = mcp_tools
            for name, error in mcp_errors.items():
                logger.warning(f"MCP 服务 [{name}] 加载失败，继续使用其他工具\n{error}")
            logger.info(f"成功加载 {len(mcp_tools)} 个 MCP 工具")

            all_tools = self.tools + self.mcp_tools
            self.agent = create_agent(
                self.model,
                tools=all_tools,
                system_prompt=self.system_prompt,
                middleware=[
                    self.summary_middleware,
                    self.sliding_window_middleware,
                ],
                checkpointer=self.checkpointer,
            )
            self._agent_initialized = True

            if all_tools:
                tool_names = [
                    tool.name if hasattr(tool, "name") else str(tool) for tool in all_tools
                ]
                logger.info(f"可用工具列表: {', '.join(tool_names)}")

    async def _prepare_messages(
        self,
        session_id: str,
        question: str,
    ) -> list[BaseMessage]:
        """限制内存会话数量，并避免每轮重复写入 SystemMessage。"""
        config_dict: RunnableConfig = {"configurable": {"thread_id": session_id}}

        async with self._session_lock:
            checkpoint = self.checkpointer.get(config_dict)
            messages = (
                checkpoint.get("channel_values", {}).get("messages", []) if checkpoint else []
            )

            if len(messages) >= config.max_session_messages:
                self.checkpointer.delete_thread(session_id)
                messages = []
                logger.warning("会话 {} 超过消息上限，已清理旧上下文", session_id)

            self._recent_sessions.pop(session_id, None)
            self._recent_sessions[session_id] = None
            while len(self._recent_sessions) > config.max_memory_sessions:
                expired_session_id, _ = self._recent_sessions.popitem(last=False)
                self.checkpointer.delete_thread(expired_session_id)
                logger.info("清理最久未使用的内存会话: {}", expired_session_id)

        return [HumanMessage(content=question)]

    def _build_system_prompt(self) -> str:
        """
        构建系统提示词

        注意：LangChain 框架会自动将工具信息传递给 LLM，
        因此系统提示词中无需列举具体的工具列表。

        Returns:
            str: 系统提示词
        """
        from textwrap import dedent

        return dedent("""
            你是一个专业的AI助手，能够使用多种工具来帮助用户解决问题。

            工作原则:
            1. 理解用户需求，选择合适的工具来完成任务
            2. 当需要获取实时信息或专业知识时，主动使用相关工具
            3. 基于工具返回的结果提供准确、专业的回答
            4. 如果工具无法提供足够信息，请诚实地告知用户

            回答要求:
            - 保持友好、专业的语气
            - 回答简洁明了，重点突出
            - 基于事实，不编造信息
            - 如有不确定的地方，明确说明

            请根据用户的问题，灵活使用可用工具，提供高质量的帮助。
        """).strip()

    async def query(
        self,
        question: str,
        session_id: str,
    ) -> str:
        """
        非流式处理用户问题（一次性返回完整答案）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）

        Returns:
            str: 完整答案
        """
        try:
            await self._initialize_agent()

            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（非流式）: {question}")

            messages = await self._prepare_messages(session_id, question)

            # 构建 Agent 输入
            agent_input = {"messages": messages}

            # 配置 thread_id（用于会话持久化）
            config_dict = {"configurable": {"thread_id": session_id}}

            agent = self.agent
            if agent is None:
                raise RuntimeError("Agent 初始化失败")

            result = await agent.ainvoke(
                input=agent_input,
                config=config_dict,
            )

            # 提取最终答案
            messages_result = result.get("messages", [])
            if messages_result:
                last_message = messages_result[-1]
                answer = (
                    last_message.content if hasattr(last_message, "content") else str(last_message)
                )

                # 记录工具调用
                if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                    tool_names = [tc.get("name", "unknown") for tc in last_message.tool_calls]
                    logger.info(f"[会话 {session_id}] Agent 调用了工具: {tool_names}")

                logger.info(f"[会话 {session_id}] RAG Agent 查询完成（非流式）")
                return answer

            logger.warning(f"[会话 {session_id}] Agent 返回结果为空")
            return ""

        except Exception as e:
            logger.error(
                f"[会话 {session_id}] RAG Agent 查询失败（非流式）: {format_exception_chain(e)}"
            )
            raise

    async def query_stream(
        self,
        question: str,
        session_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式处理用户问题。"""
        try:
            await self._initialize_agent()
            messages = await self._prepare_messages(session_id, question)
            config_dict: RunnableConfig = {"configurable": {"thread_id": session_id}}

            agent = self.agent
            if agent is None:
                raise RuntimeError("Agent 初始化失败")

            answer_parts: list[str] = []
            async for token, metadata in agent.astream(
                input={"messages": messages},
                config=config_dict,
                stream_mode="messages",
            ):
                node_name = (
                    metadata.get("langgraph_node", "unknown")
                    if isinstance(metadata, dict)
                    else "unknown"
                )
                if type(token).__name__ not in ("AIMessage", "AIMessageChunk"):
                    continue

                content_blocks = getattr(token, "content_blocks", None)
                if not isinstance(content_blocks, list):
                    continue

                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_content = block.get("text", "")
                        if text_content:
                            answer_parts.append(text_content)
                            yield {
                                "type": "content",
                                "data": text_content,
                                "node": node_name,
                            }

            yield {
                "type": "complete",
                "data": {"answer": "".join(answer_parts)},
            }
        except Exception as exc:
            detail = format_exception_chain(exc)
            logger.error(f"[会话 {session_id}] RAG Agent 流式查询失败: {detail}")
            yield {"type": "error", "data": "请求处理失败"}

    def get_session_history(self, session_id: str) -> list[dict[str, str]]:
        """读取 MemorySaver 中的会话历史。"""
        try:
            config_dict: RunnableConfig = {"configurable": {"thread_id": session_id}}
            checkpoint = self.checkpointer.get(config_dict)
            if not checkpoint:
                return []

            messages = checkpoint.get("channel_values", {}).get("messages", [])
            history: list[dict[str, str]] = []
            for message in messages:
                if isinstance(message, SystemMessage):
                    continue

                role = "user" if isinstance(message, HumanMessage) else "assistant"
                item = {
                    "role": role,
                    "content": str(getattr(message, "content", message)),
                }
                timestamp = getattr(message, "timestamp", None)
                if timestamp:
                    item["timestamp"] = str(timestamp)
                history.append(item)
            return history
        except Exception:
            logger.exception("获取会话历史失败: {}", session_id)
            return []

    def clear_session(self, session_id: str) -> bool:
        """
        清空会话历史（从 MemorySaver checkpointer 中删除）

        Args:
            session_id: 会话ID（即 thread_id）

        Returns:
            bool: 是否成功
        """
        try:
            # 使用 checkpointer 的 delete_thread 方法删除该 thread 的所有检查点
            self.checkpointer.delete_thread(session_id)

            logger.info(f"已清除会话历史: {session_id}")
            return True

        except Exception as e:
            logger.error(f"清空会话历史失败: {session_id}, 错误: {e}")
            return False

    async def cleanup(self):
        """清理资源"""
        try:
            logger.info("清理 RAG Agent 服务资源...")
            # MCP 客户端由全局管理器统一管理，无需手动清理
            logger.info("RAG Agent 服务资源已清理")
        except Exception as e:
            logger.error(f"清理资源失败: {e}")


# 全局单例 - 启用流式输出
rag_agent_service = RagAgentService(streaming=True)
