"""Raw run directories -> the data repository. This module is the only writer of ``data/``.

    python -m rtlscout_bench.record                 # record every finished run of the campaigns under runs/
    python -m rtlscout_bench.record runs/campaigns/20261101_090000.json
    python -m rtlscout_bench.record --check         # validate data/ (schema, policy, artefacts, key patterns)
    python -m rtlscout_bench.record --exclude tpu-adp/glm-5.2/20261101_090004 --reason "provider outage at step 12"

For each run it writes ``data/runs/<task>/<entry>/<run-id>/`` with ``record.json`` (everything a table or plot
needs), ``best_design/``, ``summary.txt`` and ``chat_log.txt``, adds the run to the selection in
``data/leaderboards/<task>.json`` and copies the campaign file to ``data/campaigns/``. The raw per-evaluation
workspaces are not copied.

Run directories that were not launched by ``rtlscout_bench.campaign`` can be adopted by naming their place in the
matrix, e.g. the runs that seeded the leaderboard:

    python -m rtlscout_bench.record <run dir>... --task tpu-adp --entry glm-5.2 \\
        --origin seed --suite pre-v1 --rtlscout "pre-0.2.0 (unpackaged, 2026-07)"

Refusals: a model not marked ``open_weights`` in ``data/models.json`` while ``policy.open_weights_only`` is on,
and any transcript or artefact that contains an API-key pattern.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import CONDITION_FIELDS, Matrix, MatrixError, load_matrix

SCHEMA = 1
KEY_PATTERNS = ("sk-ant-", "sk-or-", "API_KEY=")
HOME_PATH_RE = re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+")
RUN_ID_RE = re.compile(r"^\d{8}_\d{6}$")
ORIGINS = ("campaign", "seed", "manual")
STATUSES = ("completed", "ended-early")
EVAL_KEYS = ("e", "s", "ok", "c", "area", "delay", "power", "td", "ctx", "t_min", "tok_in", "tok_out")


class RecordError(Exception):
    """A run cannot be recorded (policy, key pattern, inconsistent or incomplete run directory)."""


# --------------------------------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------------------------------

def _read_json(path: Path):
    return json.loads(path.read_text())


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _write_json(path: Path, obj) -> None:
    _write_text_atomic(path, json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def _r(v, nd: int):
    return None if v is None else round(float(v), nd)


def git_commit(repo: Path) -> str | None:
    """Full commit id of *repo*'s HEAD, with ``-dirty`` appended when tracked files are modified; None outside git."""
    try:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no",
                                "--ignore-submodules=all"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return sha.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def scan_key_patterns(path: Path) -> list[tuple[int, str]]:
    """(line number, pattern) for every API-key pattern in a text file. The matching text itself is never returned."""
    hits = []
    try:
        with open(path, "r", errors="replace") as f:
            for n, line in enumerate(f, 1):
                for pat in KEY_PATTERNS:
                    if pat in line:
                        hits.append((n, pat))
    except OSError:
        pass
    return hits


def _artefact_files(run_dir: Path) -> list[Path]:
    files = [run_dir / "chat_log.txt", run_dir / "summary.txt"]
    best = run_dir / "best_design"
    if best.is_dir():
        files += sorted(p for p in best.rglob("*") if p.is_file())
    return [p for p in files if p.is_file()]


# --------------------------------------------------------------------------------------------------------------
# models.json and prices
# --------------------------------------------------------------------------------------------------------------

def load_models(data: Path) -> dict:
    """``data/models.json`` as {model spec: info}. A missing file is an empty registry."""
    p = data / "models.json"
    if not p.is_file():
        return {}
    return _read_json(p).get("models", {})


def model_prices(info: dict | None) -> tuple[float, float, float, float] | None:
    """(input, output, cache write, cache read) in USD per million tokens; cache prices default to the input price."""
    price = (info or {}).get("price_per_mtok")
    if not isinstance(price, dict) or "input" not in price or "output" not in price:
        return None
    pin, pout = float(price["input"]), float(price["output"])
    return pin, pout, float(price.get("cache_write", pin)), float(price.get("cache_read", pin))


def cost_usd(tokens: dict, prices: tuple[float, float, float, float] | None) -> float | None:
    if prices is None:
        return None
    pin, pout, pcw, pcr = prices
    return (tokens["inp"] * pin + tokens["out"] * pout + tokens["cache_w"] * pcw + tokens["cache_r"] * pcr) / 1e6


# --------------------------------------------------------------------------------------------------------------
# raw run dir -> record
# --------------------------------------------------------------------------------------------------------------

