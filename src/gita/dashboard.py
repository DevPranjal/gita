"""Static HTML dashboard.

No server and no CDN: the file opens from disk with the network unplugged, which
is both the point of the project and a requirement for demoing it offline.

Every cost figure is rendered next to a quality figure. A dashboard that shows
only savings invites the reader to assume the answers were as good, and they may
not have been.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .telemetry import summarise as summarise_telemetry
from .eval.score import summarise_runs

_CSS = """
:root {
  --bg:#fafaf8; --ink:#1c1c1a; --muted:#6b6b63; --line:#e3e3dc;
  --card:#ffffff; --good:#1a7f5a; --bad:#b3462f; --accent:#c8912a;
}
* { box-sizing:border-box; }
body { margin:0; padding:40px 32px 72px; background:var(--bg); color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
main { max-width:1080px; margin:0 auto; }
h1 { font-size:28px; margin:0 0 4px; letter-spacing:-0.02em; }
h2 { font-size:15px; text-transform:uppercase; letter-spacing:0.08em;
  color:var(--muted); margin:40px 0 12px; font-weight:600; }
.sub { color:var(--muted); margin:0 0 32px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:18px 20px; }
.card .label { font-size:12px; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted); }
.card .value { font-size:30px; font-weight:600; margin-top:6px; letter-spacing:-0.02em; }
.card .note { font-size:12px; color:var(--muted); margin-top:4px; }
.good { color:var(--good); } .bad { color:var(--bad); }
table { width:100%; border-collapse:collapse; background:var(--card);
  border:1px solid var(--line); border-radius:10px; overflow:hidden; }
th,td { padding:10px 14px; text-align:right; border-bottom:1px solid var(--line); font-variant-numeric:tabular-nums; }
th:first-child,td:first-child { text-align:left; font-variant-numeric:normal; }
th { font-size:11px; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted);
  background:#f4f4ee; font-weight:600; }
