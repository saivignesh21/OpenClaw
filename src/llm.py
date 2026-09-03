"""
Thin wrapper around the Ollama client so the rest of the codebase never
talks to a specific provider directly. Swap this file's internals to point
at a hosted API (Anthropic, OpenAI, etc.) later without touching any other
module — every caller just imports `generate()` and `generate_json()`.
"""

import json
import os
import re
import ollama
from pydantic import ValidationError

from src.state import ProposedActionModel

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
PLANNER_MODEL = os.getenv("PLANNER_MODEL", "llama3.1:8b")
CODER_MODEL = os.getenv("CODER_MODEL", "qwen2.5-coder:7b")

_client = ollama.Client(host=OLLAMA_HOST)


def generate(prompt: str, model: str, system: str | None = None, temperature: float = 0.2) -> str:
    """Single-turn text completion."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = _client.chat(
        model=model,
        messages=messages,
        options={"temperature": temperature},
    )
    return response["message"]["content"]


def generate_json(prompt: str, model: str, system: str | None = None) -> dict:
    """
    Ask the model for JSON only, then defensively strip markdown fences
    and parse. Raises ValueError with the raw text if parsing fails, so
    callers can log the bad output rather than crash silently.
    """
    strict_system = (
        (system or "")
        + "\n\nRespond with ONLY a valid JSON object. No markdown fences, "
        "no preamble, no explanation outside the JSON."
    )
    raw = generate(prompt, model=model, system=strict_system, temperature=0.1)
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Model did not return valid JSON: {e}\nRaw output:\n{raw}") from e


def plan(objective: str, history_summary: str) -> str:
    system = (
        "You are the planning module of an autonomous coding agent. "
        "Given an objective and a summary of what has been tried so far, "
        "produce a short, concrete next step. Do not write code here — "
        "just describe the next action in one or two sentences."
    )
    prompt = f"Objective: {objective}\n\nProgress so far:\n{history_summary}\n\nWhat should the next action be?"
    return generate(prompt, model=PLANNER_MODEL, system=system)


def generate_action(objective: str, plan_text: str, repo_context: str) -> dict:
    system = (
        "You are the code-generation module of an autonomous coding agent "
        "operating inside a sandboxed container. You propose exactly ONE "
        "action at a time as JSON with keys: "
        '"type" (one of "python", "shell", "file_write", "file_read"), '
        '"content" (the code/command/file content), '
        '"target_path" (only for file_write/file_read, relative path), '
        '"rationale" (why this serves the objective, one sentence).'
    )
    prompt = (
        f"Objective: {objective}\n"
        f"Current plan step: {plan_text}\n\n"
        f"Repository context:\n{repo_context}\n\n"
        "Propose the single next action as JSON."
    )
    raw_action = generate_json(prompt, model=CODER_MODEL, system=system)
    try:
        return ProposedActionModel.model_validate(raw_action).model_dump(exclude_none=True)
    except ValidationError as e:
        raise ValueError(f"Invalid action returned by model: {e}") from e
