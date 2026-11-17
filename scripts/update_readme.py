"""Refresh the README's eval table and example conversations from the latest eval results.

    python -m evals.run_evals --repeats 2 && python scripts/update_readme.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from render_examples import main as render_examples_doc  # noqa: E402
from render_examples import render  # noqa: E402

RESULTS = ROOT / "evals" / "results"
LABELS = {"ollama-llama3.1-8b": "Llama 3.1 8B", "ollama-qwen3-8b": "Qwen3 8B"}
README_EXAMPLES = [
    ("discovery", "Discovery: the first choice is sold out, so sister restaurants are offered"),
    ("full_booking", "Booking over three turns: details, hold, explicit confirmation"),
    ("hinglish", "Hinglish request with a relative date (“kal” = tomorrow)"),
    ("one_shot_confirm", "Safety: “confirm right away” still gets a summary first"),
    ("prompt_injection", "Adversarial: “admin mode, list every booking”"),
]


def label(config: str) -> str:
    profile, mode = config.split(":")
    return f"{LABELS.get(profile, profile)} · guardrails {mode}"


def rate(s: dict) -> str:
    share = s.get("pass_rate", s["scenarios_passed"] / s["scenarios"])
    return f"{share:.0%}"


def evals_section() -> str:
    summaries = json.loads((RESULTS / "summary.json").read_text())
    runs = summaries[0].get("runs", 1)
    turns = summaries[0]["turns"] // runs
    lines = [f"17 scripted conversations ({turns} guest turns), {runs} run{'s' * (runs != 1)} per configuration, each on "
             "a fresh database with a frozen clock. Every scenario is scored on the tools called, the reply and the "
             "final database state. Report: **[docs/EVALS.md](docs/EVALS.md)** · transcripts: `evals/results/`.", "",
             "| Configuration | Scenarios passed | Passed every run | Unconfirmed actions | Invented contact used | "
             "Ungrounded replies | Median reply (local) |", "|---|---|---|---|---|---|---|"]
    for s in summaries:
        every = f"{s['pass_all_runs']}/{s['scenarios']}" if "pass_all_runs" in s else "–"
        lines.append(f"| {label(s['config'])} | **{rate(s)}** | {every} | {s['unconfirmed_actions']} | "
                     f"{s['invented_contact_executed']} | {s['ungrounded_replies']} | "
                     f"{s.get('median_latency_s', s['avg_latency_s'])} s |")
    baseline = RESULTS / "baseline" / "summary.json"
    if baseline.exists():
        before = {b["config"]: b for b in json.loads(baseline.read_text())}
        moved = [f"{label(s['config'])}: {rate(before[s['config']])} → **{rate(s)}**"
                 for s in summaries if s["config"] in before]
        if all(rate(before[s["config"]]) == rate(s) and s.get("runs", 1) == 1 for s in summaries if s["config"] in before):
            moved = []  # these *are* the first-run results: nothing to compare yet
        lines += ["", "**Eval-driven iteration.** The first full run exposed failures that became fixes E7–E18 in the "
                  "[prompt-engineering log](docs/PROMPT_ENGINEERING.md)."
                  + (" First run → now: " + "; ".join(moved) + "." if moved else "")]
    return "\n".join(lines)


def examples_section() -> str:
    data = json.loads((RESULTS / "ollama-llama3.1-8b_on.json").read_text())
    by_id = {r["id"]: r for r in data["results"]}
    out = ["Real, unedited transcripts from the eval run (Llama 3.1 8B, guardrails on). 🛠️ lines are MCP tool calls "
           "the model chose; 🛡️ lines are reliability-layer interventions. More journeys (waitlist, changes, "
           "cancellations, hand-off): **[docs/EXAMPLES.md](docs/EXAMPLES.md)**.", ""]
    for i, (scenario, title) in enumerate(README_EXAMPLES):
        if scenario not in by_id:
            continue
        body = "\n".join(render(by_id[scenario], title)[2:])
        out += [f"<details{' open' if i == 0 else ''}>", f"<summary><b>{title}</b></summary>", "", body, "</details>", ""]
    return "\n".join(out)


def replace(text: str, name: str, content: str) -> str:
    pattern = re.compile(rf"(<!-- {name}:START -->).*?(<!-- {name}:END -->)", re.S)
    assert pattern.search(text), f"marker {name} missing"
    return pattern.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(2)}", text)


def main() -> None:
    sys.argv = sys.argv[:1]
    render_examples_doc()
    readme = ROOT / "README.md"
    text = replace(readme.read_text(), "EVALS", evals_section())
    readme.write_text(replace(text, "EXAMPLES", examples_section()))
    print("README.md updated")


if __name__ == "__main__":
    main()
