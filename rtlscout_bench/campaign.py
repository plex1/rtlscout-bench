"""Run what the campaign matrix still needs: one baseline per task and language, N repeats per (task, entry).

    python -m rtlscout_bench.campaign                    # every enabled entry that is short of its repeat count
    python -m rtlscout_bench.campaign --dry-run          # print the plan and the exact commands, launch nothing
    python -m rtlscout_bench.campaign --baselines-only   # measure the shipped starting points only
    python -m rtlscout_bench.campaign --entry glm-5.2 -n 1 --parallel 1     # one extra run of one entry

Each run is ``python -m rtlscout.run_benchmark`` with the task's metric and flags and the entry's model and agent
conditions; each baseline is ``python -m rtlscout.run_eval`` on the benchmark's shipped starting point with the
same metric and scoring flags (an agent run's first evaluation is not used as the baseline: it can fail, or the
agent may have edited the design already).

Re-invoking resumes: runs already recorded in ``data/`` for the current suite, and finished runs of earlier
invocations that are still waiting under ``runs/``, count towards an entry's ``repeats``. Scheduling: at most
``--parallel`` runs at once, never two tasks marked ``heavy`` together, launches two seconds apart so the
second-stamped run directories stay unique.

Raw output goes to ``runs/`` (scratch, not in git):
    runs/<task>/<entry>/<benchmark>/<model>/<run-id>/    the run directory rtlscout writes
    runs/logs/<campaign>/<task>__<entry>__r<k>.log       stdout + stderr of each run; baseline__*.log
    runs/campaigns/<campaign>.json                       conditions, versions, baselines and the run list
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import Entry, Matrix, MatrixError, Task, load_matrix
from .record import SCHEMA, conditions_of, git_commit, iter_records, load_leaderboard, load_models

_RUNDIR_RE = re.compile(r"^Results stored in:\s*(\S+)", re.M)
_WORKDIR_RE = re.compile(r"^Workdir:\s*(\S+)", re.M)
LAUNCH_STAGGER_S = 2.0


def installed_version(dist: str) -> str | None:
    """Version of an installed distribution, without importing it."""
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


# --------------------------------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------------------------------

def _metric_args(task: Task) -> list[str]:
    args = ["--cost-metric", task.cost_metric]
    if task.target_delay is not None:
        args += ["--target-delay", f"{task.target_delay:g}"]
    if task.technology:
        args += ["--technology", task.technology]
    if task.energy_exp is not None:
        args += ["--energy-exp", f"{task.energy_exp:g}"]
    return args


def entry_runs_dir(runs_dir: Path, task: Task, entry: Entry) -> Path:
    """One runs directory per (task, entry): two entries of the same model never write into the same tree."""
    return runs_dir / task.id / entry.id


def run_cmd(matrix: Matrix, task: Task, entry: Entry, runs_dir: Path) -> list[str]:
    """The agent run, in portable form (``python`` stands for the interpreter that runs the campaign)."""
    cmd = ["python", "-m", "rtlscout.run_benchmark", "--benchmark", task.bench_name(entry.language),
           "--language", entry.language, "--model", entry.model, "--max-steps", str(entry.max_steps),
           "--benchmarks-root", str(matrix.benchmarks_root), "--runs-dir", str(entry_runs_dir(runs_dir, task, entry))]
    cmd += _metric_args(task)
    if entry.agent_backend != "react":
        cmd += ["--agent-backend", entry.agent_backend]
    return cmd + list(task.flags) + list(entry.flags)


def starting_point(bench_dir: Path) -> Path | None:
    try:
        named = json.loads((bench_dir / "metadata.json").read_text()).get("starting_point")
    except (OSError, ValueError):
        named = None
    if named and (bench_dir / named).is_file():
        return bench_dir / named
    found = sorted((bench_dir / "context").glob("starting_point.*"))
    return found[0] if found else None


def baseline_cmd(matrix: Matrix, task: Task, language: str) -> list[str] | None:
    """``rtlscout.run_eval`` on the shipped starting point: same metric and scoring flags, no agent-side flags."""
    bench_dir = matrix.bench_dir(task, language)
    sp = starting_point(bench_dir)
    if sp is None:
        return None
    return ["python", "-m", "rtlscout.run_eval", str(sp), "--benchmark", str(bench_dir)] + _metric_args(task) \
        + [f for f in task.flags if f.startswith("--skip")] + ["--json"]


def run_env(task: Task, entry: Entry | None) -> dict[str, str]:
    """Environment of a child process: the task's variables, and the entry's reasoning effort and nothing else's."""
    env = dict(os.environ)
    env.pop("RTLSCOUT_REASONING_EFFORT", None)      # an ambient value must not leak into 'default' entries
    env.update(dict(task.env))
    if entry is not None and entry.reasoning_effort != "default":
        env["RTLSCOUT_REASONING_EFFORT"] = entry.reasoning_effort
    return env


