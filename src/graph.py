"""
The LangGraph state machine.

plan -> generate_action -> static_check -> (reject -> plan) | semantic_check -> (reject -> plan) | execute -> evaluate -> (loop | done)

Each guardrail is a real node in the graph, not a prompt instruction the
model could ignore. Rejections are logged and fed back into the next
planning step so the agent can course-correct with the actual reason it
was blocked.
"""

import os
from langgraph.graph import StateGraph, END

from src.state import AgentState
from src.llm import plan as llm_plan, generate_action as llm_generate_action
from src.guardrails import run_static_check, run_semantic_check
from src.sandbox import run_in_sandbox
from src.logger import RunLogger

MAX_ITERATIONS_DEFAULT = int(os.getenv("MAX_ITERATIONS", "8"))


def _summarize_history(history: list) -> str:
    if not history:
        return "(nothing tried yet)"
    lines = []
    for entry in history[-5:]:  # keep prompt short: last 5 steps only
        outcome = entry["outcome"]
        action_type = entry["proposed_action"]["type"]
        lines.append(f"- iter {entry['iteration']}: proposed {action_type} action -> {outcome}")
    return "\n".join(lines)


def make_graph(logger: RunLogger):

    def node_plan(state: AgentState) -> AgentState:
        summary = _summarize_history(state["history"])
        plan_text = llm_plan(state["objective"], summary)
        logger.log_plan(state["iteration"], plan_text)
        return {**state, "plan": plan_text}

    def node_generate_action(state: AgentState) -> AgentState:
        repo_context = f"Repository root mounted at /workspace (host path: {state['repo_path']})"
        action = llm_generate_action(state["objective"], state["plan"], repo_context)
        logger.log_proposed_action(state["iteration"], action)
        return {**state, "proposed_action": action}

    def node_static_check(state: AgentState) -> AgentState:
        result = run_static_check(state["proposed_action"])
        logger.log_static_check(state["iteration"], result.to_dict())
        return {**state, "static_check": result.to_dict()}

    def node_semantic_check(state: AgentState) -> AgentState:
        result = run_semantic_check(state["objective"], state["proposed_action"])
        logger.log_semantic_check(state["iteration"], result.to_dict())
        return {**state, "semantic_check": result.to_dict()}

    def node_execute(state: AgentState) -> AgentState:
        result = run_in_sandbox(state["proposed_action"], state["repo_path"])
        logger.log_execution(state["iteration"], result)
        return {**state, "execution_result": result}

    def node_evaluate(state: AgentState) -> AgentState:
        history_entry = {
            "iteration": state["iteration"],
            "proposed_action": state["proposed_action"],
            "static_check": state.get("static_check"),
            "semantic_check": state.get("semantic_check"),
            "execution_result": state.get("execution_result"),
            "outcome": "executed",
        }
        new_history = state["history"] + [history_entry]

        exec_result = state.get("execution_result", {})
        succeeded = exec_result.get("exit_code") == 0 and not exec_result.get("timed_out")

        done = succeeded or (state["iteration"] + 1 >= state["max_iterations"])
        summary = None
        if done:
            summary = (
                f"Objective {'achieved' if succeeded else 'NOT fully achieved (max iterations reached)'} "
                f"after {state['iteration'] + 1} iteration(s)."
            )
            logger.log_run_finished(summary, state["iteration"] + 1)

        return {
            **state,
            "history": new_history,
            "iteration": state["iteration"] + 1,
            "done": done,
            "final_summary": summary,
        }

    def node_reject(state: AgentState, checker: str) -> AgentState:
        check = state.get("static_check") if checker == "static" else state.get("semantic_check")
        reason = check["reason"] if check else "unknown"
        logger.log_rejection(state["iteration"], checker, reason)

        history_entry = {
            "iteration": state["iteration"],
            "proposed_action": state["proposed_action"],
            "static_check": state.get("static_check"),
            "semantic_check": state.get("semantic_check"),
            "outcome": f"rejected_{checker}",
        }
        new_history = state["history"] + [history_entry]
        done = state["iteration"] + 1 >= state["max_iterations"]

        return {
            **state,
            "history": new_history,
            "iteration": state["iteration"] + 1,
            "done": done,
            "final_summary": "Max iterations reached with agent repeatedly proposing blocked actions." if done else None,
        }

    def node_reject_static(state: AgentState) -> AgentState:
        return node_reject(state, "static")

    def node_reject_semantic(state: AgentState) -> AgentState:
        return node_reject(state, "semantic")

    def route_after_static(state: AgentState) -> str:
        if not state["static_check"]["allowed"]:
            return "reject_static"
        return "semantic_check"

    def route_after_semantic(state: AgentState) -> str:
        if not state["semantic_check"]["allowed"]:
            return "reject_semantic"
        return "execute"

    def route_after_evaluate(state: AgentState) -> str:
        return END if state["done"] else "plan"

    def route_after_reject(state: AgentState) -> str:
        return END if state["done"] else "plan"

    graph = StateGraph(AgentState)
    graph.add_node("plan", node_plan)
    graph.add_node("generate_action", node_generate_action)
    graph.add_node("static_check", node_static_check)
    graph.add_node("semantic_check", node_semantic_check)
    graph.add_node("execute", node_execute)
    graph.add_node("evaluate", node_evaluate)
    graph.add_node("reject_static", node_reject_static)
    graph.add_node("reject_semantic", node_reject_semantic)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "generate_action")
    graph.add_edge("generate_action", "static_check")
    graph.add_conditional_edges("static_check", route_after_static, {
        "reject_static": "reject_static",
        "semantic_check": "semantic_check",
    })
    graph.add_conditional_edges("semantic_check", route_after_semantic, {
        "reject_semantic": "reject_semantic",
        "execute": "execute",
    })
    graph.add_edge("execute", "evaluate")
    graph.add_conditional_edges("evaluate", route_after_evaluate, {"plan": "plan", END: END})
    graph.add_conditional_edges("reject_static", route_after_reject, {"plan": "plan", END: END})
    graph.add_conditional_edges("reject_semantic", route_after_reject, {"plan": "plan", END: END})

    return graph.compile()


def run_agent(objective: str, repo_path: str, max_iterations: int | None = None) -> AgentState:
    logger = RunLogger()
    max_iterations = max_iterations or MAX_ITERATIONS_DEFAULT

    initial_state: AgentState = {
        "objective": objective,
        "repo_path": repo_path,
        "iteration": 0,
        "max_iterations": max_iterations,
        "history": [],
        "done": False,
    }

    logger.log_objective(objective, repo_path, max_iterations)
    compiled_graph = make_graph(logger)
    final_state = compiled_graph.invoke(initial_state, config={"recursion_limit": max_iterations * 10 + 10})
    return final_state