def eval_minutes_from_mtimes(run_dir: Path, result: dict) -> dict[int, float]:
    """Minutes since run start per evaluation, for runs written before rtlscout recorded ``elapsed_s``.

    Each evaluation leaves ``eval_<n>/result.json``; its modification time minus the run's start gives the
    elapsed time. The start is the run directory's own time stamp (``YYYYMMDD_HHMMSS``, written in the clock of
    the machine that ran it). The stamp carries no time zone, so the zone offset is taken from the end of the
    run: ``result.json`` is written ``duration_s`` after the start, and the stamp must agree with that to within
    a minute once shifted by a whole number of quarter hours (at most 14 hours, the largest zone offset there is).
    If it does not, or the evaluations do not spread over the run (files copied without their modification
    times, for instance), no times are returned rather than wrong ones.
    """
    m = RUN_ID_RE.match(run_dir.name)
    result_json = run_dir / "result.json"
    if not m or not result_json.is_file() or not result.get("duration_s"):
        return {}
    stamp = datetime.strptime(run_dir.name, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc).timestamp()
    start_from_end = result_json.stat().st_mtime - float(result["duration_s"])
    offset = round((start_from_end - stamp) / 900) * 900
    if abs(offset) > 14 * 3600 or abs(start_from_end - stamp - offset) > 60:
        return {}
    start = stamp + offset
    out: dict[int, float] = {}
    raw: list[float] = []
    last = 0.0
    for ev in result.get("all_evals", []):
        idx = ev.get("eval_index")
        p = run_dir / f"eval_{idx}" / "result.json"
        if not p.is_file():
            return {}
        t = (p.stat().st_mtime - start) / 60
        if t < last - 0.05 or t > float(result["duration_s"]) / 60 + 1:
            return {}
        last = t
        out[idx] = round(t, 1)
        raw.append(t)
    if len(raw) > 1 and raw[-1] - raw[0] < 1 / 60:
        return {}
    return out


def _eval_row(ev: dict, t_min: float | None) -> dict:
    metrics = ev.get("metrics") or {}
    cum = ev.get("cumulative_token_usage") or {}
    if ev.get("elapsed_s") is not None:
        t_min = round(float(ev["elapsed_s"]) / 60, 1)
    row = dict(e=ev.get("eval_index"), s=ev.get("step_index"), ok=bool(ev.get("passed")),
               c=_r(ev.get("cost_value"), 2), area=_r(metrics.get("area"), 2), delay=_r(metrics.get("delay"), 2),
               power=metrics.get("power"),
               # the delay the flow actually synthesised for: the metrics carry it also when the agent did not ask
               # for a specific one (rtlscout's default applies then); a failed evaluation has only the request
               td=metrics.get("target_delay", ev.get("target_delay")), ctx=ev.get("context_window_tokens"),
               t_min=t_min, tok_in=cum.get("input_tokens"), tok_out=cum.get("output_tokens"))
    # cache token counts only where a provider reports them, so the common case stays compact
    if cum.get("cache_creation_input_tokens") or cum.get("cache_read_input_tokens"):
        row["tok_cw"] = cum.get("cache_creation_input_tokens", 0)
        row["tok_cr"] = cum.get("cache_read_input_tokens", 0)
    return row


def build_record(run_dir: Path, cond: dict, prov: dict, models: dict) -> dict:
    """Assemble the record of one raw run directory.

    *cond* holds the campaign conditions (``config.CONDITION_FIELDS``); *prov* the provenance (suite, origin,
    rtlscout, spire_hdl, bench_commit, and optionally campaign, reasoning_effort_source).
    """
    result_path = run_dir / "result.json"
    if not result_path.is_file():
        raise RecordError(f"{run_dir}: no result.json (the run did not finish)")
    if not RUN_ID_RE.match(run_dir.name):
        raise RecordError(f"{run_dir}: directory name is not a run id (YYYYMMDD_HHMMSS)")
    res = _read_json(result_path)

    model_name = cond["model"].split(":", 1)[1]
    for what, got, want in (("benchmark", res.get("benchmark_name"), cond["benchmark"]),
                            ("model", res.get("model"), model_name),
                            ("cost metric", res.get("cost_metric"), cond["cost_metric"])):
        if got != want:
            raise RecordError(f"{run_dir}: {what} is {got!r} but entry {cond['entry']} / task {cond['task']} "
                              f"expects {want!r}")
    if (res.get("num_steps") or 0) > cond["max_steps"]:
        raise RecordError(f"{run_dir}: used {res.get('num_steps')} steps, more than the entry's max_steps "
                          f"{cond['max_steps']}")

    evals_raw = res.get("all_evals") or []
    mtimes = {} if all(e.get("elapsed_s") is not None for e in evals_raw) and evals_raw else \
        eval_minutes_from_mtimes(run_dir, res)
    evals = [_eval_row(e, mtimes.get(e.get("eval_index"))) for e in evals_raw]

    best = None
    meta_path = run_dir / "best_design" / "_best_meta.json"
    if meta_path.is_file():
        meta = _read_json(meta_path)
        want = res.get("best_cost")
        if want is not None and abs(meta["best_cost"] - want) > 1e-6 * abs(want):
            raise RecordError(f"{run_dir}: best_design/_best_meta.json ({meta['best_cost']}) and result.json "
                              f"({res['best_cost']}) disagree on the best cost")
        ev = next((e for e in evals_raw if e.get("eval_index") == meta.get("eval_index")), {})
        metrics = ev.get("metrics") or res.get("best_metrics") or {}
        best = dict(cost=_r(meta["best_cost"], 2), area=_r(metrics.get("area"), 2), delay=_r(metrics.get("delay"), 2),
                    eval=meta.get("eval_index"), step=meta.get("step_index"), file=meta.get("design_file"))
    elif res.get("passed"):
        raise RecordError(f"{run_dir}: result.json reports a passing design but best_design/ is missing")

    usage = res.get("token_usage") or {}
    tokens = dict(inp=usage.get("input_tokens", 0), out=usage.get("output_tokens", 0),
                  cache_w=usage.get("cache_creation_input_tokens", 0), cache_r=usage.get("cache_read_input_tokens", 0))
    info = models.get(cond["model"])
    prices = model_prices(info)
    usd = cost_usd(tokens, prices)
    started = datetime.strptime(run_dir.name, "%Y%m%d_%H%M%S").strftime("%Y-%m-%dT%H:%M")

    rec = dict(schema=SCHEMA, suite=prov["suite"], origin=prov["origin"],
               status="ended-early" if res.get("error") else "completed",
               rtlscout=prov["rtlscout"], spire_hdl=prov.get("spire_hdl"), bench_commit=prov.get("bench_commit"))
    if prov.get("campaign"):
        rec["campaign"] = prov["campaign"]
    rec.update(task=cond["task"], benchmark=cond["benchmark"], language=cond["language"],
               cost_metric=cond["cost_metric"], target_delay=cond["target_delay"], technology=cond["technology"],
               entry=cond["entry"], model=cond["model"], open_weights=(info or {}).get("open_weights"),
               reasoning_effort=cond["reasoning_effort"])
    if prov.get("reasoning_effort_source"):
        rec["reasoning_effort_source"] = prov["reasoning_effort_source"]
    rec.update(agent_backend=cond["agent_backend"], max_steps=cond["max_steps"], flags=list(cond["flags"]),
               env=dict(cond["env"]), started=started, best=best, evals=evals, n_evals=len(evals),
               n_failed=sum(1 for e in evals if not e["ok"]), steps_used=res.get("num_steps"),
               runtime_min=_r((res.get("duration_s") or 0) / 60, 1), tokens=tokens, usd=_r(usd, 2),
               price_per_mtok=[prices[0], prices[1]] if prices else None)
    if prices and (tokens["cache_w"] or tokens["cache_r"]):
        rec["price_cache_per_mtok"] = [prices[2], prices[3]]
    if res.get("error"):
        rec["error"] = str(res["error"])[:500]
    return rec


