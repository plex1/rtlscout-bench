"""Shared fixtures: a miniature bench checkout and synthetic raw run directories.

Nothing here touches the real ``data/`` submodule or the network. Model names are invented.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "stub_rtlscout"

MATRIX = """\
suite: suite-t1
policy:
  open_weights_only: true
tasks:
  demo-adp:
    benchmark: demo
    cost_metric: area_delay_product
    flags: [--skip-cec]
  demo-heavy:
    benchmark: demo
    cost_metric: area_delay_product_fast
    env: {RTLSCOUT_ADP_FAST_TIMEOUT: "60"}
    heavy: true
    enabled: false
defaults:
  tasks: [demo-adp]
  language: verilog
  repeats: 2
  max_steps: 10
  reasoning_effort: default
  agent_backend: react
  flags: []
  enabled: true
entries:
  - id: model-a
    model: openrouter:example/model-a
  - id: model-a-high
    model: openrouter:example/model-a
    reasoning_effort: high
  - id: model-a-spire
    model: openrouter:example/model-a
    language: spirehdl
    flags: [--fsm-optimize]
    enabled: false
  - id: model-c
    model: openrouter:example/model-c-closed
    enabled: false
"""

MODELS = {
    "schema": 1,
    "models": {
        "openrouter:example/model-a": {"name": "Model A", "vendor": "Example Lab", "open_weights": True,
                                        "price_per_mtok": {"input": 1.0, "output": 4.0}},
        "openrouter:example/model-c-closed": {"name": "Model C", "vendor": "Example Corp", "open_weights": False,
                                               "price_per_mtok": {"input": 2.0, "output": 8.0}},
    },
}


def write_benchmark(root: Path, name: str, starting_point: str) -> None:
    d = root / name
    (d / "context").mkdir(parents=True)
    (d / "description.txt").write_text("Demo design.\n")
    (d / "metadata.json").write_text(json.dumps({"name": name, "module_name": "demo",
                                                 "starting_point": f"context/{starting_point}"}))
    (d / "tb.sv").write_text("module tb; endmodule\n")
    (d / "vectors.dat").write_text("0 0\n")
    (d / "context" / starting_point).write_text("// starting point\n")


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A miniature rtlscout-bench checkout in a temp dir, with the process chdir'ed into it."""
    root = tmp_path / "bench"
    root.mkdir()
    (root / "campaign_matrix.yaml").write_text(MATRIX)
    write_benchmark(root / "benchmarks", "demo_verilog", "starting_point.v")
    write_benchmark(root / "benchmarks", "demo_spire", "starting_point.py")
    (root / "site").mkdir()
    shutil.copyfile(REPO / "site" / "index.html", root / "site" / "index.html")
    data = root / "data"
    (data / "leaderboards").mkdir(parents=True)
    (data / "campaigns").mkdir()
    (data / "runs").mkdir()
    (data / "models.json").write_text(json.dumps(MODELS, indent=1))
    monkeypatch.chdir(root)
    return root


