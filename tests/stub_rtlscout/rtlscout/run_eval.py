"""Stub of ``python -m rtlscout.run_eval <file> --benchmark <dir> ... --json``: header lines, then one JSON object."""
import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--benchmark")
    ap.add_argument("--cost-metric", default="transistors")
    ap.add_argument("--target-delay", type=float, default=500.0)
    ap.add_argument("--technology", default="asap7")
    ap.add_argument("--energy-exp", type=float, default=1.0)
    ap.add_argument("--skip-cec", action="store_true")
    ap.add_argument("--skip-netlist-sim", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    assert Path(args.file).is_file(), args.file
    print(f"Workdir:  /tmp/run_eval_stub (sandbox)\nDesign:   {Path(args.file).name}\nMetric:   {args.cost_metric}\n")
    result = dict(passed=True, cost_value=1000.0, cost_metric=args.cost_metric, duration_s=0.1,
                  metrics=dict(area=100.0, delay=10.0, target_delay=args.target_delay))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