def parse_eval_json(text: str) -> dict:
    """``run_eval --json`` prints header lines and then one JSON object: return that object ({} if there is none)."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"^\{", text, re.M):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


# --------------------------------------------------------------------------------------------------------------
# what is still needed
# --------------------------------------------------------------------------------------------------------------

def _local_campaigns(runs_dir: Path) -> list[dict]:
    out = []
    for p in sorted((runs_dir / "campaigns").glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except ValueError:
            print(f"warning: ignoring unreadable campaign file {p}", file=sys.stderr)
    return out


def completed_runs(matrix: Matrix, data: Path, runs_dir: Path) -> dict[tuple[str, str], set[str]]:
    """Run ids that count towards each (task, entry): current suite, finished, same conditions, not excluded."""
    done: dict[tuple[str, str], set[str]] = {}
    want = {(t.id, e.id): matrix.conditions(t, e) for t, e in matrix.jobs(include_disabled=True)}
    excluded = set()
    for task_id in matrix.tasks:
        excluded |= {(task_id, x.get("entry"), x.get("run")) for x in load_leaderboard(data, task_id)["exclude"]}
    for task_id, entry_id, run_id, path in iter_records(data):
        key = (task_id, entry_id)
        if key not in want or (task_id, entry_id, run_id) in excluded:
            continue
        rec = json.loads(path.read_text())
        if (rec.get("suite") == matrix.suite and rec.get("origin") != "seed" and rec.get("status") == "completed"
                and conditions_of(rec) == want[key]):
            done.setdefault(key, set()).add(run_id)
    for camp in _local_campaigns(runs_dir):
        if camp.get("campaign", {}).get("suite") != matrix.suite:
            continue
        for r in camp.get("runs", []):
            key = (r.get("task"), r.get("entry"))
            if (key in want and r.get("status") == "completed" and r.get("run_id")
                    and (key[0], key[1], r["run_id"]) not in excluded and conditions_of(r) == want[key]):
                done.setdefault(key, set()).add(r["run_id"])
    return done


def known_baselines(matrix: Matrix, data: Path, runs_dir: Path, rtlscout_version: str | None) -> set[str]:
    """``<task>/<language>`` keys whose starting point is already measured for this suite and rtlscout version."""
    known = set()
    for task_id in matrix.tasks:
        for lang, b in load_leaderboard(data, task_id)["baselines"].items():
            if b.get("cost") and b.get("suite") == matrix.suite and b.get("rtlscout") == rtlscout_version:
                known.add(f"{task_id}/{lang}")
    for camp in _local_campaigns(runs_dir):
        meta = camp.get("campaign", {})
        if meta.get("suite") == matrix.suite and meta.get("rtlscout") == rtlscout_version:
            known |= {k for k, b in camp.get("baselines", {}).items() if b.get("status") == "ok"}
    return known


def plan(matrix: Matrix, data: Path, runs_dir: Path, *, entries: list[str] | None = None,
         tasks: list[str] | None = None, n: int | None = None) -> tuple[list[dict], list[str]]:
    """The run jobs to launch, plus one status line per (task, entry) considered.

    Without ``entries`` the enabled entries are topped up to their ``repeats``. Naming an entry selects it even
    when it is disabled; ``n`` launches exactly that many additional runs instead of topping up.
    """
    for e in entries or []:
        matrix.entry(e)
    for t in tasks or []:
        matrix.task(t)
    done = completed_runs(matrix, data, runs_dir)
    jobs, lines = [], []
    for task, entry in matrix.jobs(include_disabled=True):
        if tasks and task.id not in tasks:
            continue
        if entries:
            if entry.id not in entries:
                continue
        elif not entry.enabled:
            continue
        if not task.enabled and not (tasks and task.id in tasks):
            continue
        have = len(done.get((task.id, entry.id), ()))
        todo = n if n is not None else max(0, entry.repeats - have)
        lines.append(f"{task.id + ' / ' + entry.id:<40} {have} of {entry.repeats} runs done, {todo} to launch"
                     + ("" if entry.enabled else "  (entry is disabled in the matrix; launched because it was named)"))
        for i in range(todo):
            jobs.append(dict(task=task, entry=entry, repeat=have + i, order=i))
    # first one run of every entry, then the second of every entry, ...: concurrent runs then hit different
    # providers, and an interrupted campaign leaves every row with some runs rather than one row complete
    jobs.sort(key=lambda j: j["order"])
    return jobs, lines


# --------------------------------------------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------------------------------------------

class Campaign:
    def __init__(self, matrix: Matrix, data: Path, runs_dir: Path, jobs: list[dict], baselines: list[tuple[Task, str]],
                 *, name: str, parallel: int, argv: list[str], versions: dict):
        self.matrix, self.data, self.runs_dir = matrix, data, runs_dir
        self.jobs, self.baseline_jobs = jobs, baselines
        self.name, self.parallel = name, parallel
        self.out_path = runs_dir / "campaigns" / f"{name}.json"
        self.log_dir = runs_dir / "logs" / name
        self.results: list[dict] = []
        self.baselines: dict[str, dict] = {}
        self.cv = threading.Condition()
        self.running_total = 0
        self.running_heavy = 0
        self.procs: set[subprocess.Popen] = set()
        self.stop = False
        self.meta = dict(name=name, suite=matrix.suite, started=datetime.now().isoformat(timespec="seconds"),
                         argv=argv, matrix=matrix.path.name, open_weights_only=matrix.open_weights_only,
                         parallel=parallel, **versions)

    def _write(self) -> None:
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.out_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(schema=SCHEMA, campaign=self.meta, baselines=self.baselines,
                                       runs=self.results), indent=1))
        tmp.replace(self.out_path)

    def _exec(self, cmd: list[str], env: dict, log: Path) -> int:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "w") as lf:
            lf.write("$ " + " ".join(cmd) + "\n")
            lf.flush()
            proc = subprocess.Popen([sys.executable] + cmd[1:], env=env, stdout=lf, stderr=subprocess.STDOUT, text=True)
            with self.cv:
                self.procs.add(proc)
            try:
                return proc.wait()
            finally:
                with self.cv:
                    self.procs.discard(proc)

    def _finish(self, heavy: bool) -> None:
        self.running_total -= 1
        self.running_heavy -= 1 if heavy else 0
        self._write()
        self.cv.notify_all()

    def _run_baseline(self, task: Task, language: str) -> None:
        key = f"{task.id}/{language}"
        cmd = baseline_cmd(self.matrix, task, language)
        rec = dict(task=task.id, language=language, benchmark=task.bench_name(language), cost=None, area=None,
                   delay=None, status="no-starting-point", cmd=cmd, env=dict(task.env), log=None)
        if cmd is not None:
            log = self.log_dir / f"baseline__{task.id}__{language}.log"
            rc = self._exec(cmd, run_env(task, None), log)
            j = parse_eval_json(log.read_text(errors="replace"))
            m = j.get("metrics") or {}
            ok = j.get("cost_value") is not None and j.get("passed") is not False
            rec.update(cost=j.get("cost_value"), area=m.get("area"), delay=m.get("delay") or m.get("delay_ps"),
                       status="ok" if ok else f"failed(rc={rc})", log=str(log))
        with self.cv:
            self.baselines[key] = rec
            self._finish(task.heavy)
        cost = "n/a" if rec["cost"] is None else f"{rec['cost']:,.2f}"
        print(f"[baseline] {key}: {rec['status']}  cost {cost}", flush=True)

    def _run_one(self, job: dict) -> None:
        task, entry = job["task"], job["entry"]
        cmd = run_cmd(self.matrix, task, entry, self.runs_dir)
        log = self.log_dir / f"{task.id}__{entry.id}__r{job['repeat']}.log"
        t0 = time.time()
        rc = self._exec(cmd, run_env(task, entry), log)
        text = log.read_text(errors="replace")
        found = _RUNDIR_RE.findall(text) or _WORKDIR_RE.findall(text)
        run_dir = Path(found[-1]) if found else None
        rec = dict(self.matrix.conditions(task, entry), repeat=job["repeat"], cmd=cmd, returncode=rc,
                   duration_s=round(time.time() - t0, 1), log=str(log), run_dir=None, run_id=None,
                   best_cost=None, best_eval=None, n_evals=0)
        status = "launch-failed"
        if run_dir is not None and run_dir.is_dir():
            rec["run_dir"] = os.path.relpath(run_dir, self.runs_dir)
            rec["run_id"] = run_dir.name
            status = "ended-early"
            try:
                res = json.loads((run_dir / "result.json").read_text())
                rec["n_evals"] = len(res.get("all_evals") or [])
                if rc == 0 and not res.get("error"):
                    status = "completed"
            except (OSError, ValueError):
                pass
            try:
                meta = json.loads((run_dir / "best_design" / "_best_meta.json").read_text())
                rec["best_cost"], rec["best_eval"] = meta.get("best_cost"), meta.get("eval_index")
            except (OSError, ValueError):
                pass
        rec["status"] = status
        with self.cv:
            self.results.append(rec)
            self._finish(task.heavy)
        best = "none" if rec["best_cost"] is None else f"{rec['best_cost']:,.2f}"
        print(f"[done] {task.id}/{entry.id} r{job['repeat']}: {status}  best {best}  run {rec['run_id']}  "
              f"({rec['duration_s'] / 60:.0f} min)", flush=True)

    def _can_start(self, heavy: bool) -> bool:
        return self.running_total < self.parallel and not (heavy and self.running_heavy >= 1)

    def run(self) -> int:
        pending: list[tuple] = [("baseline", t, lang) for t, lang in self.baseline_jobs]
        pending += [("run", j) for j in self.jobs]
        threads = []
        self._write()
        try:
            with self.cv:
                while pending or self.running_total:
                    item = next((p for p in pending
                                 if self._can_start((p[1] if p[0] == "baseline" else p[1]["task"]).heavy)), None)
                    if item is None:
                        self.cv.wait(timeout=30)
                        continue
                    pending.remove(item)
                    heavy = (item[1] if item[0] == "baseline" else item[1]["task"]).heavy
                    self.running_total += 1
                    self.running_heavy += 1 if heavy else 0
                    if item[0] == "baseline":
                        t = threading.Thread(target=self._run_baseline, args=(item[1], item[2]), daemon=True)
                        print(f"[start] baseline {item[1].id}/{item[2]}", flush=True)
                    else:
                        j = item[1]
                        t = threading.Thread(target=self._run_one, args=(j,), daemon=True)
                        print(f"[start] {j['task'].id}/{j['entry'].id} r{j['repeat']}", flush=True)
                    t.start()
                    threads.append(t)
                    self.cv.release()
                    try:
                        time.sleep(LAUNCH_STAGGER_S)        # run directories are stamped to the second
                    finally:
                        self.cv.acquire()
        except KeyboardInterrupt:
            self.stop = True
            with self.cv:
                procs = list(self.procs)
            print(f"\ninterrupted: stopping {len(procs)} running process(es); finished runs are kept, "
                  f"re-run the same command to resume", flush=True)
            for p in procs:
                p.terminate()
        for t in threads:
            t.join(timeout=60 if self.stop else None)
        self.meta["finished"] = datetime.now().isoformat(timespec="seconds")
        if self.stop:
            self.meta["interrupted"] = True
        with self.cv:
            self._write()
        n_ok = sum(1 for r in self.results if r["status"] == "completed")
        n_base = sum(1 for b in self.baselines.values() if b["status"] == "ok")
        print(f"campaign {self.name}: {n_ok} of {len(self.results)} runs completed, {n_base} baseline(s) measured "
              f"-> {self.out_path}")
        bad = [r for r in self.results if r["status"] != "completed"] + \
              [b for b in self.baselines.values() if b["status"] != "ok"]
        for r in bad:
            who = f"{r.get('task')}/{r.get('entry', r.get('language'))}"
            print(f"  not completed: {who}: {r['status']}, see {r.get('log')}")
        return 130 if self.stop else (1 if bad else 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rtlscout_bench.campaign", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", help="matrix file (default: ./campaign_matrix.yaml)")
    ap.add_argument("--data", default="data", help="data repository checkout, read to see what is already recorded")
    ap.add_argument("--runs-dir", default="runs", help="scratch directory for raw output (default: runs)")
    ap.add_argument("--parallel", type=int, default=2,
                    help="concurrent runs (default 2; 2-3 is the limit for one machine, heavy tasks never overlap)")
    ap.add_argument("--entry", action="append", metavar="ID", help="only this entry (repeatable); also launches a "
                                                                    "disabled entry")
    ap.add_argument("--task", action="append", metavar="ID", help="only this task (repeatable)")
    ap.add_argument("-n", "--runs", type=int, metavar="N", help="launch exactly N more runs per selected (task, entry) "
                                                                 "instead of topping up to its repeats")
    ap.add_argument("--baselines-only", action="store_true", help="measure the shipped starting points, no agent runs")
    ap.add_argument("--name", help="campaign name (default: the start time, YYYYMMDD_HHMMSS)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and the commands, launch nothing")
    args = ap.parse_args(argv)
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    try:
        matrix = load_matrix(args.matrix)
        data, runs_dir = Path(args.data), Path(args.runs_dir)
        jobs, lines = plan(matrix, data, runs_dir, entries=args.entry, tasks=args.task, n=args.runs)
    except MatrixError as exc:
        print(f"{exc.path}: invalid matrix\n" + "\n".join(f"  - {p}" for p in exc.problems), file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    if args.parallel < 1:
        ap.error("--parallel must be at least 1")

    versions = dict(rtlscout=installed_version("rtlscout"), spire_hdl=installed_version("spire-hdl"),
                    bench_commit=git_commit(matrix.path.parent))
    known = known_baselines(matrix, data, runs_dir, versions["rtlscout"])
    if args.baselines_only:
        jobs = []
        pairs = {(t.id, e.language): (t, e.language) for t, e in matrix.jobs()
                 if (not args.task or t.id in args.task) and (not args.entry or e.id in args.entry)}
    else:
        pairs = {(j["task"].id, j["entry"].language): (j["task"], j["entry"].language) for j in jobs}
        pairs = {k: v for k, v in pairs.items() if f"{k[0]}/{k[1]}" not in known}
    baselines = list(pairs.values())

    print(f"suite {matrix.suite} · rtlscout {versions['rtlscout'] or 'not installed'} · "
          f"spire-hdl {versions['spire_hdl'] or 'not installed'} · parallel {args.parallel}")
    for line in lines:
        print(f"  {line}")
    if not jobs and not baselines:
        print("nothing to do: every selected entry has its runs. Use --entry <id> -n <N> for additional runs.")
        return 0

    if args.dry_run:
        for task, lang in baselines:
            cmd = baseline_cmd(matrix, task, lang)
            env = " ".join(f"{k}={v}" for k, v in task.env)
            shown = " ".join(cmd) if cmd else "NO STARTING POINT"
            print(f"[baseline] {task.id}/{lang}: {env + ' ' if env else ''}{shown}")
        for j in jobs:
            task, entry = j["task"], j["entry"]
            env = dict(task.env)
            if entry.reasoning_effort != "default":
                env["RTLSCOUT_REASONING_EFFORT"] = entry.reasoning_effort
            envs = " ".join(f"{k}={v}" for k, v in env.items())
            print(f"[run] {task.id}/{entry.id} r{j['repeat']}{' HEAVY' if task.heavy else ''}: "
                  f"{envs + ' ' if envs else ''}{' '.join(run_cmd(matrix, task, entry, runs_dir))}")
        print(f"dry run: {len(baselines)} baseline(s) and {len(jobs)} run(s) would be launched, nothing was started")
        return 0

    if versions["rtlscout"] is None:
        print("error: rtlscout is not installed in this environment (pip install -e . inside the rtlscout container)",
              file=sys.stderr)
        return 1
    if matrix.open_weights_only:
        models = load_models(data)
        closed = sorted({j["entry"].model for j in jobs
                         if models.get(j["entry"].model, {}).get("open_weights") is not True})
        if closed:
            print(f"error: policy.open_weights_only is on and these models are not marked open_weights in "
                  f"{data / 'models.json'}: {', '.join(closed)}. Their runs could not be recorded; nothing launched.",
                  file=sys.stderr)
            return 1
    if (versions["bench_commit"] or "").endswith("-dirty"):
        print("warning: the bench checkout has uncommitted changes; records will carry a '-dirty' commit id")
    name = args.name or datetime.now().strftime("%Y%m%d_%H%M%S")
    if (runs_dir / "campaigns" / f"{name}.json").exists():
        print(f"error: campaign {name} already exists under {runs_dir / 'campaigns'}; pick another --name",
              file=sys.stderr)
        return 1
    return Campaign(matrix, data, runs_dir, jobs, baselines, name=name, parallel=args.parallel, argv=raw_argv,
                    versions=versions).run()


if __name__ == "__main__":
    sys.exit(main())