def format_record(rec: dict) -> str:
    """JSON with one top-level key per line and one evaluation per line: compact, and readable in a diff."""
    lines = []
    for k, v in rec.items():
        if k == "evals" and v:
            body = ",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in v)
            lines.append(f' "evals": [\n{body}\n ]')
        else:
            lines.append(f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}")
    return "{\n" + ",\n".join(lines) + "\n}\n"


def conditions_of(rec: dict) -> dict:
    return {k: rec.get(k) for k in CONDITION_FIELDS}


def run_path(data: Path, task: str, entry: str, run_id: str) -> Path:
    return data / "runs" / task / entry / run_id


def check_refusals(data: Path, rec: dict, run_dir: Path, *, policy_open_only: bool) -> None:
    """Raise :class:`RecordError` when the run must not enter the data repo: policy, key patterns, no transcript."""
    if policy_open_only and rec["open_weights"] is not True:
        raise RecordError(f"{run_dir}: model {rec['model']} is not marked \"open_weights\": true in "
                          f"{data / 'models.json'} and policy.open_weights_only is on; nothing was written")
    for p in _artefact_files(run_dir) + [run_dir / "result.json"]:
        hits = scan_key_patterns(p)
        if hits:
            where = ", ".join(f"line {n} ({pat})" for n, pat in hits[:5])
            raise RecordError(f"{p}: contains an API-key pattern at {where}; refusing to copy this run. "
                              f"Transcripts are published verbatim: remove the run or rotate the key.")
    if not (run_dir / "chat_log.txt").is_file():
        raise RecordError(f"{run_dir}: no chat_log.txt")


def write_run(data: Path, rec: dict, run_dir: Path, *, policy_open_only: bool, force: bool = False) -> Path:
    """Copy the durable part of *run_dir* into the data repo and write its record. Enforces the refusals."""
    check_refusals(data, rec, run_dir, policy_open_only=policy_open_only)
    dest = run_path(data, rec["task"], rec["entry"], run_dir.name)
    if (dest / "record.json").exists() and not force:
        raise FileExistsError(str(dest))
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    shutil.copy2(run_dir / "chat_log.txt", tmp / "chat_log.txt")
    if (run_dir / "summary.txt").is_file():
        shutil.copy2(run_dir / "summary.txt", tmp / "summary.txt")
    if (run_dir / "best_design").is_dir():
        shutil.copytree(run_dir / "best_design", tmp / "best_design",
                        ignore=shutil.ignore_patterns("obj_dir", "__pycache__", ".spire_cache"))
    (tmp / "record.json").write_text(format_record(rec))
    if dest.exists():
        shutil.rmtree(dest)
    tmp.replace(dest)
    return dest


def home_path_warnings(run_dir: Path) -> list[str]:
    """Transcripts are published verbatim; say so when one mentions a home directory."""
    out = []
    for p in _artefact_files(run_dir):
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        found = sorted(set(HOME_PATH_RE.findall(text)))
        if found:
            out.append(f"{p.name} mentions {', '.join(found[:3])}")
    return out


