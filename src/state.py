"""
Shared state object passed between every node in the LangGraph graph.

Keeping this as an explicit, typed schema (rather than a free-form dict)
means every node's inputs/outputs are self-documenting, and the dashboard
can deserialize logged states without guessing at shape.
"""

from __future__ import annotations
from typing import TypedDict, Literal, Optional
from typing_extensions import NotRequired
from pydantic import BaseModel, ConfigDict, Field, model_validator


ActionType = Literal["python", "shell", "file_write", "file_read"]


class ProposedAction(TypedDict):
    """A single action the agent wants to take, before it is validated."""
    type: ActionType
    content: str            # code, shell command, or file content
    target_path: NotRequired[str]   # relevant for file_write/file_read
    rationale: str           # why the agent believes this serves the objective


class ProposedActionModel(BaseModel):
    """Strict boundary for untrusted action JSON returned by the LLM."""

    model_config = ConfigDict(extra="forbid")

    type: ActionType
    content: str = Field(max_length=200_000)
    target_path: str | None = None
    rationale: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_target_path(self):
        needs_path = self.type in {"file_write", "file_read"}
        if needs_path and not self.target_path:
            raise ValueError(f"{self.type} actions require target_path")
        if not needs_path and self.target_path is not None:
            raise ValueError(f"{self.type} actions must not include target_path")
        return self


class CheckResult(TypedDict):
    allowed: bool
    reason: str
    checker: str             # "static" | "semantic"


class ExecutionResult(TypedDict):
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool


class HistoryEntry(TypedDict):
    iteration: int
    proposed_action: ProposedAction
    static_check: NotRequired[CheckResult]
    semantic_check: NotRequired[CheckResult]
    execution_result: NotRequired[ExecutionResult]
    validation_result: NotRequired[ExecutionResult]
    outcome: str              # "executed" | "rejected_static" | "rejected_semantic" | "error"


class AgentState(TypedDict):
    objective: str
    repo_path: str
    iteration: int
    max_iterations: int
    plan: NotRequired[str]
    proposed_action: NotRequired[ProposedAction]
    static_check: NotRequired[CheckResult]
    semantic_check: NotRequired[CheckResult]
    execution_result: NotRequired[ExecutionResult]
    history: list[HistoryEntry]
    done: bool
    final_summary: NotRequired[str]
