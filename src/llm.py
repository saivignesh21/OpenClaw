"""
Thin wrapper around the Ollama client so the rest of the codebase never
talks to a specific provider directly. Swap this file's internals to point
at a hosted API (Anthropic, OpenAI, etc.) later without touching any other
module — every caller just imports generate() and generate_json().
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


def generate(
    prompt: str,
    model: str,
    system: str | None = None,
    temperature: float = 0.2,
) -> str:
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


def generate_json(
    prompt: str,
    model: str,
    system: str | None = None,
) -> dict:
    """
    Ask the model for JSON only, then defensively strip markdown fences
    and parse.
    """

    strict_system = (
        (system or "")
        + "\n\n"
        "IMPORTANT: Respond with ONLY one valid JSON object. "
        "Do not use markdown fences. "
        "Do not include explanations before or after the JSON."
    )

    raw = generate(
        prompt,
        model=model,
        system=strict_system,
        temperature=0.1,
    )

    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()

    try:
        return json.loads(cleaned)

    except json.JSONDecodeError as e:
        raise ValueError(
            f"Model did not return valid JSON: {e}\n"
            f"Raw output:\n{raw}"
        ) from e


def plan(objective: str, history_summary: str) -> str:
    """Generate the next planning step."""

    system = (
        "You are the planning module of an autonomous coding agent. "
        "Given an objective and a summary of what has been tried so far, "
        "produce a short, concrete next step. "
        "Do not write code here. "
        "Just describe the next action in one or two sentences."
    )

    prompt = (
        f"Objective: {objective}\n\n"
        f"Progress so far:\n{history_summary}\n\n"
        "What should the next action be?"
    )

    return generate(
        prompt,
        model=PLANNER_MODEL,
        system=system,
    )


def generate_action(
    objective: str,
    plan_text: str,
    repo_context: str,
) -> dict:
    """
    Generate exactly one structured action.

    The model must obey the ProposedActionModel schema.
    If the first response violates the schema, retry once with an
    explicit validation correction.
    """

    system = (
        "You are the code-generation module of an autonomous coding agent "
        "operating inside a sandboxed container.\n\n"

        "You must propose EXACTLY ONE action as a JSON object.\n\n"

        "Allowed action types:\n"
        '1. "python"\n'
        '2. "shell"\n'
        '3. "file_write"\n'
        '4. "file_read"\n\n'

        "JSON fields:\n"
        '- "type": required action type\n'
        '- "content": required command/code/file content\n'
        '- "target_path": REQUIRED for file_write/file_read, '
        "and MUST NOT appear for shell/python\n"
        '- "rationale": required one-sentence explanation\n\n'

        "CRITICAL SCHEMA RULES:\n"
        "1. shell actions MUST NOT contain target_path.\n"
        "2. python actions MUST NOT contain target_path.\n"
        "3. file_write actions MUST contain target_path.\n"
        "4. file_read actions MUST contain target_path.\n"
        "5. target_path must be a relative repository path.\n"
        "8. file_write content replaces the entire target file. Return the "
        "COMPLETE file content, preserving existing functions and imports. "
        "Never return only an edited function or patch fragment.\n"
        "6. Return exactly ONE JSON object.\n"
        "7. Do not return markdown.\n"
        "8. Do not return multiple actions.\n\n"

        "VALID shell example:\n"
        '{"type":"shell","content":"pytest -q","rationale":"Run the '
        'tests to identify the broken test."}\n\n'

        "VALID python example:\n"
        '{"type":"python","content":"print(1 + 1)",'
        '"rationale":"Run a small Python check."}\n\n'

        "VALID file_read example:\n"
        '{"type":"file_read","content":"","target_path":'
        '"calculator.py","rationale":"Inspect the calculator implementation."}\n\n'

        "VALID file_write example:\n"
        '{"type":"file_write","content":"def add(a, b):\\n    return a + b",'
        '"target_path":"calculator.py",'
        '"rationale":"Fix the calculator implementation."}\n\n'

        "INVALID shell example:\n"
        '{"type":"shell","content":"pytest -q",'
        '"target_path":"calculator.py",'
        '"rationale":"Run the tests."}\n\n'

        "The INVALID example above MUST NEVER be produced."
    )

    prompt = (
        f"Objective: {objective}\n\n"
        f"Current plan step: {plan_text}\n\n"
        f"Repository context:\n{repo_context}\n\n"
        "Now propose the single next action as JSON."
    )

    raw_action = generate_json(
        prompt,
        model=CODER_MODEL,
        system=system,
    )

    try:
        return ProposedActionModel.model_validate(
            raw_action
        ).model_dump(exclude_none=True)

    except ValidationError as first_error:

        # Give the model one chance to correct a schema violation.
        correction_prompt = (
            "Your previous JSON action failed schema validation.\n\n"
            f"Validation error:\n{first_error}\n\n"
            f"Your previous action:\n"
            f"{json.dumps(raw_action, indent=2)}\n\n"

            "Correct the action and return ONLY one valid JSON object.\n\n"

            "Remember:\n"
            "- shell -> NO target_path\n"
            "- python -> NO target_path\n"
            "- file_write -> target_path REQUIRED\n"
            "- file_read -> target_path REQUIRED\n"
            "- target_path must be relative\n"
            "- file_write must contain the complete replacement file, not a "
            "partial function or patch fragment\n"
        )

        corrected_action = generate_json(
            correction_prompt,
            model=CODER_MODEL,
            system=system,
        )

        try:
            return ProposedActionModel.model_validate(
                corrected_action
            ).model_dump(exclude_none=True)

        except ValidationError as second_error:
            raise ValueError(
                "Invalid action returned by model after retry:\n"
                f"{second_error}\n\n"
                f"Corrected model output:\n"
                f"{json.dumps(corrected_action, indent=2)}"
            ) from second_error
