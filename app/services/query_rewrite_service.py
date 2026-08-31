"""Conditional query rewriting for noisy or context-dependent AIOps queries."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from textwrap import dedent

from langchain_core.prompts import ChatPromptTemplate
from langchain_qwq import ChatQwen
from loguru import logger
from pydantic import BaseModel, Field

from app.config import config

AMBIGUOUS_REFERENCE_PATTERN = re.compile(
    r"(?:这个|那个|上述|前面|刚才|之前|同样|还是它|这种情况|that one|same issue)",
    re.IGNORECASE,
)
LOG_NOISE_PATTERN = re.compile(
    r"(?:Traceback|Exception|ERROR|WARN|stack trace|\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}|"
    r"\b(?:trace_id|request_id|span_id|timestamp|level)=)",
    re.IGNORECASE,
)
IDENTIFIER_PATTERN = re.compile(
    r"\b(?:[A-Za-z0-9_.-]+(?i:service)|"
    r"[A-Za-z][A-Za-z0-9_.-]*(?i:Error|Exception)|"
    r"[A-Z][A-Z0-9_-]{2,}|[a-z][a-z0-9]*(?:_[a-z0-9]+)+|"
    r"\d+(?:\.\d+)?(?:ms|s|%|MiB|GiB))\b"
)
IGNORED_IDENTIFIERS = {
    "debug",
    "error",
    "info",
    "level",
    "request_id",
    "span_id",
    "timestamp",
    "trace",
    "trace_id",
    "warn",
    "warning",
}


class RewrittenQuery(BaseModel):
    """Structured model output."""

    query: str = Field(description="适合知识库检索的一条独立、简洁的查询")


@dataclass(frozen=True)
class QueryRewriteResult:
    """Observable result returned by the rewrite service."""

    original_query: str
    rewritten_query: str
    attempted: bool
    applied: bool
    reason: str


RewriteModel = Callable[[str], str]


REWRITE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent("""
                你负责把 AIOps 故障查询整理成适合知识库检索的一条独立查询。

                规则：
                - 只能使用原查询中已经出现的事实，不猜测根因、服务或环境。
                - 必须原样保留服务名、错误码、异常类、指标名、数值和单位。
                - 删除时间戳、重复日志、堆栈噪声、寒暄和无关描述。
                - 保留故障对象、症状、关键错误以及用户想查的问题。
                - 无法消除的指代不要臆测；如果改写没有收益，原样返回。
                - 只输出一条查询，不回答问题。
            """).strip(),
        ),
        ("user", "{query}"),
    ]
)


class QueryRewriteService:
    """Rewrite only queries that are likely to benefit, with safe fallback."""

    def __init__(self, rewrite_model: RewriteModel | None = None) -> None:
        self._rewrite_model = rewrite_model

    @staticmethod
    def should_rewrite(query: str) -> tuple[bool, str]:
        stripped = query.strip()
        if not stripped:
            return False, "empty"
        if AMBIGUOUS_REFERENCE_PATTERN.search(stripped):
            return True, "ambiguous_reference"
        if len(stripped) >= max(1, config.rag_query_rewrite_min_chars):
            return True, "long_query"
        if stripped.count("\n") >= 3:
            return True, "multiline_query"
        if LOG_NOISE_PATTERN.search(stripped) and (
            len(LOG_NOISE_PATTERN.findall(stripped)) >= 2 or len(stripped) >= 80
        ):
            return True, "log_noise"
        return False, "clear_query"

    @staticmethod
    def _protected_identifiers(query: str) -> set[str]:
        return {
            identifier
            for identifier in IDENTIFIER_PATTERN.findall(query)
            if identifier.lower() not in IGNORED_IDENTIFIERS
        }

    @staticmethod
    def _default_rewrite(query: str) -> str:
        llm = ChatQwen(
            model=config.rag_model,
            api_key=config.dashscope_api_secret,
            temperature=0,
            streaming=False,
        )
        chain = REWRITE_PROMPT | llm.with_structured_output(RewrittenQuery)
        result = chain.invoke({"query": query})
        if isinstance(result, RewrittenQuery):
            return result.query
        if isinstance(result, dict):
            return str(result.get("query", ""))
        return ""

    def rewrite(self, query: str) -> QueryRewriteResult:
        original = query.strip()
        should_rewrite, reason = self.should_rewrite(original)
        if not config.rag_query_rewrite_enabled or not should_rewrite:
            return QueryRewriteResult(original, original, False, False, reason)

        if self._rewrite_model is None and not config.dashscope_api_key:
            logger.warning("Query Rewrite 已跳过：未配置 DashScope API Key")
            return QueryRewriteResult(original, original, False, False, "missing_api_key")

        bounded_query = original[: max(1, config.rag_query_rewrite_max_chars)]
        try:
            rewrite_model = self._rewrite_model or self._default_rewrite
            rewritten = rewrite_model(bounded_query).strip()
        except Exception:
            logger.exception("Query Rewrite 失败，回退到原查询")
            return QueryRewriteResult(original, original, True, False, "rewrite_error")

        if not rewritten or rewritten == original:
            return QueryRewriteResult(original, original, True, False, "unchanged")

        missing_identifiers = self._protected_identifiers(original) - self._protected_identifiers(
            rewritten
        )
        if missing_identifiers:
            logger.warning(
                "Query Rewrite 丢失受保护标识符 {}，回退到原查询",
                sorted(missing_identifiers),
            )
            return QueryRewriteResult(original, original, True, False, "identifier_loss")

        logger.info(
            "Query Rewrite 已应用: reason={}, original_chars={}, rewritten_chars={}",
            reason,
            len(original),
            len(rewritten),
        )
        return QueryRewriteResult(original, rewritten, True, True, reason)


query_rewrite_service = QueryRewriteService()