# --------------------------------------------------------------------------------------------------------------
# leaderboard files: which runs count
# --------------------------------------------------------------------------------------------------------------

def leaderboard_path(data: Path, task: str) -> Path:
    return data / "leaderboards" / f"{task}.json"


def load_leaderboard(data: Path, task: str) -> dict:
    p = leaderboard_path(data, task)
    lb = _read_json(p) if p.is_file() else dict(schema=SCHEMA, task=task, title=task, description="",
                                                 metric_label="", unit="", flow="")
    lb.setdefault("baselines", {})
    lb.setdefault("runs", {})
    lb.setdefault("exclude", [])
    lb.setdefault("notes", {})
    lb.setdefault("footnotes", [])
    return lb


def save_leaderboard(data: Path, lb: dict) -> None:
    lb["runs"] = {e: sorted(set(ids)) for e, ids in lb["runs"].items() if ids}
    _write_json(leaderboard_path(data, lb["task"]), lb)


def _is_excluded(lb: dict, entry: str, run_id: str) -> bool:
    return any(x.get("entry") == entry and x.get("run") == run_id for x in lb["exclude"])


def exclude_run(lb: dict, entry: str, run_id: str, reason: str) -> None:
    lb["runs"][entry] = [r for r in lb["runs"].get(entry, []) if r != run_id]
    if not _is_excluded(lb, entry, run_id):
        lb["exclude"].append(dict(entry=entry, run=run_id, reason=reason))


def include_run(lb: dict, entry: str, run_id: str) -> None:
    lb["exclude"] = [x for x in lb["exclude"] if not (x.get("entry") == entry and x.get("run") == run_id)]
    lb["runs"].setdefault(entry, [])
    if run_id not in lb["runs"][entry]:
        lb["runs"][entry].append(run_id)


def select_run(data: Path, lb: dict, rec: dict, run_id: str) -> list[str]:
    """Add a freshly recorded run to the leaderboard selection. Returns notes about what else changed.

    A run that ended early is recorded but listed under ``exclude`` with the reason. Runs of an earlier suite that
    the entry still had selected (the seed runs, typically) are superseded: one row never mixes suites.
    """
    notes = []
    entry = rec["entry"]
    if rec["status"] != "completed":
        exclude_run(lb, entry, run_id, f"ended early: {rec.get('error', 'no error text')}"[:200])
        return [f"{entry}/{run_id} ended early and is listed under exclude"]
    for old in list(lb["runs"].get(entry, [])):
        old_path = run_path(data, rec["task"], entry, old) / "record.json"
        old_suite = _read_json(old_path).get("suite") if old_path.is_file() else None
        if old != run_id and old_suite != rec["suite"]:
            exclude_run(lb, entry, old, f"superseded by {rec['suite']} runs")
            notes.append(f"{entry}/{old} ({old_suite}) superseded by {rec['suite']} runs")
    include_run(lb, entry, run_id)
    return notes


# --------------------------------------------------------------------------------------------------------------
# recording: campaigns and adopted run dirs
# --------------------------------------------------------------------------------------------------------------

def iter_records(data: Path):
    """Yield (task, entry, run id, path to record.json) for every record in the data repo, sorted."""
    for p in sorted((data / "runs").glob("*/*/*/record.json")):
        yield p.parts[-4], p.parts[-3], p.parts[-2], p


def _campaign_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted((path / "campaigns").glob("*.json"))


def published_campaign(camp: dict, recorded: set[str]) -> dict:
    """The copy of a campaign file kept in ``data/campaigns/``: no local log or scratch paths."""
    runs = []
    for r in camp.get("runs", []):
        keep = {k: r.get(k) for k in ("task", "entry", "model", "language", "benchmark", "repeat", "run_id", "status",
                                      "returncode", "duration_s", "best_cost", "best_eval", "n_evals", "cmd", "env")}
        keep["recorded"] = f"{r.get('task')}/{r.get('entry')}/{r.get('run_id')}" in recorded
        runs.append(keep)
    baselines = {k: {kk: v.get(kk) for kk in ("task", "language", "benchmark", "cost", "area", "delay", "status",
                                               "cmd", "env")}
                 for k, v in camp.get("baselines", {}).items()}
    meta = {k: v for k, v in camp.get("campaign", {}).items() if k not in ("runs_dir", "data_dir")}
    return dict(schema=camp.get("schema", SCHEMA), campaign=meta, baselines=baselines, runs=runs)


