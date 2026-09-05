# OpenClaw

OpenClaw is a local coding agent that plans repository changes, reviews each
action with two guardrail layers, executes approved work in a disposable Docker
sandbox, and evaluates the result by running tests.

The project is designed to be reproducible on Windows with Docker Desktop and
Ollama. The dashboard reads the agent's JSONL event log so you can follow every
proposed, rejected, and executed action.

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Safety model](#safety-model)
- [Requirements](#requirements)
- [Windows setup](#windows-setup)
- [Run OpenClaw](#run-openclaw)
- [Dashboard](#dashboard)
- [Tests and verification](#tests-and-verification)
- [Evaluation experiments](#evaluation-experiments)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

## What it does

Given an objective and a repository, OpenClaw repeatedly:

1. creates a plan;
2. generates one structured action;
3. checks the action with deterministic rules;
4. reviews the action's intent with a second local LLM call;
5. executes approved work in Docker;
6. runs validation tests in the same sandbox; and
7. either reports success or replans until the iteration limit is reached.

The agent can inspect files, write files, run shell commands, and run Python
snippets. Every action is validated before it reaches the guardrails.

## Architecture

```text
Objective
   |
   v
Plan -> Generate action -> Static guardrail -> Semantic guardrail
                                      |                 |
                                   reject              reject
                                      |                 |
                                      +----> Replan <---+
                                                        |
                                                        v
                                             Docker sandbox execution
                                                        |
                                                        v
                                             Test validation / evaluation
                                                        |
                                  success <--------------+--------------> replan
```

The graph is implemented in [`src/graph.py`](src/graph.py). Run events are
written to `logs/run_<timestamp>.jsonl`.

## Safety model

OpenClaw uses defense in depth:

- **Action schema:** Pydantic validates the action type, path, rationale, and
  content limits before execution.
- **Static guardrail:** AST inspection checks Python actions; shell and path
  rules catch known-dangerous patterns and workspace escapes.
- **Semantic guardrail:** a separate Ollama review checks whether the action
  matches the objective. Invalid, unavailable, timed-out, or unexpected
  reviewer responses fail closed.
- **Docker sandbox:** approved actions run with networking disabled, resource
  limits, a non-root user, dropped capabilities, `no-new-privileges`, a process
  limit, and cleanup after each action.

The sandbox image lives in `sandbox_image/`. That directory name is deliberate:
using `docker/` beside the source can shadow Python's installed `docker`
package during imports.

## Requirements

- Windows 10/11
- Python 3.11 or newer
- Docker Desktop with the WSL2 backend enabled
- Ollama for local planning, coding, and semantic review
- Git (recommended)

Install Docker Desktop from [docker.com](https://www.docker.com/products/docker-desktop/),
Ollama from [ollama.com](https://ollama.com/download/windows), and Python from
[python.org](https://www.python.org/downloads/).

## Windows setup

Open PowerShell in the repository root:

```powershell
python --version
docker version
ollama --version

python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env

docker build -t openclaw-sandbox -f sandbox_image/Dockerfile.sandbox .
ollama pull llama3.1:8b
ollama pull qwen2.5-coder:7b
```

If PowerShell blocks activation, run this once in an elevated PowerShell:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Ollama normally starts with Windows. If it is not running, start it in a
separate terminal with `ollama serve`.

## Run OpenClaw

The included sample repository contains a deliberately broken calculator test.
Run the agent against it with:

```powershell
venv\Scripts\Activate.ps1
python main.py --objective "Find the broken unit test and fix it" --repo examples/sample_repo
```

Run it against another local repository by supplying an absolute path:

```powershell
python main.py --objective "Add input validation to parse_config" --repo C:\path\to\repo
```

The terminal prints the final outcome. Detailed events remain in `logs/`.

## Dashboard

Start the dashboard in a second PowerShell window from the project root:

```powershell
venv\Scripts\Activate.ps1
streamlit run src/dashboard/app.py
```

Open the local URL printed by Streamlit, normally
`http://localhost:8501`. The dashboard refreshes the JSONL log and displays:

- the current run and iteration;
- proposed actions and their rationales;
- static and semantic verdicts with reasons;
- sandbox output, errors, and validation results; and
- allowed, blocked, and executed action counts.

## Tests and verification

The pure-Python test suite does not require Ollama or Docker:

```powershell
venv\Scripts\Activate.ps1
python -m pytest -q tests/
```

The sample repository is expected to fail before the agent repairs it. After a
successful agent run, verify it separately:

```powershell
python -m pytest -q examples/sample_repo/
```

GitHub Actions also compiles the source, runs `tests/`, and builds the Docker
sandbox image. The sample repository is intentionally excluded from CI because
its broken test is part of the demonstration fixture.

## Evaluation experiments

The benchmark runner creates an isolated workspace for each task and records
machine-readable results. Run one or more coder models like this:

```powershell
python scripts/run_evaluation.py --models qwen2.5-coder:7b --max-iterations 4
```

Results are written to `evaluation/results/` (ignored by Git):

- `results.json` and `results.csv` contain task-level records;
- `summary.json` contains success rate, iterations, runtime, actions, and
  blocked-action totals; and
- checkpoints allow an interrupted run to resume.

The repository includes 20 coding-task fixtures. Add task objects to
`evaluation/tasks.json` when expanding the benchmark.

To evaluate the labelled guardrail fixtures without running the coding loop:

```powershell
python scripts/run_evaluation.py --guardrail-only --models llama3.1:8b
```

This reports precision, recall, F1, false positives, and false negatives for
the static and semantic layers. Treat generated metrics as experiment output;
rerun the command after changing models, prompts, or policies.

### Measured guardrail results

The repository's recorded guardrail experiment evaluated 20 labelled fixtures
with each of two local models (`llama3.1:8b` and `qwen2.5-coder:7b`), for 40
evaluations total. The expected label is whether the action should be allowed.

| Checker | Evaluations | Precision | Recall | F1 | False-positive rate |
|---|---:|---:|---:|---:|---:|
| Static | 40 | 71.43% | 100.00% | 83.33% | 40.00% |
| Semantic | 40 | 100.00% | 100.00% | 100.00% | 0.00% |

Confusion counts for the same run:

| Checker | True positives | True negatives | False positives | False negatives |
|---|---:|---:|---:|---:|
| Static | 20 | 12 | 8 | 0 |
| Semantic | 20 | 20 | 0 | 0 |

These are benchmark observations, not security guarantees. The raw records and
summary are generated locally in `evaluation/results/` and are intentionally
ignored by Git. Re-run the command above to reproduce or update the numbers.

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint |
| `PLANNER_MODEL` | `llama3.1:8b` | Planner and semantic-review model |
| `CODER_MODEL` | `qwen2.5-coder:7b` | Action-generation model |
| `MAX_ITERATIONS` | `8` | Maximum graph iterations |
| `SANDBOX_TIMEOUT_SECONDS` | `30` | Per-action timeout |
| `SANDBOX_MEM_LIMIT` | `512m` | Container memory limit |
| `SANDBOX_NETWORK_DISABLED` | `true` | Disable sandbox networking |

## Project layout

```text
OpenClaw/
├── main.py
├── requirements.txt
├── .env.example
├── src/
│   ├── graph.py                  # LangGraph state machine
│   ├── state.py                  # Typed graph state and history
│   ├── llm.py                    # Ollama and action-schema integration
│   ├── logger.py                 # Structured JSONL logging
│   ├── evaluation.py             # Benchmark metrics and result writers
│   ├── guardrails/
│   │   ├── static_check.py       # AST, shell, and path checks
│   │   └── semantic_check.py     # LLM intent review
│   ├── sandbox/docker_runner.py  # Disposable Docker execution
│   └── dashboard/app.py          # Streamlit monitoring dashboard
├── sandbox_image/Dockerfile.sandbox
├── examples/sample_repo/         # Deliberately broken demo repository
├── evaluation/                   # Tasks and labelled guardrail fixtures
├── scripts/run_evaluation.py
└── tests/                        # Guardrail and metric unit tests
```

## Troubleshooting

**`docker version` cannot connect:** start Docker Desktop and wait until its
status is “Running”. Confirm that WSL2 integration is enabled.

**Ollama connection errors:** start `ollama serve`, verify
`http://localhost:11434`, and confirm both models were pulled.

**The dashboard is empty:** run the agent once from the project root and check
that a `logs/*.jsonl` file exists. The dashboard reads logs from the current
working directory.

**The sample test still fails:** that is expected before the agent runs. Use
`python -m pytest -q tests/` for the guardrail suite, then rerun the agent and
validate `examples/sample_repo/`.

**A benchmark stops part-way through:** rerun the same command. The evaluation
checkpoint is designed to continue completed model/task combinations.

## Limitations

Static checks are pattern- and AST-based, so they cannot guarantee detection of
every obfuscated payload. The Docker sandbox is the containment layer if a
guardrail misses something.

Semantic review quality depends on the selected local model. A larger reviewer
model may make better intent decisions, but no LLM review should be treated as
a complete security boundary.

Do not run this project against untrusted adversarial code without additional
isolation such as a VM or a separately secured host.

## License

See [LICENSE](LICENSE).
