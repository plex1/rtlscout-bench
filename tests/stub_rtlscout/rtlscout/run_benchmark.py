"""Stub of ``python -m rtlscout.run_benchmark``: writes a plausible run directory in milliseconds.

Behaviour is steered by the model name (the part after ``fake:``/``provider:``):
    .../crash       exit 3 before a run directory exists
    .../cut-short   create the run directory, then exit 1 without result.json
    .../slow        create the run directory, then sleep for a minute (to be interrupted)
    anything else   a complete run with three evaluations (pass, fail, pass)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--benchmarks-root", nargs="+")
    ap.add_argument("--runs-dir", default="runs")
    ap.add_argument("--max-steps", type=int, default=20)
    ap.add_argument("--cost-metric", default="transistors")
    ap.add_argument("--target-delay", type=float, default=500.0)
    ap.add_argument("--technology", default="asap7")
    ap.add_argument("--energy-exp", type=float, default=1.0)
    ap.add_argument("--language", default="verilog")
    ap.add_argument("--agent-backend", default="react")
    for flag in ("--skip-cec", "--skip-netlist-sim", "--fsm-optimize", "--arith-autoconfig"):
        ap.add_argument(flag, action="store_true")
    args = ap.parse_args()

    model = args.model.split(":", 1)[1]
    if not any((Path(r) / args.benchmark / "tb.sv").is_file() for r in args.benchmarks_root or ["benchmarks"]):
        print(f"Benchmark not found: {args.benchmark}")
        sys.exit(1)
    if model.endswith("crash"):
        print("stub: simulated crash before the run directory exists", file=sys.stderr)
        sys.exit(3)

    stamp = datetime.now()
    while True:                     # the real tool stamps to the second; the stub never reuses a directory
        workdir = Path(args.runs_dir) / args.benchmark / model.replace("/", "_") / stamp.strftime("%Y%m%d_%H%M%S")
        try:
            workdir.mkdir(parents=True)
            break
        except FileExistsError:
            stamp += timedelta(seconds=1)
    print(f"Workdir: {workdir}")
    if model.endswith("slow"):
        sys.stdout.flush()
        time.sleep(60)
    if model.endswith("cut-short"):
        print("stub: simulated kill in the middle of the run", file=sys.stderr)
        sys.exit(1)

    effort = os.environ.get("RTLSCOUT_REASONING_EFFORT", "default")
    base = 1000.0 if effort == "default" else 900.0
    costs = [(True, base), (False, None), (True, base * 0.8)]
    evals, usage = [], dict(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                            total_input_tokens=0)
    for i, (ok, cost) in enumerate(costs, 1):
        usage = dict(usage, input_tokens=usage["input_tokens"] + 1000 * i, output_tokens=usage["output_tokens"] + 100,
                     total_input_tokens=usage["total_input_tokens"] + 1000 * i)
        metrics = dict(area=cost / 10, delay=10.0, power=0.5, target_delay=args.target_delay) if ok else {}
        evals.append(dict(passed=ok, cost_value=cost, cost_metric=args.cost_metric, metrics=metrics, eval_index=i,
                          step_index=2 * i, design_file="design.sv", target_delay=None, context_window_tokens=500 * i,
                          elapsed_s=30.0 * i, cumulative_token_usage=dict(usage)))
    best = evals[-1]
    (workdir / "best_design").mkdir()
    (workdir / "best_design" / "design.sv").write_text("module demo(input a, output y); assign y = a; endmodule\n")
    (workdir / "best_design" / "_best_meta.json").write_text(json.dumps(dict(
        eval_index=best["eval_index"], step_index=best["step_index"], best_cost=best["cost_value"],
        cost_metric=args.cost_metric, design_file="design.sv")))
    (workdir / "summary.txt").write_text(f"Stub summary (reasoning effort {effort}).\n")
    (workdir / "chat_log.txt").write_text(
        f"BENCHMARK : {args.benchmark}\nMODEL     : {model}\n[SYSTEM]\nstub transcript\n")
    time.sleep(0.01)
    (workdir / "result.json").write_text(json.dumps(dict(
        benchmark_name=args.benchmark, model=model, passed=True, best_cost=best["cost_value"],
        cost_metric=args.cost_metric, best_metrics=best["metrics"], best_eval=best, all_evals=evals,
        num_steps=min(args.max_steps, 6), token_usage=usage, duration_s=95.0, error="", workdir=str(workdir))))
    print(f"\nBest: PASS | {best['cost_value']} {args.cost_metric} (step {best['eval_index']})")
    print(f"Results stored in: {workdir}")


if __name__ == "__main__":
    main()