tr:last-child td { border-bottom:none; }
.control td:first-child::after { content:" · control"; color:var(--accent); font-size:11px; }
.caveats { background:#fffdf5; border:1px solid #e8dcb8; border-radius:10px; padding:18px 22px; }
.caveats li { margin:6px 0; color:#5f5a48; }
footer { margin-top:36px; color:var(--muted); font-size:12px; }
"""


def _pct(value: float | None, invert: bool = False) -> str:
    if value is None:
        return "n/a"
    css = "good" if (value > 0) != invert else "bad"
    return f'<span class="{css}">{value:+.1%}</span>'


def _num(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.0f}"


def _card(label: str, value: str, note: str = "") -> str:
    note_html = f'<div class="note">{html.escape(note)}</div>' if note else ""
    return (f'<div class="card"><div class="label">{html.escape(label)}</div>'
            f'<div class="value">{value}</div>{note_html}</div>')


def _arm_table(report: dict) -> str:
    rows = []
    for arm, stats in report["by_arm"].items():
        rows.append(
            f"<tr><td>{html.escape(arm)}</td>"
            f"<td>{stats['runs']}</td>"
            f"<td>{stats['mean_recall']:.0%}</td>"
            f"<td>{_num(stats['mean_prompt_tokens'])}</td>"
            f"<td>{_num(stats['mean_tool_tokens'])}</td>"
            f"<td>{stats['mean_turns']:.1f}</td>"
            f"<td>{_num(stats['tokens_per_correct_answer'])}</td></tr>")
    return (
        "<table><thead><tr><th>arm</th><th>runs</th><th>recall</th>"
        "<th>prompt tokens / run</th><th>tool tokens / run</th><th>turns</th>"
        "<th>tokens per correct answer</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>")


def _task_table(report: dict, controls: set[str]) -> str:
    rows = []
    for task in report["tasks"]:
        css = ' class="control"' if task["task"] in controls else ""
        rows.append(
            f"<tr{css}><td>{html.escape(task['task'])}</td>"
            f"<td>{_num(task['git_prompt_tokens'])}</td>"
            f"<td>{_num(task['gita_prompt_tokens'])}</td>"
            f"<td>{_pct(task['reduction'])}</td>"
            f"<td>{task['git_recall']:.0%}</td>"
            f"<td>{task['gita_recall']:.0%}</td></tr>")
    return (
        "<table><thead><tr><th>task</th><th>git prompt</th><th>gita prompt</th>"
        "<th>reduction</th><th>git recall</th><th>gita recall</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>")


def _telemetry_section(events: list[dict]) -> str:
    if not events:
        return "<p class='sub'>No tool-call telemetry recorded.</p>"

    stats = summarise_telemetry(events)
    per_arm = stats["by_arm"]
    rows = []
    for arm, values in per_arm.items():
        rows.append(
            f"<tr><td>{html.escape(arm)}</td>"
            f"<td>{values['calls']}</td>"
            f"<td>{_num(values['avg_tokens_per_call'])}</td>"
            f"<td>{_num(values['avg_tokens_per_session'])}</td>"
            f"<td>{_num(values['avg_latency_ms'])} ms</td></tr>")

    depth = stats["drill_depth"]
    depth_cards = "".join(
        _card(f"stopped at {layer}", f"{share:.0%}",
              "share of sessions whose deepest call was this layer")
        for layer, share in depth.items())

    return (
        "<table><thead><tr><th>arm</th><th>tool calls</th>"
        "<th>tokens / call</th><th>tokens / session</th><th>latency</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        f"<h2>Drill depth</h2><div class='cards'>{depth_cards}</div>")


CAVEATS = [
    "Prompt tokens are summed across turns, so a tool that adds a turn costs "
    "more even when each call is cheaper. Turns, not per-call size, dominate.",
    "Entity recall is substring matching against hand-curated ground truth. It "
    "rewards naming the right things, not explaining them correctly.",
    "Ground truth comes from commit messages and git's own hunk headers, never "
    "from gita.",
    "Tasks marked control are ones where gita is expected to lose; they are "
    "included so the benchmark can be lost.",
    "Small sample: results are directional, not statistically significant.",
]


def render_dashboard(results: list[dict], events: list[dict] | None = None,
                     title: str = "gita — evaluation") -> str:
    events = events or []
    report = summarise_runs(results)
    controls = {r["task"] for r in results if r.get("expect_no_benefit")}

    reduction = report["reduction"].get("prompt_tokens")
    tool_reduction = report["reduction"].get("tool_tokens")
    quality = report["quality_delta"]
    adoption = report["adoption_rate"]

    cards = "".join([
        _card("Reduction in real cost", _pct(reduction),
              "paired, prompt tokens across whole sessions"),
        _card("Reduction in tool output", _pct(tool_reduction),
              "what the tools themselves returned"),
        _card("Quality delta", _pct(quality),
              "gita recall minus git recall; must not be negative"),
        _card("gita adoption", "n/a" if adoption is None else f"{adoption:.0%}",
              "runs where the agent chose gita unprompted"),
    ])

    caveats = "".join(f"<li>{html.escape(c)}</li>" for c in CAVEATS)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)}</title><style>{_CSS}</style></head>
<body><main>
<h1>{html.escape(title)}</h1>
<p class="sub">git-only versus git+gita, same prompts, same model.
Cost is shown beside quality throughout.</p>

<div class="cards">{cards}</div>

<h2>Per arm</h2>
{_arm_table(report)}

<h2>Per task</h2>
{_task_table(report, controls)}

<h2>Tool calls</h2>
{_telemetry_section(events)}

<h2>Read this before quoting the numbers</h2>
<div class="caveats"><ul>{caveats}</ul></div>

<footer>Generated offline from results.jsonl and telemetry.jsonl.
No network, no cloud, no model involved in producing these figures.</footer>
</main></body></html>"""


def load_results(run_dir: str | Path) -> tuple[list[dict], list[dict]]:
    root = Path(run_dir)
    results = []
    path = root / "results.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf8").splitlines():
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    events: list[dict] = []
    for telemetry in root.rglob("telemetry.jsonl"):
        for line in telemetry.read_text(encoding="utf8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return results, events


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gita-dashboard")
    parser.add_argument("run_dir")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    results, events = load_results(args.run_dir)
    out = Path(args.out) if args.out else Path(args.run_dir) / "dashboard.html"
    out.write_text(render_dashboard(results, events), encoding="utf8")
    print(f"wrote {out}  ({len(results)} runs, {len(events)} tool calls)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
