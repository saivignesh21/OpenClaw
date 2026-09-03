"""
CLI entry point.

Usage (Windows PowerShell):
    python main.py --objective "Fix the broken unit test" --repo examples/sample_repo
    python main.py --objective "Add input validation" --repo C:\\path\\to\\repo --max-iterations 5
"""

import argparse
import sys
from dotenv import load_dotenv

load_dotenv()

from src.graph import run_agent  # noqa: E402  (import after load_dotenv on purpose)


def main():
    parser = argparse.ArgumentParser(description="OpenClaw — local autonomous dev agent with guardrail sandbox")
    parser.add_argument("--objective", required=True, help="What the agent should accomplish")
    parser.add_argument("--repo", required=True, help="Path to the target repository")
    parser.add_argument("--max-iterations", type=int, default=None, help="Override MAX_ITERATIONS from .env")
    args = parser.parse_args()

    print(f"\n[OpenClaw] Objective: {args.objective}")
    print(f"[OpenClaw] Repo: {args.repo}")
    print("[OpenClaw] Starting agent loop... (watch the dashboard for live guardrail activity)\n")

    final_state = run_agent(args.objective, args.repo, args.max_iterations)

    print("\n" + "=" * 60)
    print("RUN COMPLETE")
    print("=" * 60)
    print(final_state.get("final_summary", "(no summary produced)"))
    print(f"\nTotal iterations: {final_state['iteration']}")
    print(f"Actions executed: {sum(1 for h in final_state['history'] if h['outcome'] == 'executed')}")
    print(f"Actions rejected (static):   {sum(1 for h in final_state['history'] if h['outcome'] == 'rejected_static')}")
    print(f"Actions rejected (semantic): {sum(1 for h in final_state['history'] if h['outcome'] == 'rejected_semantic')}")
    print("\nFull log written under logs/. Run `streamlit run src/dashboard/app.py` to inspect visually.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[OpenClaw] Interrupted by user.")
        sys.exit(1)
