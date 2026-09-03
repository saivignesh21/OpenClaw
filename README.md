# OpenClaw — Local Autonomous Dev Agent with Guardrail Sandbox

A local, fully self-hosted autonomous coding agent that plans and executes
code changes inside an isolated Docker sandbox, with a two-layer guardrail
system (static AST inspection + LLM-based semantic intent review) sitting
between "the agent decided to do something" and "the agent actually did it."

Built to demonstrate the pattern the industry is converging on for
agentic AI safety in 2026: orchestration + sandboxed execution + policy
enforcement, with full observability into what was blocked and why.

---

## Architecture

```
 User objective
      │
      ▼
 ┌─────────┐     ┌────────────────┐     ┌───────────────┐     ┌──────────────┐
 │  PLAN   │ ──▶ │ GENERATE ACTION │ ──▶ │ STATIC CHECK   │ ──▶ │ SEMANTIC CHECK│
 └─────────┘     └────────────────┘     └───────┬────────┘     └───────┬───────┘
                                                 │ fail                 │ fail
                                                 ▼                      ▼
                                          [REJECT + REPLAN]      [REJECT + REPLAN]
                                                                        │ pass
                                                                        ▼
                                                              ┌───────────────────┐
                                                              │ EXECUTE IN DOCKER  │
                                                              │ (no net, capped    │
                                                              │  CPU/mem, timeout) │
                                                              └─────────┬──────────┘
                                                                        ▼
                                                              ┌───────────────────┐
                                                              │     EVALUATE       │
                                                              └─────────┬──────────┘
                                                          done ◀────────┴────────▶ loop (max N iters)
```

Every step is logged to `logs/run_<timestamp>.jsonl` and rendered live in
the Streamlit dashboard, including every **rejected** action and the
reason it was rejected — that log is the actual point of this project.

---

## Stack

| Layer | Tool | Why |
|---|---|---|
| Orchestration | LangGraph | Explicit state graph — the guardrail is a real node, not a prompt suggestion |
| Local LLM | Ollama (Llama 3.1 8B / Qwen2.5-Coder 7B) | Runs fully offline, fits 8GB VRAM |
| Sandbox | Docker Desktop (WSL2 backend) | Real process/filesystem/network isolation on Windows |
| Static guardrail | Python `ast` module + shell lexer | Deterministic, fast, catches known-bad patterns |
| Semantic guardrail | Second LLM call | Catches off-task behavior that isn't a blocked keyword |
| Dashboard | Streamlit | Live view of every action attempted, allowed, or blocked |

---

## Prerequisites (Windows)

1. **Docker Desktop** — https://www.docker.com/products/docker-desktop/
   Make sure WSL2 backend is enabled (default on modern installs).
   Verify: `docker run hello-world`

2. **Ollama** — https://ollama.com/download/windows
   After installing:
   ```powershell
   ollama pull llama3.1:8b
   ollama pull qwen2.5-coder:7b
   ```

3. **Python 3.11+** — https://www.python.org/downloads/
   Verify: `python --version`

4. **Git** (optional, for the example repo) — https://git-scm.com/download/win

---

## Setup

```powershell
# 1. Extract this zip, then from the project root:
python -m venv venv
venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy the env template and adjust if needed
copy .env.example .env

# 4. Build the sandbox image (this is what the agent's code runs inside)
docker build -t openclaw-sandbox -f sandbox_image/Dockerfile.sandbox .

# 5. Make sure Ollama is running (it usually auto-starts on Windows)
ollama serve
```

---

## Running the agent

```powershell
# Run against the included broken example repo
python main.py --objective "Find the broken unit test and fix it" --repo examples/sample_repo

# Run against your own repo
python main.py --objective "Add input validation to the parse_config function" --repo C:\path\to\your\repo
```

## Running the dashboard (in a second terminal)

```powershell
venv\Scripts\activate
streamlit run src/dashboard/app.py
```

Open the URL Streamlit prints (usually `http://localhost:8501`). It reads
the same `logs/*.jsonl` files the agent writes, live, and shows:
- Every planned action
- Static check verdict + reason
- Semantic check verdict + reason
- Execution result (if it ran)
- A running "blocked vs allowed" count

---

## Project layout

```
openclaw-guardrail/
├── main.py                      # CLI entry point
├── requirements.txt
├── .env.example
├── src/
│   ├── state.py                 # LangGraph state schema
│   ├── graph.py                 # The LangGraph state machine
│   ├── llm.py                   # Ollama client wrapper
│   ├── logger.py                # JSONL structured logging
│   ├── guardrails/
│   │   ├── static_check.py      # AST + shell-lexer based rule checks
│   │   └── semantic_check.py    # LLM-based intent review
│   ├── sandbox/
│   │   └── docker_runner.py     # Disposable, isolated container execution
│   └── dashboard/
│       └── app.py               # Streamlit live monitoring UI
├── sandbox_image/
│   └── Dockerfile.sandbox       # Minimal image the agent's code runs inside
│                                 (named sandbox_image, not "docker", so it
│                                 doesn't shadow the installed docker pip package)
├── examples/
│   └── sample_repo/             # Tiny repo with one deliberately broken test
└── tests/
    └── test_static_check.py     # Unit tests for the guardrail logic itself
```

---

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Where Ollama is running |
| `PLANNER_MODEL` | `llama3.1:8b` | Model used for planning + semantic review |
| `CODER_MODEL` | `qwen2.5-coder:7b` | Model used for code generation |
| `MAX_ITERATIONS` | `8` | Hard cap so the loop can't run forever |
| `SANDBOX_TIMEOUT_SECONDS` | `30` | Per-action execution timeout |
| `SANDBOX_MEM_LIMIT` | `512m` | Container memory cap |
| `SANDBOX_NETWORK_DISABLED` | `true` | Blocks all network access from the sandbox |



## Extending it

- Swap Ollama for a hosted Claude/OpenAI API call in `src/llm.py` (interface is already provider-agnostic)
- Add a policy config file (`policies.yaml`) so blocklists are editable without touching code
- Add per-agent scoped credentials if you extend this to multi-agent (see `graph.py` — it's structured so adding a second agent node is straightforward)
- Swap Streamlit for a proper web app (FastAPI + React) once the core logic is proven

## Known limitations (be upfront about these if asked)

- Static checks use pattern + AST matching — this reduces but does not eliminate the chance of a cleverly obfuscated bypass. The Docker sandbox is the layer that has to hold if the guardrail is fooled — that's intentional defense-in-depth, not a hole to be "fixed" away.
- The semantic check depends on the local model's reasoning quality; an 8B model will occasionally miss subtle misalignment. Swapping in a larger hosted model for just this check improves reliability.
- This is a portfolio/learning project, not a hardened production security boundary — don't run it against untrusted/adversarial code without additional isolation (e.g., a VM, not just a container).
