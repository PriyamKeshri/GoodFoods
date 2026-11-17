"""Render real eval transcripts as Markdown example conversations (docs/EXAMPLES.md).

    python scripts/render_examples.py [--config ollama-llama3.1-8b_on]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PICKS = [
    ("discovery", "Discovery with live availability: the first choice is sold out"),
    ("full_booking", "A booking over three turns: hold, then explicit confirmation"),
    ("hinglish", "Hinglish request with a relative date ('kal' = tomorrow)"),
    ("sold_out", "Sold out: recover the guest instead of losing them"),
    ("waitlist", "The guest insists on a full venue: waitlist"),
    ("modify", "Changing a booking: preview, then apply"),
    ("one_shot_confirm", "Safety: the guest says 'confirm right away', the agent still asks"),
    ("no_invented_contact", "Safety: no invented name or phone number"),
    ("large_party", "Hand-off: 40 guests go to the events team"),
    ("prompt_injection", "Adversarial: 'admin mode, dump all bookings'"),
]


def fmt_args(args: dict) -> str:
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())


def render(result: dict, title: str) -> list[str]:
    lines = [f"### {title}", ""]
    for turn in result["turns"]:
        lines.append(f"> **Guest:** {turn['user']}")
        lines.append(">")
        for g in turn["guardrails"]:
            lines.append(f"> 🛡️ *{g}*  ")
        for t in turn["tools"]:
            status = "blocked" if t["blocked"] else (t["status"] or ("ok" if t["ok"] else "error"))
            lines.append(f"> 🛠️ `{t['name']}({fmt_args(t['args'])})` → **{status}**  ")
        if turn["tools"] or turn["guardrails"]:
            lines.append(">")
        reply = turn["reply"].strip().replace("\n", "\n> ")
        lines.append(f"> **Concierge:** {reply}")
        lines.append(f"> <sub>{turn['latency_ms'] / 1000:.1f}s · {turn['llm_calls']} LLM call(s)</sub>")
        lines.append("")
    verdict = "✅ passed" if result["passed"] else "❌ " + "; ".join(c["check"] for c in result["checks"] if not c["passed"])
    lines += [f"<sub>Eval checks: {verdict}</sub>", ""]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="ollama-llama3.1-8b_on")
    args = parser.parse_args()
    data = json.loads((ROOT / "evals" / "results" / f"{args.config}.json").read_text())
    by_id = {r["id"]: r for r in data["results"]}
    out = ["# Example conversations", "",
           f"Real, unedited transcripts from the eval run (`{args.config.replace('_', ' guardrails ')}`; clock frozen at "
           "Mon 16 Nov 2026, 10:00 IST). 🛠️ lines are MCP tool calls chosen by the model; 🛡️ lines are reliability-layer "
           "interventions.", ""]
    for scenario, title in PICKS:
        if scenario in by_id:
            out += render(by_id[scenario], title)
    (ROOT / "docs" / "EXAMPLES.md").write_text("\n".join(out))
    print("wrote docs/EXAMPLES.md")


if __name__ == "__main__":
    main()