def record_campaign(camp_path: Path, data: Path, matrix: Matrix, models: dict, *, force: bool = False,
                    dry_run: bool = False, log=print) -> dict:
    """Record every finished run of one campaign file. Returns counts: recorded, skipped, refused."""
    camp = _read_json(camp_path)
    meta = camp.get("campaign", {})
    name = meta.get("name") or camp_path.stem
    runs_root = camp_path.resolve().parent.parent      # <runs>/campaigns/<name>.json; run_dir is relative to <runs>
    counts = dict(recorded=0, already=0, skipped=0, refused=0)
    touched: dict[str, dict] = {}
    recorded_keys: set[str] = set()

    def board(task: str) -> dict:
        if task not in touched:
            touched[task] = load_leaderboard(data, task)
        return touched[task]

    for r in camp.get("runs", []):
        key = f"{r.get('task')}/{r.get('entry')}/{r.get('run_id')}"
        if not r.get("run_dir") or not r.get("run_id"):
            log(f"  skip     {r.get('task')}/{r.get('entry')} r{r.get('repeat')}: {r.get('status')} (no run directory)")
            counts["skipped"] += 1
            continue
        dest = run_path(data, r["task"], r["entry"], r["run_id"])
        if (dest / "record.json").exists() and not force:
            counts["already"] += 1
            recorded_keys.add(key)
            continue
        run_dir = Path(r["run_dir"])
        if not run_dir.is_absolute():
            run_dir = runs_root / run_dir
        if not (run_dir / "result.json").is_file():
            log(f"  skip     {key}: {r.get('status')} (no result.json: the run did not finish)")
            counts["skipped"] += 1
            continue
        cond = {k: r.get(k) for k in CONDITION_FIELDS}
        prov = dict(suite=meta.get("suite"), origin="campaign", rtlscout=meta.get("rtlscout"),
                    spire_hdl=meta.get("spire_hdl"), bench_commit=meta.get("bench_commit"), campaign=name)
        try:
            rec = build_record(run_dir, cond, prov, models)
            if dry_run:
                check_refusals(data, rec, run_dir, policy_open_only=matrix.open_weights_only)
                log(f"  would record {key}  best {_fmt_cost(rec)}")
            else:
                write_run(data, rec, run_dir, policy_open_only=matrix.open_weights_only, force=force)
                for note in select_run(data, board(r["task"]), rec, r["run_id"]):
                    log(f"  note     {note}")
                for w in home_path_warnings(run_dir):
                    log(f"  warning  {key}: {w}")
                log(f"  recorded {key}  best {_fmt_cost(rec)}")
            counts["recorded"] += 1
            recorded_keys.add(key)
        except RecordError as exc:
            log(f"  REFUSED  {key}: {exc}")
            counts["refused"] += 1

    if dry_run:
        return counts
    # baselines measured by this campaign become the task's reference
    for b in camp.get("baselines", {}).values():
        if b.get("status") != "ok" or b.get("cost") is None:
            continue
        lb = board(b["task"])
        new = dict(cost=_r(b["cost"], 2), area=_r(b.get("area"), 2), delay=_r(b.get("delay"), 2),
                   suite=meta.get("suite"), rtlscout=meta.get("rtlscout"), campaign=name)
        old = lb["baselines"].get(b["language"])
        if old and old.get("cost") and abs(old["cost"] - new["cost"]) > 1e-6 * abs(new["cost"]):
            log(f"  WARNING  baseline of {b['task']} ({b['language']}) changed: {old['cost']:,.2f} -> "
                f"{new['cost']:,.2f}. Rows measured against the old starting point are no longer comparable.")
        lb["baselines"][b["language"]] = new
    for lb in touched.values():
        save_leaderboard(data, lb)
    has_baseline = any(b.get("status") == "ok" for b in camp.get("baselines", {}).values())
    if recorded_keys or has_baseline:
        _write_json(data / "campaigns" / f"{name}.json", published_campaign(camp, recorded_keys))
    return counts


def _fmt_cost(rec: dict) -> str:
    return f"{rec['best']['cost']:,.2f}" if rec.get("best") else "none (no passing design)"


def record_run_dirs(run_dirs: list[Path], data: Path, matrix: Matrix, models: dict, *, task_id: str, entry_id: str,
                    origin: str, suite: str, rtlscout: str, spire_hdl: str | None, effort_source: str | None,
                    bench_commit: str | None, force: bool = False, dry_run: bool = False, log=print) -> dict:
    """Adopt run directories that no campaign file describes; conditions come from the matrix entry."""
    task, entry = matrix.task(task_id), matrix.entry(entry_id)
    if task_id not in entry.tasks:
        raise RecordError(f"entry {entry_id} does not list task {task_id} in {matrix.path}")
    cond = matrix.conditions(task, entry)
    prov = dict(suite=suite, origin=origin, rtlscout=rtlscout, spire_hdl=spire_hdl, bench_commit=bench_commit,
                reasoning_effort_source=effort_source)
    counts = dict(recorded=0, already=0, skipped=0, refused=0)
    lb = load_leaderboard(data, task_id)
    for run_dir in run_dirs:
        key = f"{task_id}/{entry_id}/{run_dir.name}"
        if (run_path(data, task_id, entry_id, run_dir.name) / "record.json").exists() and not force:
            log(f"  already recorded {key}")
            counts["already"] += 1
            continue
        try:
            rec = build_record(run_dir, cond, prov, models)
            if dry_run:
                check_refusals(data, rec, run_dir, policy_open_only=matrix.open_weights_only)
                log(f"  would record {key}  best {_fmt_cost(rec)}")
            else:
                write_run(data, rec, run_dir, policy_open_only=matrix.open_weights_only, force=force)
                for note in select_run(data, lb, rec, run_dir.name):
                    log(f"  note     {note}")
                for w in home_path_warnings(run_dir):
                    log(f"  warning  {key}: {w}")
                timed = sum(1 for e in rec["evals"] if e["t_min"] is not None)
                usd = "?" if rec["usd"] is None else f"{rec['usd']:.2f}"
                log(f"  recorded {key}  best {_fmt_cost(rec)}  evals {rec['n_evals']} ({timed} timed)  "
                    f"{rec['runtime_min']:.1f} min  ${usd}")
            counts["recorded"] += 1
        except RecordError as exc:
            log(f"  REFUSED  {key}: {exc}")
            counts["refused"] += 1
    if not dry_run and counts["recorded"]:
        save_leaderboard(data, lb)
    return counts


