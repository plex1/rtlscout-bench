"""Leaderboard tables from the data repository, as text, markdown or LaTeX.

    python -m rtlscout_bench.tables                       # one table per enabled task, plain text
    python -m rtlscout_bench.tables --format md           # markdown (--format tex for LaTeX)
    python -m rtlscout_bench.tables --runs                # every run behind each row
    python -m rtlscout_bench.tables --head-to-head        # Verilog vs. Spire HDL rows of the same model, side by side
    python -m rtlscout_bench.tables --campaign runs/campaigns/<name>.json    # status of one campaign's runs

Per entry the table reports the best cost reached per run as mean ± sample standard deviation over the n selected
runs, the best single run, the mean relative to the unmodified starting point, runtime per run and API cost
per run at list prices. This module also holds the aggregation that the site generator and ``publish`` share.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

from .config import LANGUAGE_LABEL, MatrixError, load_matrix
from .record import conditions_of, load_leaderboard, load_models, run_path

DASH = "—"


class BoardError(Exception):
    """The selection of a leaderboard file cannot be turned into a table (missing record, mixed conditions)."""


# --------------------------------------------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------------------------------------------

def _sd(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def aggregate(records: list[dict]) -> dict | None:
    """Row statistics over the runs that produced a passing design. ``sd`` is the sample standard deviation (n-1)."""
    valid = [r for r in records if r.get("best")]
    if not valid:
        return None
    cost = [r["best"]["cost"] for r in valid]
    minutes = [r["runtime_min"] for r in records]
    usd = [r["usd"] for r in records if r.get("usd") is not None]
    return dict(n=len(valid), no_pass=len(records) - len(valid), mean=statistics.mean(cost), sd=_sd(cost),
                min=min(cost), max=max(cost), median=statistics.median(cost),
                runtime=statistics.mean(minutes), runtime_sd=_sd(minutes),
                usd=statistics.mean(usd) if usd else None, usd_sd=_sd(usd) if usd else None)


def _label(name: str, rec: dict) -> str:
    parts = [name]
    if rec["reasoning_effort"] != "default":
        parts.append(f"{rec['reasoning_effort']} effort")
    if rec["language"] != "verilog":
        parts.append(LANGUAGE_LABEL.get(rec["language"], rec["language"]))
    return " · ".join(parts)


def scale_exponent(value: float | None) -> int:
    """Power of ten (a multiple of three) used to print costs of this magnitude, e.g. 6 for 4.6e6."""
    if not value or value <= 0:
        return 0
    return max(0, int(math.floor(math.log10(value) / 3)) * 3)


def load_board(data: Path, task_id: str, current_suite: str | None = None, models: dict | None = None) -> dict:
    """The resolved leaderboard of one task: curation file + the records it selects + row statistics.

    Raises :class:`BoardError` when a selected run has no record or the runs of one entry were not measured under
    identical conditions (one row must never mix suites, models, languages, step budgets or flags).
    """
    models = load_models(data) if models is None else models
    lb = load_leaderboard(data, task_id)
    rows = []
    for entry, run_ids in lb["runs"].items():
        recs = []
        for run_id in sorted(run_ids):
            p = run_path(data, task_id, entry, run_id) / "record.json"
            if not p.is_file():
                raise BoardError(f"leaderboards/{task_id}.json selects {entry}/{run_id} but {p} does not exist")
            rec = json.loads(p.read_text())
            rec["run"] = run_id
            recs.append(rec)
        if not recs:
            continue
        first = dict(conditions_of(recs[0]), suite=recs[0]["suite"])
        for rec in recs[1:]:
            other = dict(conditions_of(rec), suite=rec["suite"])
            diff = sorted(k for k in first if first[k] != other[k])
            if diff:
                raise BoardError(f"entry {entry} of task {task_id} mixes runs measured under different conditions: "
                                 f"{recs[0]['run']} and {rec['run']} differ in {', '.join(diff)}. Exclude one "
                                 f"set (python -m rtlscout_bench.record --exclude ... --reason ...).")
        info = models.get(recs[0]["model"], {})
        name = info.get("name") or recs[0]["model"]
        rows.append(dict(
            entry=entry, model=recs[0]["model"], name=name, vendor=info.get("vendor"),
            open_weights=info.get("open_weights"), language=recs[0]["language"],
            reasoning_effort=recs[0]["reasoning_effort"], flags=recs[0]["flags"], max_steps=recs[0]["max_steps"],
            suite=recs[0]["suite"], seed=current_suite is not None and recs[0]["suite"] != current_suite,
            label=_label(name, recs[0]), price=recs[0].get("price_per_mtok"), note=lb["notes"].get(entry),
            runs=recs, agg=aggregate(recs)))
    seen: dict[str, int] = {}
    for row in rows:
        seen[row["label"]] = seen.get(row["label"], 0) + 1
    for row in rows:
        if seen[row["label"]] > 1:
            row["label"] += f" · {row['entry']}"
    rows.sort(key=lambda r: (r["agg"] is None, r["agg"]["mean"] if r["agg"] else 0.0, r["entry"]))

    excluded = []
    for x in lb["exclude"]:
        p = run_path(data, task_id, x["entry"], x["run"]) / "record.json"
        rec = json.loads(p.read_text()) if p.is_file() else {}
        excluded.append(dict(entry=x["entry"], run=x["run"], reason=x.get("reason", ""),
                             cost=(rec.get("best") or {}).get("cost"), suite=rec.get("suite")))
    ref = next((b["cost"] for b in lb["baselines"].values() if b.get("cost")), None) \
        or next((r["agg"]["mean"] for r in rows if r["agg"]), None)
    return dict(task=task_id, title=lb.get("title") or task_id, description=lb.get("description", ""),
                metric_label=lb.get("metric_label") or "cost", unit=lb.get("unit", ""), flow=lb.get("flow", ""),
                baselines=lb["baselines"], rows=rows, excluded=excluded, footnotes=lb["footnotes"],
                scale_exp=scale_exponent(ref))


def baseline_cost(board: dict, language: str) -> float | None:
    return (board["baselines"].get(language) or {}).get("cost")


# --------------------------------------------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------------------------------------------

def _unit(board: dict, tex: bool = False) -> str:
    exp = board["scale_exp"]
    if tex:
        scale = f"$10^{{{exp}}}$\\," if exp else ""
        return scale + board["unit"].replace("µm²·ps", "$\\mu$m$^2{\\cdot}$ps").replace("µm²", "$\\mu$m$^2$")
    return (f"10^{exp} " if exp else "") + board["unit"]


def _cells(board: dict, row: dict) -> dict:
    """Formatted cells of one row; costs are divided by 10^scale_exp."""
    k = 10 ** board["scale_exp"]
    a = row["agg"]
    if a is None:
        n = f"0 (+{len(row['runs'])} without a passing design)"
        return dict(label=row["label"], n=n, mean=DASH, best=DASH, vs=DASH, runtime=DASH, usd=DASH)
    pm = lambda m, s, fmt, n: f"{m:{fmt}} ± {s:{fmt}}" if n > 1 else f"{m:{fmt}}"
    total = a["n"] + a["no_pass"]
    base = baseline_cost(board, row["language"])
    vs = f"{(a['mean'] / base - 1) * 100:+.1f}%" if base else DASH
    n = str(a["n"]) + (f" (+{a['no_pass']} without a passing design)" if a["no_pass"] else "")
    return dict(label=row["label"], n=n,
                mean=pm(a["mean"] / k, a["sd"] / k, ".3f", a["n"]), best=f"{a['min'] / k:.3f}", vs=vs,
                runtime=pm(a["runtime"], a["runtime_sd"], ".0f", total),
                usd=pm(a["usd"], a["usd_sd"], ".1f", total) if a["usd"] is not None else DASH)


def _caption(board: dict) -> str:
    k = 10 ** board["scale_exp"]
    starts = ", ".join(f"{LANGUAGE_LABEL.get(lang, lang)} {b['cost'] / k:.3f}" for lang, b in board["baselines"].items()
                       if b.get("cost"))
    parts = [f"Task `{board['task']}`: {board['title']}."]
    if starts:
        parts.append(f"Starting point: {starts} ({_unit(board)}).")
    parts.append(f"Numbers: best {board['metric_label']} reached per run, mean ± sample sd over n independent runs; "
                 f"`best run` is the single best run, `vs. start` the mean relative to the unmodified starting "
                 f"point, runtime the duration of the agent run, $/run the API cost from recorded token usage at list prices.")
    return " ".join(parts)


HEADERS = ("#", "Entry", "n", "mean ± sd", "best run", "vs. start", "runtime (min)", "$/run", "suite")


def _table_rows(board: dict) -> list[tuple[str, ...]]:
    out = []
    for i, row in enumerate(board["rows"], 1):
        c = _cells(board, row)
        out.append((str(i), c["label"], c["n"], c["mean"], c["best"], c["vs"], c["runtime"], c["usd"], row["suite"]))
    return out


def render_text(board: dict) -> str:
    """Fixed-width table for terminals (what ``site`` and ``publish`` print)."""
    k = 10 ** board["scale_exp"]
    label = ", ".join(part for part in (board["metric_label"], _unit(board)) if part)
    lines = [f"{board['task']}: {board['title']}  [{label}; lower is better]"]
    for lang, b in board["baselines"].items():
        if b.get("cost"):
            lines.append(f"starting point ({LANGUAGE_LABEL.get(lang, lang)}): {b['cost']:,.2f} = {b['cost'] / k:.3f}")
    rows = _table_rows(board)
    if not rows:
        return "\n".join(lines + ["(no runs selected)"])
    table = [HEADERS] + rows
    widths = [max(len(r[i]) for r in table) for i in range(len(HEADERS))]
    left = {1, 8}
    for j, r in enumerate(table):
        cells = (c.ljust(widths[i]) if i in left else c.rjust(widths[i]) for i, c in enumerate(r))
        lines.append("  ".join(cells).rstrip())
        if j == 0:
            lines.append("  ".join("-" * w for w in widths))
    if board["excluded"]:
        lines.append(f"excluded: {len(board['excluded'])} run(s)")
        lines += [f"  {x['entry']}/{x['run']}: {x['reason']}" for x in board["excluded"]]
    return "\n".join(lines)


def render_markdown(board: dict) -> str:
    hdr = list(HEADERS)
    hdr[3] = f"{board['metric_label']}, mean ± sd ({_unit(board)})"
    lines = [f"### {board['title']} (`{board['task']}`)", "", _caption(board), "",
             "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    lines += ["| " + " | ".join(r) + " |" for r in _table_rows(board)]
    if board["excluded"]:
        lines += ["", "Excluded runs:", ""] + [f"- `{x['entry']}/{x['run']}`: {x['reason']}" for x in board["excluded"]]
    return "\n".join(lines) + "\n"


def _tex(s: str) -> str:
    for a, b in (("\\", r"\textbackslash{}"), ("%", r"\%"), ("_", r"\_"), ("&", r"\&"), ("#", r"\#"), ("$", r"\$"),
                 ("±", r"$\pm$"), ("·", r"$\cdot$"), ("×", r"$\times$"), ("−", "$-$"), (DASH, "--")):
        s = s.replace(a, b)
    return s


def render_latex(board: dict) -> str:
    lines = [r"% generated by python -m rtlscout_bench.tables --format tex", r"\begin{table}[t]", r"\centering",
             rf"\caption{{{_tex(board['title'])} (\texttt{{{_tex(board['task'])}}}): "
             rf"best {_tex(board['metric_label'])} per run, mean $\pm$ sample sd over $n$ runs, "
             rf"in {_unit(board, tex=True)}. Lower is better.}}",
             rf"\label{{tab:{board['task']}}}", r"\begin{tabular}{llrrrrrr}", r"\toprule",
             r"\# & Entry & $n$ & mean $\pm$ sd & best run & vs.\ start & runtime (min) & \$/run \\", r"\midrule"]
    for r in _table_rows(board):
        lines.append(" & ".join(_tex(c) for c in r[:8]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def render_runs_markdown(board: dict) -> str:
    """Every selected run of every row, best first within a row."""
    k = 10 ** board["scale_exp"]
    lines = [f"### Runs of `{board['task']}`", "",
             f"| Entry | Run | best ({_unit(board)}) | area | delay | best at eval / step | evals / failed | min | $ | "
             f"suite | rtlscout |", "|" + "---|" * 11]
    num = lambda v, fmt: DASH if v is None else f"{v:{fmt}}"
    for row in board["rows"]:
        for rec in sorted(row["runs"], key=lambda r: (r["best"] is None, (r["best"] or {}).get("cost", 0))):
            b = rec["best"] or {}
            cost = b.get("cost") and b["cost"] / k
            lines.append(f"| {row['label']} | `{row['entry']}/{rec['run']}` | {num(cost, '.3f')} "
                         f"| {num(b.get('area'), '.0f')} | {num(b.get('delay'), '.1f')} "
                         f"| {b.get('eval', DASH)} / {b.get('step', DASH)} | {rec['n_evals']} / {rec['n_failed']} "
                         f"| {rec['runtime_min']:.0f} | {num(rec['usd'], '.2f')} | {rec['suite']} "
                         f"| {rec['rtlscout']} |")
    return "\n".join(lines) + "\n"


def render_head_to_head(board: dict) -> str:
    """Verilog and Spire HDL rows of the same model and reasoning effort, side by side (markdown)."""
    k = 10 ** board["scale_exp"]
    pairs: dict[tuple, dict] = {}
    for row in board["rows"]:
        pairs.setdefault((row["model"], row["reasoning_effort"]), {}).setdefault(row["language"], row)
    fmt = lambda v: DASH if v is None else f"{v / k:.3f}"
    pct = lambda new, base: DASH if not new or not base else f"{(new / base - 1) * 100:+.1f}%"
    lines = [f"### Verilog vs. Spire HDL on `{board['task']}` ({_unit(board)})", "",
             "| Model | Start V / S | Best V (median) | Best S (median) | vs. start (V / S) | Winner |",
             "|---|---|---|---|---|---|"]
    n = 0
    for (model, effort), sides in pairs.items():
        v, s = sides.get("verilog"), sides.get("spirehdl")
        if not (v and s and v["agg"] and s["agg"]):
            continue
        n += 1
        bv, bs = baseline_cost(board, "verilog"), baseline_cost(board, "spirehdl")
        va, sa = v["agg"], s["agg"]
        lo, hi = sorted((sa["min"], va["min"]))
        win = ("Spire " if sa["min"] < va["min"] else "Verilog ") + pct(lo, hi)
        name = v["name"] + ("" if effort == "default" else f" · {effort} effort")
        lines.append(f"| {name} | {fmt(bv)} / {fmt(bs)} | {fmt(va['min'])} ({fmt(va['median'])}) "
                     f"| {fmt(sa['min'])} ({fmt(sa['median'])}) | {pct(va['min'], bv)} / {pct(sa['min'], bs)} "
                     f"| {win} |")
    if not n:
        lines.append("| (no model has both a Verilog and a Spire HDL row in this task) | | | | | |")
    return "\n".join(lines) + "\n"


def render_campaign(camp_path: Path) -> str:
    """Markdown status tables of one campaign file: baselines, per (task, entry) summary, and every run.

    Best costs are re-read from each run directory when it still exists, so the table reflects the folders even
    if the campaign file is stale.
    """
    camp = json.loads(camp_path.read_text())
    meta = camp.get("campaign", {})
    runs_root = camp_path.resolve().parent.parent
    runs = []
    for r in camp.get("runs", []):
        r = dict(r)
        d = runs_root / r["run_dir"] if r.get("run_dir") else None
        if d is not None and (d / "best_design" / "_best_meta.json").is_file():
            m = json.loads((d / "best_design" / "_best_meta.json").read_text())
            r["best_cost"], r["best_eval"] = m.get("best_cost"), m.get("eval_index")
        runs.append(r)
    fmt = lambda v: DASH if v is None else (f"{v:.4g}" if v >= 1e6 else f"{v:,.0f}")
    pct = lambda new, base: DASH if not new or not base else f"{(new / base - 1) * 100:+.1f}%"
    name = meta.get("name", camp_path.stem)
    lines = [f"# Campaign {name} · {meta.get('suite')} · rtlscout {meta.get('rtlscout')}", "",
             "| Task | Entry | n | Start | Best (median) | vs. start |", "|---|---|---|---|---|---|"]
    groups: dict[tuple, list[dict]] = {}
    for r in runs:
        groups.setdefault((r.get("task"), r.get("entry"), r.get("language")), []).append(r)
    for (task, entry, lang), rs in groups.items():
        bests = [r["best_cost"] for r in rs if r.get("best_cost")]
        base = (camp.get("baselines", {}).get(f"{task}/{lang}") or {}).get("cost")
        best, med = (min(bests), statistics.median(bests)) if bests else (None, None)
        lines.append(f"| `{task}` | `{entry}` | {len(rs)} | {fmt(base)} | {fmt(best)} ({fmt(med)}) "
                     f"| {pct(best, base)} |")
    lines += ["", "## Runs", "", "| Task | Entry | Rep | Run | Status | Best (eval) | Evals |",
              "|---|---|---|---|---|---|---|"]
    for r in sorted(runs, key=lambda x: (str(x.get("task")), str(x.get("entry")), x.get("repeat") or 0)):
        lines.append(f"| `{r.get('task')}` | `{r.get('entry')}` | {r.get('repeat')} | `{r.get('run_id') or DASH}` "
                     f"| {r.get('status')} | {fmt(r.get('best_cost'))} (eval {r.get('best_eval')}) "
                     f"| {r.get('n_evals')} |")
    return "\n".join(lines) + "\n"


def diff_boards(before: dict | None, after: dict) -> list[str]:
    """What changed between two states of one leaderboard, as readable lines (for the publish gate)."""
    k = 10 ** after["scale_exp"]
    b_rows = {r["entry"]: r for r in (before or {}).get("rows", [])}
    a_rows = {r["entry"]: r for r in after["rows"]}
    desc = lambda r: (f"n={r['agg']['n']}, mean {r['agg']['mean'] / k:.3f} ± {r['agg']['sd'] / k:.3f}" if r["agg"]
                      else "no passing run")
    out = []
    for e, r in a_rows.items():
        if e not in b_rows:
            out.append(f"+ row added    {r['label']} [{e}]: {desc(r)} ({r['suite']})")
        else:
            old = b_rows[e]
            ids_old, ids_new = {x["run"] for x in old["runs"]}, {x["run"] for x in r["runs"]}
            if ids_old != ids_new:
                out.append(f"~ row changed  {r['label']} [{e}]: {desc(old)} ({old['suite']}) -> {desc(r)} "
                           f"({r['suite']}); "
                           f"runs +{len(ids_new - ids_old)} -{len(ids_old - ids_new)}")
    for e, r in b_rows.items():
        if e not in a_rows:
            out.append(f"- row removed  {r['label']} [{e}]: was {desc(r)}")
    old_ex = {(x["entry"], x["run"]) for x in (before or {}).get("excluded", [])}
    for x in after["excluded"]:
        if (x["entry"], x["run"]) not in old_ex:
            out.append(f"! run excluded {x['entry']}/{x['run']}: {x['reason']}")
    for lang, b in after["baselines"].items():
        old = ((before or {}).get("baselines") or {}).get(lang)
        if b.get("cost") and (not old or old.get("cost") != b["cost"]):
            was = f"{old['cost']:,.2f}" if old and old.get("cost") else "none"
            out.append(f"~ starting point ({lang}): {was} -> {b['cost']:,.2f}")
    return out


# --------------------------------------------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rtlscout_bench.tables", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", help="matrix file (default: ./campaign_matrix.yaml)")
    ap.add_argument("--data", default="data", help="data repository checkout (default: data)")
    ap.add_argument("--task", action="append", help="task id (repeatable; default: every enabled task)")
    ap.add_argument("--format", choices=("text", "md", "tex"), default="text")
    ap.add_argument("--runs", action="store_true", help="list every selected run instead of the summary rows")
    ap.add_argument("--head-to-head", action="store_true", help="pair Verilog and Spire HDL rows of the same model")
    ap.add_argument("--campaign", metavar="FILE", help="print the status tables of one campaign JSON and exit")
    ap.add_argument("-o", "--output", help="write to this file instead of stdout")
    args = ap.parse_args(argv)

    if args.campaign:
        text = render_campaign(Path(args.campaign))
    else:
        try:
            matrix = load_matrix(args.matrix)
        except MatrixError as exc:
            print(f"{exc.path}: invalid matrix\n" + "\n".join(f"  - {p}" for p in exc.problems), file=sys.stderr)
            return 2
        data = Path(args.data)
        blocks = []
        try:
            for task_id in args.task or [t.id for t in matrix.enabled_tasks()]:
                matrix.task(task_id)
                board = load_board(data, task_id, matrix.suite)
                if args.head_to_head:
                    blocks.append(render_head_to_head(board))
                elif args.runs:
                    blocks.append(render_runs_markdown(board))
                else:
                    blocks.append({"text": render_text, "md": render_markdown, "tex": render_latex}[args.format](board))
        except (BoardError, KeyError) as exc:
            print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
            return 1
        text = "\n".join(blocks)
    if args.output:
        Path(args.output).write_text(text if text.endswith("\n") else text + "\n")
        print(f"wrote {args.output}")
    else:
        print(text.rstrip("\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
