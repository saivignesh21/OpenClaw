"""
Executes an already-approved action inside a disposable Docker container.

Non-negotiable defaults:
  - network_disabled=True   -> no exfiltration, no pulling extra payloads
  - mem_limit / cpu_quota   -> a runaway process can't take down the host
  - hard timeout            -> an infinite loop doesn't hang the pipeline
  - only the target repo dir is mounted, nothing else on the host
  - container is force-removed after every single action (no shared state
    between actions that could be used to smuggle something through)
"""

import os
import base64
import docker
from docker.errors import ContainerError, ImageNotFound, APIError, DockerException

SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "openclaw-sandbox")
TIMEOUT_SECONDS = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "30"))
MEM_LIMIT = os.getenv("SANDBOX_MEM_LIMIT", "512m")
CPU_QUOTA = int(os.getenv("SANDBOX_CPU_QUOTA", "50000"))
NETWORK_DISABLED = os.getenv("SANDBOX_NETWORK_DISABLED", "true").lower() == "true"

_client = None


def get_client():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def _build_command(action: dict) -> list[str]:
    action_type = action["type"]
    if action_type == "python":
        return ["python", "-c", action["content"]]
    if action_type == "shell":
        return ["sh", "-c", action["content"]]
    if action_type == "file_write":
        target = action["target_path"]
        # Base64 avoids quoting and newline edge cases in generated content.
        encoded = base64.b64encode(action["content"].encode("utf-8")).decode("ascii")
        py = (
            "import ast, base64, pathlib; "
            f"p=pathlib.Path({target!r}); new=base64.b64decode({encoded!r}).decode('utf-8'); "
            "old=p.read_text(encoding='utf-8') if p.exists() else ''; "
            "ot=ast.parse(old) if old else None; nt=ast.parse(new); "
            "od={n.name:n for n in (ot.body if ot else []) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}; "
            "nd={n.name:n for n in nt.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}; "
            "lines=old.splitlines(keepends=True); "
            "[(lines.__setitem__(slice(od[name].lineno-1,od[name].end_lineno), new.splitlines(keepends=True)[nd[name].lineno-1:nd[name].end_lineno])) for name in nd if name in od and len(nd)<len(od)]; "
            "p.write_text(''.join(lines) if old and nd and len(nd)<len(od) and any(name in od for name in nd) else new, encoding='utf-8')"
        )
        return ["python", "-c", py]
    if action_type == "file_read":
        target = action["target_path"]
        py = f"print(pathlib.Path({target!r}).read_text(encoding='utf-8'))"
        py = "import pathlib; " + py
        return ["python", "-c", py]
    raise ValueError(f"Unsupported action type for execution: {action_type}")


def run_in_sandbox(action: dict, repo_path: str) -> dict:
    """
    Runs one approved action inside a fresh container.
    Returns a dict matching the ExecutionResult schema in state.py.
    """
    repo_path = os.path.abspath(repo_path)
    command = _build_command(action)

    container = None
    try:
        client = get_client()
        container = client.containers.run(
            image=SANDBOX_IMAGE,
            command=command,
            volumes={repo_path: {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            network_disabled=NETWORK_DISABLED,
            mem_limit=MEM_LIMIT,
            cpu_period=100000,
            cpu_quota=CPU_QUOTA,
            user="1000:1000",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            pids_limit=128,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
            detach=True,
            stdout=True,
            stderr=True,
        )

        try:
            result = container.wait(timeout=TIMEOUT_SECONDS)
            exit_code = result.get("StatusCode", -1)
            timed_out = False
        except Exception:
            container.kill()
            exit_code = None
            timed_out = True

        logs = container.logs(stdout=True, stderr=False).decode(errors="replace")
        errs = container.logs(stdout=False, stderr=True).decode(errors="replace")

        return {
            "exit_code": exit_code,
            "stdout": logs,
            "stderr": errs,
            "timed_out": timed_out,
        }

    except ImageNotFound:
        return {
            "exit_code": None,
            "stdout": "",
            "stderr": f"Sandbox image '{SANDBOX_IMAGE}' not found. Build it first: "
                      f"docker build -t {SANDBOX_IMAGE} -f sandbox_image/Dockerfile.sandbox .",
            "timed_out": False,
        }
    except (ContainerError, APIError, DockerException, OSError) as e:
        return {"exit_code": None, "stdout": "", "stderr": f"Docker error: {e}", "timed_out": False}
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
