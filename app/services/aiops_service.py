"""基于 Plan-Execute-Replan 的 AIOps 诊断服务。"""

from collections.abc import AsyncGenerator
from textwrap import dedent
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from loguru import logger

from app.agent.aiops import PlanExecuteState, executor, planner, replanner

NODE_PLANNER = "planner"
NODE_EXECUTOR = "executor"
NODE_REPLANNER = "replanner"


class AIOpsService:
    def __init__(self) -> None:
        self.checkpointer = MemorySaver()
        self.graph = self._build_graph()
        logger.info("AIOps Plan-Execute-Replan 服务初始化完成")

    def _build_graph(self):
        workflow = StateGraph(PlanExecuteState)
        workflow.add_node(NODE_PLANNER, planner)
        workflow.add_node(NODE_EXECUTOR, executor)
        workflow.add_node(NODE_REPLANNER, replanner)
        workflow.set_entry_point(NODE_PLANNER)
        workflow.add_edge(NODE_PLANNER, NODE_EXECUTOR)
        workflow.add_edge(NODE_EXECUTOR, NODE_REPLANNER)

        def should_continue(state: PlanExecuteState) -> str:
            if state.get("response"):
                return END
            if state.get("plan", []):
                return NODE_EXECUTOR
            return END

        workflow.add_conditional_edges(
            NODE_REPLANNER,
            should_continue,
            {NODE_EXECUTOR: NODE_EXECUTOR, END: END},
        )
        return workflow.compile(checkpointer=self.checkpointer)

    async def execute(
        self, user_input: str, session_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        logger.info("[会话 {}] 开始 AIOps 任务", session_id)
        config_dict = {"configurable": {"thread_id": session_id}}
        initial_state: PlanExecuteState = {
            "input": user_input,
            "plan": [],
            "past_steps": [],
            "response": "",
        }
        try:
            async for event in self.graph.astream(
                input=initial_state,
                config=config_dict,
                stream_mode="updates",
            ):
                for node_name, output in event.items():
                    if node_name == NODE_PLANNER:
                        yield self._format_planner_event(output)
                    elif node_name == NODE_EXECUTOR:
                        yield self._format_executor_event(output)
                    elif node_name == NODE_REPLANNER:
                        yield self._format_replanner_event(output)

            final_state = self.graph.get_state(config_dict)
            response = ""
            if final_state and final_state.values:
                response = final_state.values.get("response", "")
            self.checkpointer.delete_thread(session_id)
            yield {
                "type": "complete",
                "stage": "diagnosis_complete",
                "message": "诊断流程完成",
                "diagnosis": {"status": "completed", "report": response},
            }
        except Exception:
            self.checkpointer.delete_thread(session_id)
            logger.exception("[会话 {}] AIOps 任务失败", session_id)
            yield {
                "type": "error",
                "stage": "error",
                "message": "诊断执行失败，请查看服务日志",
            }

    async def diagnose_realtime(self, session_id: str) -> AsyncGenerator[dict[str, Any], None]:
        task = dedent(
            """
            执行一次实时告警诊断。首先调用 Prometheus 告警工具获取当前真实告警；
            对每条告警按需查询指标、日志和知识库证据，并生成 Markdown 报告。

            报告必须区分：事实证据、基于证据的推断、尚待验证的假设。
            禁止编造指标、日志、时间、服务名或根因。若当前无告警，明确报告“当前无活动告警”，
            不要虚构故障；若部分工具失败，列出缺失证据及下一步人工核验方法。
            已确认的历史故障记忆只能作为相似案例和排查线索，不能充当当前故障证据；
            报告引用历史案例时必须标注“历史案例”，并用当前日志或指标独立验证。
            报告包含：告警摘要、证据、根因分析、建议动作、风险与待验证项。
            """
        ).strip()
        async for event in self.execute(task, session_id):
            yield event

    async def diagnose_manual(
        self,
        description: str,
        service_name: str | None,
        session_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        target = service_name.strip() if service_name and service_name.strip() else "未指定服务"
        task = dedent(
            f"""
            根据值班人员提供的故障现象开展手工诊断。

            目标服务：{target}
            用户描述：
            <incident_description>
            {description.strip()}
            </incident_description>

            这不是实时告警拉取任务，不得声称已经从 Prometheus 获得告警。
            可以使用当前可用的日志、指标和知识库工具补充证据；工具不可用时继续基于用户输入分析。
            输出 Markdown 报告，并明确区分：用户提供的事实、工具证据、推断、待验证假设。
            已确认的历史故障记忆只能作为相似案例和排查线索，不能充当当前故障证据；
            必须说明当前现象是否真正支持历史案例中的根因。
            报告包含：现象摘要、优先级判断、排查路径、可能原因、建议动作和验证/回滚方案。
            禁止编造不存在的查询结果。
            """
        ).strip()
        async for event in self.execute(task, session_id):
            yield event

    async def diagnose(self, session_id: str = "default") -> AsyncGenerator[dict[str, Any], None]:
        """兼容旧调用，默认执行实时诊断。"""
        async for event in self.diagnose_realtime(session_id):
            yield event

    @staticmethod
    def _format_planner_event(state: dict[str, Any] | None) -> dict[str, Any]:
        plan = (state or {}).get("plan", [])
        return {
            "type": "plan",
            "stage": "plan_created",
            "message": f"执行计划已制定，共 {len(plan)} 个步骤",
            "plan": plan,
        }

    @staticmethod
    def _format_executor_event(state: dict[str, Any] | None) -> dict[str, Any]:
        plan = (state or {}).get("plan", [])
        past_steps = (state or {}).get("past_steps", [])
        if not past_steps:
            return {"type": "status", "stage": "executor", "message": "开始执行诊断步骤"}
        last_step, _ = past_steps[-1]
        return {
            "type": "step_complete",
            "stage": "step_executed",
            "message": f"步骤执行完成 ({len(past_steps)}/{len(past_steps) + len(plan)})",
            "current_step": last_step,
            "remaining_steps": len(plan),
        }

    @staticmethod
    def _format_replanner_event(state: dict[str, Any] | None) -> dict[str, Any]:
        response = (state or {}).get("response", "")
        plan = (state or {}).get("plan", [])
        if response:
            return {
                "type": "report",
                "stage": "final_report",
                "message": "诊断报告已生成",
                "report": response,
            }
        return {
            "type": "status",
            "stage": "replanner",
            "message": "评估完成，继续执行剩余步骤" if plan else "正在生成诊断报告",
            "remaining_steps": len(plan),
        }


aiops_service = AIOpsService()