# --------------------------------------------------------------------------------------------------------------
# validation of the data repo
# --------------------------------------------------------------------------------------------------------------

def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_record(rec) -> list[str]:
    """Schema problems of one record (empty list = valid)."""
    if not isinstance(rec, dict):
        return ["not a JSON object"]
    errs = []

    def need(key, ok, what):
        if key not in rec:
            errs.append(f"missing '{key}'")
        elif not ok(rec[key]):
            errs.append(f"'{key}' must be {what}")

    is_str = lambda v: isinstance(v, str) and v != ""
    opt = lambda f: (lambda v: v is None or f(v))
    need("schema", lambda v: v == SCHEMA, f"{SCHEMA}")
    for k in ("suite", "rtlscout", "task", "benchmark", "language", "cost_metric", "technology", "entry", "model",
              "reasoning_effort", "agent_backend", "started"):
        need(k, is_str, "a non-empty string")
    need("origin", lambda v: v in ORIGINS, f"one of {', '.join(ORIGINS)}")
    need("status", lambda v: v in STATUSES, f"one of {', '.join(STATUSES)}")
    need("spire_hdl", opt(is_str), "a string or null")
    need("bench_commit", opt(is_str), "a string or null")
    need("target_delay", opt(_is_num), "a number or null")
    need("open_weights", opt(lambda v: isinstance(v, bool)), "true, false or null")
    need("max_steps", lambda v: isinstance(v, int) and v > 0, "a positive integer")
    need("flags", lambda v: isinstance(v, list) and all(isinstance(f, str) for f in v), "a list of strings")
    need("env", lambda v: isinstance(v, dict), "an object")
    for k in ("n_evals", "n_failed", "steps_used"):
        need(k, lambda v: isinstance(v, int) and v >= 0, "a non-negative integer")
    need("runtime_min", _is_num, "a number")
    need("usd", opt(_is_num), "a number or null")
    need("price_per_mtok", opt(lambda v: isinstance(v, list) and len(v) == 2 and all(_is_num(x) for x in v)),
         "[input, output] or null")
    need("tokens", lambda v: isinstance(v, dict) and all(isinstance(v.get(k), int) for k in
                                                         ("inp", "out", "cache_w", "cache_r")),
         "an object with integer inp, out, cache_w, cache_r")
    need("best", opt(lambda v: isinstance(v, dict) and _is_num(v.get("cost")) and isinstance(v.get("eval"), int)
                     and all(k in v for k in ("area", "delay", "step", "file"))),
         "null or an object with cost, area, delay, eval, step, file")
    need("evals", lambda v: isinstance(v, list), "a list")
    if isinstance(rec.get("evals"), list):
        for i, e in enumerate(rec["evals"]):
            if not isinstance(e, dict) or any(k not in e for k in EVAL_KEYS):
                errs.append(f"evals[{i}] must have the keys {', '.join(EVAL_KEYS)}")
                break
            if not isinstance(e["ok"], bool) or not isinstance(e["e"], int):
                errs.append(f"evals[{i}]: 'e' must be an integer and 'ok' a boolean")
                break
        if isinstance(rec.get("n_evals"), int) and rec["n_evals"] != len(rec["evals"]):
            errs.append("n_evals does not match the number of evals")
        if isinstance(rec.get("n_failed"), int) and all(isinstance(e, dict) for e in rec["evals"]) \
                and rec["n_failed"] != sum(1 for e in rec["evals"] if not e.get("ok")):
            errs.append("n_failed does not match the evals")
    if isinstance(rec.get("best"), dict) and isinstance(rec.get("evals"), list):
        ok_costs = [e.get("c") for e in rec["evals"] if isinstance(e, dict) and e.get("ok") and e.get("c") is not None]
        if ok_costs and _is_num(rec["best"].get("cost")) and abs(min(ok_costs) - rec["best"]["cost"]) > 0.011:
            errs.append("best.cost is not the lowest passing cost among the evals")
    return errs


