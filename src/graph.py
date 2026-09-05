"""
The LangGraph state machine.

planning -> generate_action -> static_guardrail
    -> (reject -> planning)
    -> semantic_guardrail
    -> (reject -> planning)
    -> sandbox_execute
    -> evaluate
    -> (loop -> planning | done)

Each guardrail is a real node in the graph, not a prompt instruction the
model could ignore. Rejections are logged and fed back into the next
planning step so the agent can course-correct with the actual reason it
was blocked.
"""

import os

from langgraph.graph import StateGraph, END

from src.state import AgentState
from src.llm import (
    plan as llm_plan,
    generate_action as llm_generate_action,
)
from src.guardrails import (
    run_static_check,
    run_semantic_check,
)
from src.sandbox import run_in_sandbox
from src.logger import RunLogger


MAX_ITERATIONS_DEFAULT = int(
    os.getenv("MAX_ITERATIONS", "8")
)


def _summarize_history(history: list) -> str:
    """Create a short but useful summary of previous agent attempts."""

    if not history:
        return "(nothing tried yet)"

    lines = []

    for entry in history[-5:]:
        outcome = entry["outcome"]
        action = entry.get("proposed_action", {})
        action_type = action.get("type", "unknown")

        lines.append(
            f"- iter {entry['iteration']}: "
            f"proposed {action_type} action -> {outcome}"
        )

        execution = entry.get("execution_result") or {}
        stdout = str(execution.get("stdout", "")).strip()
        stderr = str(execution.get("stderr", "")).strip()

        if stdout:
            # Keep planning context compact while preserving useful failures.
            lines.append(f"  stdout: {stdout[-2000:]}")

        if stderr:
            lines.append(f"  stderr: {stderr[-1000:]}")

        validation = entry.get("validation_result") or {}
        validation_stdout = str(validation.get("stdout", "")).strip()
        validation_stderr = str(validation.get("stderr", "")).strip()
        if validation_stdout:
            lines.append(f"  validation stdout: {validation_stdout[-3000:]}")
        if validation_stderr:
            lines.append(f"  validation stderr: {validation_stderr[-1000:]}")

    return "\n".join(lines)


