"""Load and validate ``campaign_matrix.yaml``: the tasks (what is scored), the entries (who is scored) and the policy.

    python -m rtlscout_bench.config                  # validate + print the resolved matrix
    python -m rtlscout_bench.config --matrix my.yaml

The matrix is the only place where campaign conditions live. A *task* is a benchmark plus its cost metric and
the conditions that belong to that metric; an *entry* is a model under one fixed set of agent conditions and
becomes one leaderboard row. Scores are only ever compared within one task.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_MATRIX = "campaign_matrix.yaml"

LANGUAGE_SUFFIX = {"verilog": "_verilog", "spirehdl": "_spire"}
LANGUAGE_LABEL = {"verilog": "Verilog", "spirehdl": "Spire HDL"}
REASONING_EFFORTS = ("default", "low", "medium", "high")
AGENT_BACKENDS = ("react",)

# Scoring is a property of the task. These flags may only appear in a task; an entry that needs a different
# objective is a different task, so every row of one table stays comparable.
SCORING_FLAGS = ("--skip-cec", "--skip-netlist-sim")
# Options the tooling sets itself from dedicated matrix fields; never valid inside a `flags` list.
RESERVED_FLAGS = ("--benchmark", "--benchmarks-root", "--model", "--language", "--runs-dir", "--max-steps",
                  "--cost-metric", "--target-delay", "--technology", "--energy-exp", "--agent-backend", "--api-key")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_TASK_KEYS = {"benchmark", "cost_metric", "target_delay", "technology", "energy_exp", "flags", "env", "heavy",
              "enabled"}
_ENTRY_KEYS = {"id", "model", "language", "repeats", "max_steps", "reasoning_effort", "agent_backend", "flags",
               "tasks", "enabled"}
_DEFAULT_KEYS = _ENTRY_KEYS - {"id", "model"}
_TOP_KEYS = {"suite", "policy", "tasks", "defaults", "entries", "benchmarks_root"}


class MatrixError(ValueError):
    """The matrix file is invalid. ``problems`` lists every issue found, one line each."""

    def __init__(self, path: Path, problems: list[str]):
        self.path, self.problems = path, problems
        super().__init__(f"{path}: " + "; ".join(problems))


@dataclass(frozen=True)
class Task:
    id: str
    benchmark: str
    cost_metric: str
    target_delay: float | None = None
    technology: str | None = None
    energy_exp: float | None = None
    flags: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    heavy: bool = False
    enabled: bool = True

    def bench_name(self, language: str) -> str:
        """Directory (and rtlscout benchmark) name for one language: ``<benchmark>_verilog`` / ``<benchmark>_spire``."""
        return self.benchmark + LANGUAGE_SUFFIX[language]


@dataclass(frozen=True)
class Entry:
    id: str
    model: str
    language: str = "verilog"
    repeats: int = 3
    max_steps: int = 60
    reasoning_effort: str = "default"
    agent_backend: str = "react"
    flags: tuple[str, ...] = ()
    tasks: tuple[str, ...] = ()
    enabled: bool = True


@dataclass
class Matrix:
    path: Path
    suite: str
    open_weights_only: bool
    tasks: dict[str, Task]
    entries: list[Entry]
    benchmarks_root: Path
    warnings: list[str] = field(default_factory=list)

    def entry(self, entry_id: str) -> Entry:
        for e in self.entries:
            if e.id == entry_id:
                return e
        raise KeyError(f"no entry '{entry_id}' in {self.path} (known: {', '.join(e.id for e in self.entries)})")

    def task(self, task_id: str) -> Task:
        if task_id not in self.tasks:
            raise KeyError(f"no task '{task_id}' in {self.path} (known: {', '.join(self.tasks)})")
        return self.tasks[task_id]

    def enabled_tasks(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.enabled]

    def jobs(self, include_disabled: bool = False) -> list[tuple[Task, Entry]]:
        """Every (task, entry) combination the matrix asks for, in file order (entries outer, tasks inner)."""
        out = []
        for e in self.entries:
            for tid in e.tasks:
                t = self.tasks[tid]
                if include_disabled or (e.enabled and t.enabled):
                    out.append((t, e))
        return out

    def bench_dir(self, task: Task, language: str) -> Path:
        return self.benchmarks_root / task.bench_name(language)

    def conditions(self, task: Task, entry: Entry) -> dict:
        """The conditions written into every run record of this (task, entry); see ``CONDITION_FIELDS``."""
        return dict(task=task.id, benchmark=task.bench_name(entry.language), language=entry.language,
                    cost_metric=task.cost_metric, target_delay=task.target_delay, technology=task.technology or "asap7",
                    entry=entry.id, model=entry.model, reasoning_effort=entry.reasoning_effort,
                    agent_backend=entry.agent_backend, max_steps=entry.max_steps,
                    flags=list(task.flags) + list(entry.flags), env=dict(task.env))


# Fields that must agree for two runs to share a leaderboard row (and for a recorded run to count towards an
# entry's repeat count).
CONDITION_FIELDS = ("task", "benchmark", "language", "cost_metric", "target_delay", "technology", "entry", "model",
                    "reasoning_effort", "agent_backend", "max_steps", "flags", "env")


def _check_flags(where: str, flags, problems: list[str], *, scoring_allowed: bool) -> tuple[str, ...]:
    if flags is None:
        return ()
    if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
        problems.append(f"{where}: flags must be a list of strings")
        return ()
    for f in flags:
        name = f.split("=", 1)[0]
        if name in RESERVED_FLAGS:
            problems.append(f"{where}: flag {name} is set by the tooling from its own matrix field; remove it "
                            f"from flags")
        elif name in SCORING_FLAGS and not scoring_allowed:
            problems.append(f"{where}: {name} is a scoring flag and belongs to the task, not to an entry "
                            f"(a different objective is a new task)")
        elif not f.startswith("--"):
            problems.append(f"{where}: flag {f!r} must start with '--'")
    return tuple(flags)


def _unknown_keys(where: str, d: dict, allowed: set[str], problems: list[str]) -> None:
    for k in d:
        if k not in allowed:
            problems.append(f"{where}: unknown key '{k}' (allowed: {', '.join(sorted(allowed))})")


def _num(where: str, v, problems: list[str]) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        problems.append(f"{where} must be a number")
        return None
    return float(v)


def parse_matrix(raw, path: Path) -> Matrix:
    """Validate the parsed YAML document and build a :class:`Matrix`. Raises :class:`MatrixError` with all problems."""
    problems: list[str] = []
    warnings: list[str] = []
    if not isinstance(raw, dict):
        raise MatrixError(path, ["top level must be a mapping with the keys suite, policy, tasks, defaults, entries"])
    _unknown_keys("top level", raw, _TOP_KEYS, problems)

    suite = raw.get("suite")
    if not isinstance(suite, str) or not suite:
        problems.append("suite: required, a non-empty string such as 'suite-v1'")
        suite = ""
    policy = raw.get("policy") or {}
    if not isinstance(policy, dict):
        problems.append("policy: must be a mapping")
        policy = {}
    _unknown_keys("policy", policy, {"open_weights_only"}, problems)
    open_only = policy.get("open_weights_only", True)
    if not isinstance(open_only, bool):
        problems.append("policy.open_weights_only: must be true or false")
        open_only = True

    tasks: dict[str, Task] = {}
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, dict) or not raw_tasks:
        problems.append("tasks: required, a mapping of task id -> task")
        raw_tasks = {}
    for tid, t in raw_tasks.items():
        where = f"tasks.{tid}"
        if not isinstance(tid, str) or not _ID_RE.match(tid):
            problems.append(f"{where}: task ids use lower-case letters, digits, '.', '_' and '-'")
            continue
        if not isinstance(t, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        _unknown_keys(where, t, _TASK_KEYS, problems)
        for req in ("benchmark", "cost_metric"):
            if not isinstance(t.get(req), str) or not t.get(req):
                problems.append(f"{where}.{req}: required, a string")
        env = t.get("env") or {}
        if not isinstance(env, dict) or not all(isinstance(k, str) for k in env):
            problems.append(f"{where}.env: must be a mapping of variable name -> value")
            env = {}
        for flag_key in ("heavy", "enabled"):
            if not isinstance(t.get(flag_key, False), bool):
                problems.append(f"{where}.{flag_key}: must be true or false")
        if t.get("technology") is not None and not isinstance(t["technology"], str):
            problems.append(f"{where}.technology: must be a string")
        tasks[tid] = Task(
            id=tid, benchmark=str(t.get("benchmark") or ""), cost_metric=str(t.get("cost_metric") or ""),
            target_delay=_num(f"{where}.target_delay", t.get("target_delay"), problems),
            technology=t.get("technology") if isinstance(t.get("technology"), str) else None,
            energy_exp=_num(f"{where}.energy_exp", t.get("energy_exp"), problems),
            flags=_check_flags(where, t.get("flags"), problems, scoring_allowed=True),
            env=tuple(sorted((k, str(v)) for k, v in env.items())),
            heavy=t.get("heavy", False) is True, enabled=t.get("enabled", True) is not False)

    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        problems.append("defaults: must be a mapping")
        defaults = {}
    _unknown_keys("defaults", defaults, _DEFAULT_KEYS, problems)

    entries: list[Entry] = []
    raw_entries = raw.get("entries")
    if not isinstance(raw_entries, list):
        problems.append("entries: required, a list of entries")
        raw_entries = []
    seen: set[str] = set()
    for i, e in enumerate(raw_entries):
        if not isinstance(e, dict):
            problems.append(f"entries[{i}]: must be a mapping")
            continue
        eid = e.get("id")
        where = f"entries[{i}]" if not isinstance(eid, str) else f"entry {eid}"
        _unknown_keys(where, e, _ENTRY_KEYS, problems)
        if not isinstance(eid, str) or not _ID_RE.match(eid):
            problems.append(f"{where}: id is required and uses lower-case letters, digits, '.', '_' and '-' "
                            f"(it becomes a directory name in the data repo)")
            continue
        if eid in seen:
            problems.append(f"{where}: duplicate id (two rows of the same model need two different ids)")
            continue
        seen.add(eid)
        merged = {**defaults, **e}
        model = merged.get("model")
        if not isinstance(model, str) or ":" not in model or not all(model.split(":", 1)):
            problems.append(f"{where}: model is required as '<provider>:<model>', e.g. openrouter:vendor/model-name")
            model = str(model or "")
        language = merged.get("language", "verilog")
        if language not in LANGUAGE_SUFFIX:
            problems.append(f"{where}: language must be one of {', '.join(LANGUAGE_SUFFIX)}")
            language = "verilog"
        effort = merged.get("reasoning_effort", "default")
        if effort not in REASONING_EFFORTS:
            problems.append(f"{where}: reasoning_effort must be one of {', '.join(REASONING_EFFORTS)}")
            effort = "default"
        backend = merged.get("agent_backend", "react")
        if backend not in AGENT_BACKENDS:
            problems.append(f"{where}: agent_backend must be 'react' (the only backend the leaderboard uses for now)")
            backend = "react"
        ints = {}
        for key, dflt in (("repeats", 3), ("max_steps", 60)):
            v = merged.get(key, dflt)
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                problems.append(f"{where}: {key} must be a positive integer")
                v = dflt
            ints[key] = v
        if not isinstance(merged.get("enabled", True), bool):
            problems.append(f"{where}: enabled must be true or false")
        etasks = merged.get("tasks")
        if etasks is None:
            problems.append(f"{where}: no tasks (give the entry a tasks list or set defaults.tasks)")
            etasks = []
        if not isinstance(etasks, list) or not all(isinstance(t, str) for t in etasks):
            problems.append(f"{where}: tasks must be a list of task ids")
            etasks = []
        for t in etasks:
            if t not in tasks:
                problems.append(f"{where}: unknown task '{t}' (known: {', '.join(tasks) or 'none'})")
        # like every other field, an entry's own flags replace defaults.flags (they are not concatenated)
        flags = (_check_flags(where, e["flags"], problems, scoring_allowed=False) if "flags" in e
                 else _check_flags("defaults", defaults.get("flags"), [], scoring_allowed=False))
        entries.append(Entry(
            id=eid, model=model, language=language, repeats=ints["repeats"], max_steps=ints["max_steps"],
            reasoning_effort=effort, agent_backend=backend, flags=flags,
            tasks=tuple(t for t in etasks if t in tasks), enabled=merged.get("enabled", True) is not False))
    _check_flags("defaults", defaults.get("flags"), problems, scoring_allowed=False)

    broot = raw.get("benchmarks_root", "benchmarks")
    if not isinstance(broot, str):
        problems.append("benchmarks_root: must be a path (relative to the matrix file)")
        broot = "benchmarks"
    benchmarks_root = (path.parent / broot)

    if problems:
        raise MatrixError(path, problems)

    matrix = Matrix(path=path, suite=suite, open_weights_only=open_only, tasks=tasks, entries=entries,
                    benchmarks_root=benchmarks_root, warnings=warnings)
    # Every enabled (task, entry) needs its benchmark directory; disabled examples may point at benchmarks
    # that are not in the suite yet.
    missing: list[str] = []
    for t, e in matrix.jobs():
        d = matrix.bench_dir(t, e.language)
        lacking = [f for f in ("description.txt", "metadata.json", "tb.sv") if not (d / f).is_file()]
        if lacking:
            msg = (f"task {t.id}: benchmark directory {_rel(d)} ({e.language} entries) is missing or incomplete "
                   f"(needs description.txt, metadata.json, tb.sv)")
            if msg not in missing:
                missing.append(msg)
    if missing:
        raise MatrixError(path, missing)
    for e in entries:
        if e.enabled and not any(tasks[t].enabled for t in e.tasks):
            warnings.append(f"entry {e.id} is enabled but none of its tasks is: it will not run")
    return matrix


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(p)


def load_matrix(path: str | Path | None = None) -> Matrix:
    """Read ``campaign_matrix.yaml`` (in the current directory unless *path* is given)."""
    p = Path(path) if path else Path(DEFAULT_MATRIX)
    if not p.is_file():
        hint = "" if path else " (run from the root of an rtlscout-bench checkout, or pass --matrix)"
        raise MatrixError(p, [f"matrix file not found{hint}"])
    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise MatrixError(p, [f"not valid YAML: {exc}"]) from exc
    return parse_matrix(raw, p)


def describe(matrix: Matrix) -> str:
    """Human-readable dump of the resolved matrix (what ``python -m rtlscout_bench.config`` prints)."""
    on = lambda b: "enabled" if b else "disabled"
    lines = [f"matrix: {_rel(matrix.path)}", f"suite:  {matrix.suite}",
             f"policy: open_weights_only = {str(matrix.open_weights_only).lower()}", "", "tasks"]
    for t in matrix.tasks.values():
        extra = []
        if t.target_delay is not None:
            extra.append(f"target_delay={t.target_delay:g}")
        if t.technology:
            extra.append(f"technology={t.technology}")
        if t.energy_exp is not None:
            extra.append(f"energy_exp={t.energy_exp:g}")
        if t.flags:
            extra.append(" ".join(t.flags))
        extra += [f"{k}={v}" for k, v in t.env]
        if t.heavy:
            extra.append("heavy")
        lines.append(f"  {t.id:<18} {on(t.enabled):<9} benchmark {t.benchmark:<13} metric {t.cost_metric:<24} "
                     f"{' '.join(extra)}".rstrip())
    lines += ["", "entries"]
    for e in matrix.entries:
        cond = [e.language, f"{e.max_steps} steps", f"effort {e.reasoning_effort}", f"x{e.repeats}"]
        if e.flags:
            cond.append(" ".join(e.flags))
        lines.append(f"  {e.id:<26} {on(e.enabled):<9} {e.model:<46} {', '.join(cond)}  -> {', '.join(e.tasks)}")
    lines += ["", "jobs (enabled task x enabled entry)"]
    jobs = matrix.jobs()
    for t, e in jobs:
        lines.append(f"  {t.id + ' / ' + e.id:<40} {_rel(matrix.bench_dir(t, e.language)):<28} "
                     f"{e.repeats} run{'' if e.repeats == 1 else 's'}")
    if not jobs:
        lines.append("  (none)")
    for w in matrix.warnings:
        lines.append(f"warning: {w}")
    n_entries = len({e.id for _, e in jobs})
    n_tasks = len({t.id for t, _ in jobs})
    n_runs = sum(e.repeats for _, e in jobs)
    lines += ["", f"OK: {n_tasks} enabled task(s), {n_entries} enabled entr{'y' if n_entries == 1 else 'ies'}, "
                  f"{n_runs} run{'' if n_runs == 1 else 's'} for a full campaign"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rtlscout_bench.config", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", help=f"matrix file (default: ./{DEFAULT_MATRIX})")
    args = ap.parse_args(argv)
    try:
        matrix = load_matrix(args.matrix)
    except MatrixError as exc:
        print(f"{exc.path}: invalid matrix", file=sys.stderr)
        for p in exc.problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    print(describe(matrix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