def make_run(parent: Path, run_id: str = "20260301_101500", *, benchmark: str = "demo_verilog",
             model: str = "example/model-a", costs=((True, 1000.0), (False, None), (True, 800.0), (True, 900.0)),
             elapsed: bool = False, tz_offset_h: float = 0.0, duration_s: float = 600.0, error: str = "",
             chat_extra: str = "", num_steps: int = 10, with_mtimes: bool = True,
             cost_metric: str = "area_delay_product", tokens=(200_000, 10_000)) -> Path:
    """Write a raw run directory the way rtlscout does.

    Without ``elapsed`` the evaluations carry no timing fields (like runs that predate them) and the ``eval_N``
    directories get modification times spread over the run instead; ``tz_offset_h`` shifts those as if the run
    had been stamped by a clock in another time zone.
    """
    d = parent / benchmark / model.replace("/", "_") / run_id
    d.mkdir(parents=True)
    start = datetime.strptime(run_id, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc).timestamp() + tz_offset_h * 3600
    evals, cum_in, cum_out = [], 0, 0
    n = len(costs)
    for i, (ok, cost) in enumerate(costs, 1):
        cum_in, cum_out = cum_in + tokens[0] // n, cum_out + tokens[1] // n
        ev = dict(passed=ok, cost_value=cost, cost_metric=cost_metric, eval_index=i, step_index=2 * i,
                  metrics=dict(area=cost / 20, delay=20.0, power=1.5, target_delay=500.0) if ok else {},
                  design_file=f"design_{i}.sv", target_delay=None, context_window_tokens=1000 * i)
        t = duration_s * i / (n + 1)
        if elapsed:
            ev["elapsed_s"] = t
            ev["cumulative_token_usage"] = dict(input_tokens=cum_in, output_tokens=cum_out,
                                                cache_creation_input_tokens=0, cache_read_input_tokens=0,
                                                total_input_tokens=cum_in)
        evals.append(ev)
        (d / f"eval_{i}" / "workspace").mkdir(parents=True)
        p = d / f"eval_{i}" / "result.json"
        p.write_text(json.dumps(ev))
        if with_mtimes:
            os.utime(p, (start + t, start + t))
    passing = [e for e in evals if e["passed"]]
    best = min(passing, key=lambda e: e["cost_value"]) if passing else None
    if best:
        (d / "best_design").mkdir()
        (d / "best_design" / best["design_file"]).write_text("module demo; endmodule\n")
        (d / "best_design" / "tb.sv").write_text("module tb; endmodule\n")
        (d / "best_design" / "_best_meta.json").write_text(json.dumps(dict(
            eval_index=best["eval_index"], step_index=best["step_index"], best_cost=best["cost_value"],
            cost_metric=cost_metric, design_file=best["design_file"])))
    (d / "summary.txt").write_text("What worked: narrower adders.\n")
    (d / "chat_log.txt").write_text(f"BENCHMARK : {benchmark}\nMODEL     : {model}\n[SYSTEM]\nYou are an RTL agent.\n"
                                    f"{chat_extra}\n")
    res = d / "result.json"
    res.write_text(json.dumps(dict(
        benchmark_name=benchmark, model=model, passed=best is not None, best_cost=best and best["cost_value"],
        cost_metric=cost_metric, best_metrics=best and best["metrics"], best_eval=best, all_evals=evals,
        num_steps=num_steps, duration_s=duration_s, error=error, workdir=str(d),
        token_usage=dict(input_tokens=tokens[0], output_tokens=tokens[1], cache_creation_input_tokens=0,
                         cache_read_input_tokens=0, total_input_tokens=tokens[0]))))
    if with_mtimes:
        os.utime(res, (start + duration_s, start + duration_s))
    return d


def adopt(run_dirs, entry="model-a", task="demo-adp", origin="manual", suite=None, extra=()):
    """Record raw run dirs through the command line, like the seed runs were."""
    from rtlscout_bench import record
    argv = [str(d) for d in run_dirs] + ["--task", task, "--entry", entry, "--origin", origin, "--rtlscout", "0.0.test"]
    if suite:
        argv += ["--suite", suite]
    return record.main(argv + list(extra))


def stub_env(monkeypatch) -> None:
    """Make child processes import the rtlscout stub, and pretend rtlscout 0.2.0 / spire-hdl 0.4.0 are installed."""
    from rtlscout_bench import campaign
    monkeypatch.setenv("PYTHONPATH", str(STUB) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    versions = {"rtlscout": "0.2.0", "spire-hdl": "0.4.0"}
    monkeypatch.setattr(campaign, "installed_version", versions.get)
    monkeypatch.setattr(campaign, "LAUNCH_STAGGER_S", 0.01)


def git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
    return subprocess.run(["git", "-c", "protocol.file.allow=always", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env).stdout.strip()


@pytest.fixture
def python():
    return sys.executable