def make_graph(logger: RunLogger):
    """Build and compile the OpenClaw LangGraph state machine."""

    # ---------------------------------------------------------
    # Planning node
    # ---------------------------------------------------------

    def node_plan(state: AgentState) -> AgentState:
        summary = _summarize_history(
            state["history"]
        )

        planning_context = (
            "Use the previous execution output as the primary evidence for "
            "the next step. If a test failure identifies a specific test or "
            "source file, inspect and fix that file rather than searching "
            "for unrelated log files. The sandbox working directory is the "
            "repository root (/workspace), so use paths relative to that root. "
            "Preserve the behavior and exception type asserted by the test. "
            "Do not substitute a different exception contract.\n\n"
            + summary
        )

        plan_text = llm_plan(
            state["objective"],
            planning_context,
        )

        logger.log_plan(
            state["iteration"],
            plan_text,
        )

        return {
            **state,
            "plan": plan_text,
        }

    # ---------------------------------------------------------
    # Action generation node
    # ---------------------------------------------------------

    def node_generate_action(
        state: AgentState,
    ) -> AgentState:

        repo_context = (
            f"Repository root mounted at /workspace "
            f"(host path: {state['repo_path']})"
        )

        action = llm_generate_action(
            state["objective"],
            state["plan"],
            repo_context,
        )

        logger.log_proposed_action(
            state["iteration"],
            action,
        )

        return {
            **state,
            "proposed_action": action,
        }

    # ---------------------------------------------------------
    # Static guardrail node
    # ---------------------------------------------------------

    def node_static_check(
        state: AgentState,
    ) -> AgentState:

        result = run_static_check(
            state["proposed_action"]
        )

        logger.log_static_check(
            state["iteration"],
            result.to_dict(),
        )

        return {
            **state,
            "static_check": result.to_dict(),
        }

    # ---------------------------------------------------------
    # Semantic guardrail node
    # ---------------------------------------------------------

    def node_semantic_check(
        state: AgentState,
    ) -> AgentState:

        result = run_semantic_check(
            state["objective"],
            state["proposed_action"],
        )

        logger.log_semantic_check(
            state["iteration"],
            result.to_dict(),
        )

        return {
            **state,
            "semantic_check": result.to_dict(),
        }

    # ---------------------------------------------------------
    # Sandbox execution node
    # ---------------------------------------------------------

    def node_execute(
        state: AgentState,
    ) -> AgentState:

        result = run_in_sandbox(
            state["proposed_action"],
            state["repo_path"],
        )

        logger.log_execution(
            state["iteration"],
            result,
        )

        return {
            **state,
            "execution_result": result,
        }

    # ---------------------------------------------------------
    # Evaluation node
    # ---------------------------------------------------------
    def node_evaluate(
        state: AgentState,
    ) -> AgentState:
    
        history_entry = {
            "iteration": state["iteration"],
            "proposed_action": state["proposed_action"],
            "static_check": state.get("static_check"),
            "semantic_check": state.get("semantic_check"),
            "execution_result": state.get("execution_result"),
            "outcome": "executed",
        }

        # An action completing with exit code 0 only proves that the action
        # ran. Validate the repository separately and stop only when the
        # validation command passes. Both results retain the runner's public
        # schema; no synthetic tests_passed field is introduced.
        validation_result = run_in_sandbox(
            {"type": "shell", "content": "python -m pytest -q", "rationale": "Validate the repository after the approved action."},
            state["repo_path"],
        )
        logger.log("validation", iteration=state["iteration"], result=validation_result)
        history_entry["validation_result"] = validation_result
    
        new_history = state["history"] + [history_entry]
    
        exec_result = state.get("execution_result", {})
    
        execution_succeeded = (
            exec_result.get("exit_code") == 0
            and not exec_result.get("timed_out", False)
        )
    
        validation_succeeded = (
            validation_result.get("exit_code") == 0
            and not validation_result.get("timed_out", False)
        )

        succeeded = (
            execution_succeeded
            and validation_succeeded
        )
    
        reached_limit = (
            state["iteration"] + 1
            >= state["max_iterations"]
        )
    
        done = succeeded or reached_limit
    
        summary = None
    
        if done:
    
            if succeeded:
                summary = (
                    f"Objective achieved after "
                    f"{state['iteration'] + 1} iteration(s)."
                )
            else:
                summary = (
                    f"Objective NOT fully achieved "
                    f"(max iterations reached) after "
                    f"{state['iteration'] + 1} iteration(s)."
                )
    
            logger.log_run_finished(
                summary,
                state["iteration"] + 1,
            )
    
        return {
            **state,
            "history": new_history,
            "iteration": state["iteration"] + 1,
            "done": done,
            "final_summary": summary,
        }
    
    # ---------------------------------------------------------
    # Rejection node
    # ---------------------------------------------------------

    def node_reject(
        state: AgentState,
        checker: str,
    ) -> AgentState:

        if checker == "static":
            check = state.get(
                "static_check"
            )
        else:
            check = state.get(
                "semantic_check"
            )

        reason = (
            check["reason"]
            if check
            else "unknown"
        )

        logger.log_rejection(
            state["iteration"],
            checker,
            reason,
        )

        history_entry = {
            "iteration": state["iteration"],
            "proposed_action": state["proposed_action"],
            "static_check": state.get(
                "static_check"
            ),
            "semantic_check": state.get(
                "semantic_check"
            ),
            "outcome": f"rejected_{checker}",
        }

        new_history = (
            state["history"] + [history_entry]
        )

        done = (
            state["iteration"] + 1
            >= state["max_iterations"]
        )

        return {
            **state,
            "history": new_history,
            "iteration": state["iteration"] + 1,
            "done": done,
            "final_summary": (
                "Max iterations reached with agent "
                "repeatedly proposing blocked actions."
                if done
                else None
            ),
        }

    # ---------------------------------------------------------
    # Static rejection node
    # ---------------------------------------------------------

    def node_reject_static(
        state: AgentState,
    ) -> AgentState:

        return node_reject(
            state,
            "static",
        )

    # ---------------------------------------------------------
    # Semantic rejection node
    # ---------------------------------------------------------

    def node_reject_semantic(
        state: AgentState,
    ) -> AgentState:

        return node_reject(
            state,
            "semantic",
        )

    # ---------------------------------------------------------
    # Static guardrail routing
    # ---------------------------------------------------------

    def route_after_static(
        state: AgentState,
    ) -> str:

        if not state["static_check"]["allowed"]:
            return "reject_static"

        return "semantic_guardrail"

    # ---------------------------------------------------------
    # Semantic guardrail routing
    # ---------------------------------------------------------

    def route_after_semantic(
        state: AgentState,
    ) -> str:

        if not state["semantic_check"]["allowed"]:
            return "reject_semantic"

        return "sandbox_execute"

    # ---------------------------------------------------------
    # Evaluation routing
    # ---------------------------------------------------------

    def route_after_evaluate(
        state: AgentState,
    ) -> str:

        if state["done"]:
            return END

        return "planning"

    # ---------------------------------------------------------
    # Rejection routing
    # ---------------------------------------------------------

    def route_after_reject(
        state: AgentState,
    ) -> str:

        if state["done"]:
            return END

        return "planning"

    # =========================================================
    # BUILD LANGGRAPH
    # =========================================================

    graph = StateGraph(
        AgentState
    )

    # IMPORTANT:
    #
    # AgentState already contains these state keys:
    #
    # plan
    # static_check
    # semantic_check
    # execution_result
    #
    # Therefore we deliberately use different names for the
    # LangGraph nodes.
    # =========================================================

    graph.add_node(
        "planning",
        node_plan,
    )

    graph.add_node(
        "generate_action",
        node_generate_action,
    )

    graph.add_node(
        "static_guardrail",
        node_static_check,
    )

    graph.add_node(
        "semantic_guardrail",
        node_semantic_check,
    )

    graph.add_node(
        "sandbox_execute",
        node_execute,
    )

    graph.add_node(
        "evaluate",
        node_evaluate,
    )

    graph.add_node(
        "reject_static",
        node_reject_static,
    )

    graph.add_node(
        "reject_semantic",
        node_reject_semantic,
    )

    # =========================================================
    # GRAPH EDGES
    # =========================================================

    # Start
    graph.set_entry_point(
        "planning"
    )

    # Planning -> Action generation
    graph.add_edge(
        "planning",
        "generate_action",
    )

    # Action generation -> Static guardrail
    graph.add_edge(
        "generate_action",
        "static_guardrail",
    )

    # Static guardrail routing
    graph.add_conditional_edges(
        "static_guardrail",
        route_after_static,
        {
            "reject_static": "reject_static",
            "semantic_guardrail": "semantic_guardrail",
        },
    )

    # Semantic guardrail routing
    graph.add_conditional_edges(
        "semantic_guardrail",
        route_after_semantic,
        {
            "reject_semantic": "reject_semantic",
            "sandbox_execute": "sandbox_execute",
        },
    )

    # Sandbox -> Evaluation
    graph.add_edge(
        "sandbox_execute",
        "evaluate",
    )

    # Evaluation -> Planning or END
    graph.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {
            "planning": "planning",
            END: END,
        },
    )

    # Static rejection -> Planning or END
    graph.add_conditional_edges(
        "reject_static",
        route_after_reject,
        {
            "planning": "planning",
            END: END,
        },
    )

    # Semantic rejection -> Planning or END
    graph.add_conditional_edges(
        "reject_semantic",
        route_after_reject,
        {
            "planning": "planning",
            END: END,
        },
    )

    return graph.compile()


def run_agent(
    objective: str,
    repo_path: str,
    max_iterations: int | None = None,
) -> AgentState:

    logger = RunLogger()

    max_iterations = (
        max_iterations
        or MAX_ITERATIONS_DEFAULT
    )

    initial_state: AgentState = {
        "objective": objective,
        "repo_path": repo_path,
        "iteration": 0,
        "max_iterations": max_iterations,
        "history": [],
        "done": False,
    }

    logger.log_objective(
        objective,
        repo_path,
        max_iterations,
    )

    compiled_graph = make_graph(
        logger
    )

    final_state = compiled_graph.invoke(
        initial_state,
        config={
            "recursion_limit": (
                max_iterations * 10 + 10
            )
        },
    )

    return final_state
