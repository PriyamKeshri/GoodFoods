"""Terminal chat with the concierge (handy for quick tests and for reviewers without a browser).

    python -m goodfoods.cli                       # default model (LLM_PROFILE or local Llama 3.1 8B)
    python -m goodfoods.cli --model ollama-qwen3-8b --trace
    python -m goodfoods.cli --no-guardrails       # see how the raw 8B model behaves
    python -m goodfoods.cli -m "hi" -m "Italian in Indiranagar tomorrow 8pm for 2"   # scripted turns
"""

from __future__ import annotations

import argparse
import json

from goodfoods.agent.core import ConciergeAgent, TraceEvent
from goodfoods.agent.llm import DEFAULT_PROFILE, PROFILES, LLMClient
from goodfoods.agent.mcp_client import MCPStdioClient
from goodfoods.agent.telemetry import Telemetry

DIM, BOLD, CYAN, YELLOW, GREEN, RED, END = "\033[2m", "\033[1m", "\033[36m", "\033[33m", "\033[32m", "\033[31m", "\033[0m"


def show(event: TraceEvent) -> None:
    if event.kind == "tool":
        ok = GREEN + "ok" if event.detail.get("ok") else RED + "error"
        result = event.detail.get("result")
        preview = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
        print(f"{DIM}  ↳ {CYAN}{event.title}{END}{DIM}({json.dumps(event.detail.get('args'), ensure_ascii=False)}) "
              f"{ok}{END}{DIM} {event.ms:.0f}ms\n     {preview[:260]}{END}")
    elif event.kind == "guardrail":
        print(f"{YELLOW}  ⚑ {event.title}{END} {DIM}{json.dumps(event.detail, ensure_ascii=False)[:240]}{END}")
    elif event.kind == "llm":
        print(f"{DIM}  · {event.title}: {event.detail.get('decision')} ({event.ms:.0f}ms, "
              f"{event.detail.get('prompt_tokens')}+{event.detail.get('completion_tokens')} tok){END}")
    else:
        print(f"{RED}  ✖ {event.title}: {event.detail}{END}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the GoodFoods concierge")
    parser.add_argument("--model", default=DEFAULT_PROFILE, choices=list(PROFILES))
    parser.add_argument("--no-guardrails", action="store_true")
    parser.add_argument("--trace", action="store_true", help="show LLM calls, tool calls and guardrails")
    parser.add_argument("-m", "--message", action="append", help="scripted user message (repeatable)")
    args = parser.parse_args()

    llm = LLMClient(args.model)
    ready, status = llm.check()
    print(f"{BOLD}GoodFoods Concierge{END} · {llm.profile.label} · {status}")
    with MCPStdioClient() as mcp:
        agent = ConciergeAgent(mcp, llm, guardrails=not args.no_guardrails, telemetry=Telemetry())
        print(f"{DIM}MCP: {mcp.server_info.get('name')} {mcp.server_info.get('version')} · "
              f"{len(agent.specs)} tools · protocol {mcp.protocol_version}{END}\n")
        inputs = iter(args.message) if args.message else None
        while True:
            try:
                text = next(inputs) if inputs else input(f"{BOLD}you ›{END} ")
            except (StopIteration, EOFError, KeyboardInterrupt):
                print()
                break
            if inputs:
                print(f"{BOLD}you ›{END} {text}")
            if not text.strip():
                continue
            result = agent.chat(text, on_event=show if args.trace else None)
            print(f"{BOLD}{GREEN}concierge ›{END} {result.reply}")
            print(f"{DIM}  [{result.latency_ms / 1000:.1f}s · {result.llm_calls} LLM · {len(result.tools)} tools · "
                  f"{len(result.guardrail_events)} guardrail]{END}\n")


if __name__ == "__main__":
    main()
