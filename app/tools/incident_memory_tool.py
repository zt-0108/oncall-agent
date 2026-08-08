"""供 Agent 检索已确认历史故障案例的工具。"""

from langchain_core.tools import tool
from loguru import logger

from app.services.incident_memory_service import incident_memory_service


@tool
def retrieve_incident_memories(query: str, top_k: int = 3) -> str:
    """检索经人工确认的历史故障案例，仅作为排查线索，不能作为当前故障事实。"""
    try:
        results = incident_memory_service.search_similar(query, max(1, min(top_k, 5)))
    except Exception as exc:
        logger.warning("历史故障记忆检索失败: {}", exc)
        return "历史故障记忆暂不可用，继续依据当前日志、指标和告警诊断。"
    if not results:
        return "未找到相似的已确认历史故障案例。"

    sections = ["以下内容是历史相似案例，不是当前故障事实。必须用当前日志、指标和告警重新验证："]
    for index, item in enumerate(results, 1):
        sections.append(
            f"{index}. [{item.incident_id}] {item.title}\n"
            f"   服务：{item.service_name}\n"
            f"   历史已确认根因：{item.confirmed_root_cause}\n"
            f"   历史处置：{item.resolution}\n"
            f"   来源：{item.source}"
        )
    return "\n\n".join(sections)