def check_data(data: Path, matrix: Matrix, *, selected_only: bool = False) -> tuple[list[str], list[str], dict]:
    """Validate the data repo. Returns (errors, warnings, counts).

    Checks: record schema; every record sits at runs/<task>/<entry>/<run id>/ matching its own fields; the
    open-weights policy; every run has its transcript, and its best_design/ when it has a best design; no API-key
    pattern in any stored text; leaderboard files only refer to runs that exist and give a reason for each exclusion.
    With *selected_only* the per-run checks cover only the runs a leaderboard selects (what the site build needs).
    """
    errors: list[str] = []
    warnings: list[str] = []
    models = load_models(data)
    if not (data / "models.json").is_file():
        errors.append("models.json: missing")
    for spec, info in models.items():
        if not isinstance(info, dict) or not isinstance(info.get("open_weights"), bool):
            errors.append(f"models.json: {spec}: \"open_weights\" must be true or false")
        if not isinstance((info or {}).get("name"), str):
            errors.append(f"models.json: {spec}: \"name\" is required")

    boards = {}
    for p in sorted((data / "leaderboards").glob("*.json")):
        try:
            lb = load_leaderboard(data, p.stem)
        except (ValueError, OSError) as exc:
            errors.append(f"leaderboards/{p.name}: not valid JSON ({exc})")
            continue
        if lb.get("task") != p.stem:
            errors.append(f"leaderboards/{p.name}: \"task\" must be \"{p.stem}\"")
        boards[p.stem] = lb
    selected = {(t, e, r) for t, lb in boards.items() for e, ids in lb["runs"].items() for r in ids}
    excluded = {(t, x.get("entry"), x.get("run")) for t, lb in boards.items() for x in lb["exclude"]}
    for t, lb in boards.items():
        for x in lb["exclude"]:
            if not x.get("reason"):
                errors.append(f"leaderboards/{t}.json: exclusion of {x.get('entry')}/{x.get('run')} has no reason")
    for t, e, r in sorted(selected & excluded):
        errors.append(f"leaderboards/{t}.json: {e}/{r} is both selected and excluded")

    seen = set()
    n_checked = 0
    for task, entry, run_id, path in iter_records(data):
        key = (task, entry, run_id)
        seen.add(key)
        if selected_only and key not in selected:
            continue
        n_checked += 1
        rel = f"runs/{task}/{entry}/{run_id}"
        try:
            rec = _read_json(path)
        except ValueError as exc:
            errors.append(f"{rel}/record.json: not valid JSON ({exc})")
            continue
        errs = validate_record(rec)
        errors += [f"{rel}/record.json: {e}" for e in errs]
        if errs:
            continue
        if (rec["task"], rec["entry"]) != (task, entry) or not RUN_ID_RE.match(run_id):
            errors.append(f"{rel}: directory does not match the record (task {rec['task']}, entry {rec['entry']})")
        info = models.get(rec["model"])
        if matrix.open_weights_only:
            if rec["open_weights"] is not True or not (info or {}).get("open_weights"):
                errors.append(f"{rel}: model {rec['model']} is not marked open_weights "
                              f"(policy.open_weights_only is on)")
        elif info is None:
            warnings.append(f"{rel}: model {rec['model']} has no entry in models.json")
        run_dir = path.parent
        if not (run_dir / "chat_log.txt").is_file():
            errors.append(f"{rel}: chat_log.txt is missing")
        if rec["best"] is not None:
            if not (run_dir / "best_design" / "_best_meta.json").is_file():
                errors.append(f"{rel}: best_design/_best_meta.json is missing")
            elif rec["best"]["file"] and not (run_dir / "best_design" / rec["best"]["file"]).is_file():
                errors.append(f"{rel}: best_design/{rec['best']['file']} is missing")
        if not (run_dir / "summary.txt").is_file():
            warnings.append(f"{rel}: no summary.txt (the model's final summary call failed)")
        for p in _artefact_files(run_dir) + [path]:
            for n, pat in scan_key_patterns(p)[:3]:
                errors.append(f"{rel}/{p.relative_to(run_dir)}: API-key pattern {pat} at line {n}")
        if key not in selected and key not in excluded:
            warnings.append(f"{rel}: neither selected nor excluded by leaderboards/{task}.json")
    for p in sorted((data / "campaigns").glob("*.json")):
        for n, pat in scan_key_patterns(p)[:3]:
            errors.append(f"campaigns/{p.name}: API-key pattern {pat} at line {n}")
    for t, e, r in sorted(selected - seen):
        errors.append(f"leaderboards/{t}.json: selected run {e}/{r} has no record under runs/{t}/{e}/{r}/")
    for t, e, r in sorted(excluded - seen):
        errors.append(f"leaderboards/{t}.json: excluded run {e}/{r} has no record under runs/{t}/{e}/{r}/")
    counts = dict(records=len(seen), checked=n_checked, selected=len(selected), excluded=len(excluded),
                  models=len(models), leaderboards=len(boards))
    return errors, warnings, counts


# --------------------------------------------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------------------------------------------

def _split_run_ref(ref: str) -> tuple[str, str, str]:
    parts = ref.strip("/").split("/")
    if len(parts) != 3 or not RUN_ID_RE.match(parts[2]):
        raise SystemExit(f"error: '{ref}' is not <task>/<entry>/<run-id>")
    return parts[0], parts[1], parts[2]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rtlscout_bench.record", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="a runs directory (default: runs), a campaign JSON, or raw run "
                                             "directories (these need --task and --entry)")
    ap.add_argument("--matrix", help="matrix file (default: ./campaign_matrix.yaml)")
    ap.add_argument("--data", default="data", help="data repository checkout (default: data)")
    ap.add_argument("--force", action="store_true", help="rewrite runs that are already recorded")
    ap.add_argument("--dry-run", action="store_true", help="report what would be recorded; write nothing")
    g = ap.add_argument_group("adopting run directories that no campaign file describes")
    g.add_argument("--task", help="task id the runs belong to")
    g.add_argument("--entry", help="entry id the runs belong to (its conditions are taken from the matrix)")
    g.add_argument("--origin", choices=[o for o in ORIGINS if o != "campaign"], help="what the runs are")
    g.add_argument("--suite", help="suite tag to write (default: the matrix suite; required for --origin seed)")
    g.add_argument("--rtlscout", help="rtlscout version that produced the runs, as free text")
    g.add_argument("--spire-hdl", help="spire-hdl version that produced the runs (default: null)")
    g.add_argument("--reasoning-effort-source", metavar="TEXT",
                   help="where the reasoning effort is known from when the run directory does not record it")
    c = ap.add_argument_group("curation and checks")
    c.add_argument("--check", action="store_true", help="validate the data repository and exit")
    c.add_argument("--exclude", metavar="TASK/ENTRY/RUN", help="take a run out of the leaderboard (needs --reason)")
    c.add_argument("--reason", help="why the run is excluded; shown on the page")
    c.add_argument("--include", metavar="TASK/ENTRY/RUN", help="put an excluded run back into the leaderboard")
    args = ap.parse_args(argv)

    try:
        matrix = load_matrix(args.matrix)
    except MatrixError as exc:
        print(f"{exc.path}: invalid matrix\n" + "\n".join(f"  - {p}" for p in exc.problems), file=sys.stderr)
        return 2
    data = Path(args.data)

    if args.check:
        errors, warnings, n = check_data(data, matrix)
        for w in warnings:
            print(f"warning: {w}")
        for e in errors:
            print(f"ERROR: {e}")
        policy = "on" if matrix.open_weights_only else "off"
        print(f"data check: {n['records']} records ({n['selected']} selected, {n['excluded']} excluded), "
              f"{n['leaderboards']} leaderboard file(s), {n['models']} models, open-weights policy {policy}: "
              + ("OK" if not errors else f"{len(errors)} error(s)"))
        return 1 if errors else 0

    if args.exclude or args.include:
        if args.exclude and not args.reason:
            ap.error("--exclude needs --reason \"...\" (the reason is published with the leaderboard)")
        task, entry, run_id = _split_run_ref(args.exclude or args.include)
        if not (run_path(data, task, entry, run_id) / "record.json").is_file():
            print(f"error: no record at {run_path(data, task, entry, run_id)}", file=sys.stderr)
            return 1
        lb = load_leaderboard(data, task)
        if args.exclude:
            exclude_run(lb, entry, run_id, args.reason)
            print(f"excluded {task}/{entry}/{run_id}: {args.reason}")
        else:
            include_run(lb, entry, run_id)
            print(f"included {task}/{entry}/{run_id}")
        save_leaderboard(data, lb)
        print(f"updated {leaderboard_path(data, task)}; commit it in the data repo to make it count")
        return 0

    if not data.is_dir():
        print(f"error: data directory {data} not found (is the data submodule checked out?)", file=sys.stderr)
        return 1
    models = load_models(data)
    paths = [Path(p) for p in (args.paths or ["runs"])]
    missing = [str(p) for p in paths if not p.exists()]
    if missing and args.paths:
        print(f"error: not found: {', '.join(missing)}", file=sys.stderr)
        return 1
    raw = [p for p in paths if (p / "result.json").is_file()]
    if raw and len(raw) != len(paths):
        ap.error("give either raw run directories or runs directories / campaign files, not both")
    total = dict(recorded=0, already=0, skipped=0, refused=0)
    try:
        if raw:
            if not (args.task and args.entry and args.origin):
                ap.error("raw run directories need --task, --entry and --origin")
            if args.origin == "seed" and not (args.suite and args.rtlscout):
                ap.error("--origin seed needs --suite and --rtlscout: seed runs are marked with what they really are")
            if not args.rtlscout:
                ap.error("--rtlscout is required for adopted runs (the version that produced them)")
            print(f"recording {len(raw)} run(s) as {args.task}/{args.entry} "
                  f"(origin {args.origin}, suite {args.suite or matrix.suite})")
            counts = record_run_dirs(
                raw, data, matrix, models, task_id=args.task, entry_id=args.entry, origin=args.origin,
                suite=args.suite or matrix.suite, rtlscout=args.rtlscout, spire_hdl=args.spire_hdl,
                effort_source=args.reasoning_effort_source, bench_commit=git_commit(matrix.path.parent),
                force=args.force, dry_run=args.dry_run)
            for k in total:
                total[k] += counts[k]
        else:
            files = [f for p in paths for f in _campaign_files(p)]
            if not files:
                print(f"no campaign files under {', '.join(str(p) for p in paths)} (nothing to record)")
            for f in files:
                print(f"campaign {f}")
                counts = record_campaign(f, data, matrix, models, force=args.force, dry_run=args.dry_run)
                for k in total:
                    total[k] += counts[k]
    except (RecordError, KeyError) as exc:
        print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 1
    verb = "would record" if args.dry_run else "recorded"
    print(f"{verb} {total['recorded']}, already recorded {total['already']}, skipped {total['skipped']}, "
          f"refused {total['refused']}")
    return 1 if total["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
