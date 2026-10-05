"""Build the static leaderboard page: ``data/`` -> ``_site/``.

    python -m rtlscout_bench.site                  # writes _site/index.html and _site/data.js
    python -m rtlscout_bench.site --out /tmp/preview

The output is one HTML page (a copy of ``site/index.html``, which contains no results) and one generated data
file loaded with a plain ``<script src>``, so the folder also opens from ``file://``. The build reads only the data
repository: ``models.json``, ``leaderboards/*.json`` and the records those select. It never looks at raw run
directories.

The build fails, and writes nothing, when the data check fails for a selected run: a broken record, a missing
transcript or best design, an API-key pattern, or (with ``policy.open_weights_only``) a model that is not marked
open-weights. It also fails when one row would mix runs measured under different conditions.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config import Matrix, MatrixError, load_matrix
from .record import check_data, git_commit, load_models
from .tables import BoardError, load_board, render_text

_GITHUB_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?$")


class SiteError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def data_repo_url(data: Path) -> str | None:
    """``https://github.com/<owner>/<repo>`` of the data checkout's origin, or None (never a local path)."""
    try:
        url = subprocess.run(["git", "-C", str(data), "remote", "get-url", "origin"], capture_output=True, text=True,
                             check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    m = _GITHUB_RE.match(url)
    return f"https://github.com/{m.group(1)}" if m else None


def _run_json(task_id: str, entry: str, rec: dict) -> dict:
    best = rec.get("best") or {}
    traj = []
    for e in rec["evals"]:
        point = dict(e=e["e"], s=e["s"], c=e["c"], ok=e["ok"], t=e["t_min"])
        if e.get("tok_in") is not None:
            point.update(ti=e["tok_in"], to=e["tok_out"])
        traj.append(point)
    return dict(id=rec["run"], path=f"runs/{task_id}/{entry}/{rec['run']}", started=rec["started"], suite=rec["suite"],
                origin=rec["origin"], rtlscout=rec["rtlscout"], best_cost=best.get("cost"), best_eval=best.get("eval"),
                best_step=best.get("step"), best_file=best.get("file"), area=best.get("area"), delay=best.get("delay"),
                n_evals=rec["n_evals"], n_failed=rec["n_failed"], steps=rec["steps_used"],
                runtime_min=rec["runtime_min"], usd=rec["usd"], tokens=rec["tokens"], traj=traj)


def build_data(matrix: Matrix, data: Path) -> tuple[dict, list[dict]]:
    """The site's data object and the resolved boards (for printing). Raises :class:`SiteError`."""
    errors, _warnings, _counts = check_data(data, matrix, selected_only=True)
    if errors:
        raise SiteError(errors)
    models = load_models(data)
    tasks, boards = [], []
    for task in matrix.enabled_tasks():
        try:
            board = load_board(data, task.id, matrix.suite, models)
        except BoardError as exc:
            raise SiteError([str(exc)]) from exc
        boards.append(board)
        rows = []
        for row in board["rows"]:
            rows.append(dict(entry=row["entry"], label=row["label"], name=row["name"], model=row["model"],
                             vendor=row["vendor"], open=row["open_weights"] is True, language=row["language"],
                             reasoning_effort=row["reasoning_effort"], flags=row["flags"], max_steps=row["max_steps"],
                             suite=row["suite"], seed=row["seed"], price=row["price"], note=row["note"],
                             agg=row["agg"], runs=[_run_json(task.id, row["entry"], r) for r in row["runs"]]))
        tasks.append(dict(id=task.id, title=board["title"], description=board["description"],
                          metric=task.cost_metric, metric_label=board["metric_label"], unit=board["unit"],
                          scale_exp=board["scale_exp"], flow=board["flow"], baselines=board["baselines"], rows=rows,
                          excluded=board["excluded"], footnotes=board["footnotes"]))
    site = dict(generated=datetime.now().isoformat(timespec="minutes"), suite=matrix.suite,
                open_weights_only=matrix.open_weights_only, bench_commit=git_commit(matrix.path.parent),
                data_commit=git_commit(data), data_url=data_repo_url(data), tasks=tasks)
    return site, boards


def write_site(site: dict, template: Path, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    page, data_js = out / "index.html", out / "data.js"
    shutil.copyfile(template, page)
    data_js.write_text("window.RTLSCOUT_DATA = " + json.dumps(site, separators=(",", ":"), ensure_ascii=False) + ";\n")
    return page, data_js


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rtlscout_bench.site", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", help="matrix file (default: ./campaign_matrix.yaml)")
    ap.add_argument("--data", default="data", help="data repository checkout (default: data)")
    ap.add_argument("--out", default="_site", help="output directory (default: _site)")
    ap.add_argument("--template", default="site/index.html", help="page template (default: site/index.html)")
    ap.add_argument("-q", "--quiet", action="store_true", help="do not print the leaderboard tables")
    args = ap.parse_args(argv)
    try:
        matrix = load_matrix(args.matrix)
    except MatrixError as exc:
        print(f"{exc.path}: invalid matrix\n" + "\n".join(f"  - {p}" for p in exc.problems), file=sys.stderr)
        return 2
    data, template = Path(args.data), Path(args.template)
    if not template.is_file():
        print(f"error: page template {template} not found (run from the rtlscout-bench checkout)", file=sys.stderr)
        return 1
    if not (data / "models.json").is_file():
        print(f"error: {data / 'models.json'} not found. Is the data submodule checked out? "
              f"(git submodule update --init)", file=sys.stderr)
        return 1
    try:
        site, boards = build_data(matrix, data)
    except SiteError as exc:
        print("site build failed; nothing was written:", file=sys.stderr)
        for p in exc.problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    page, data_js = write_site(site, template, Path(args.out))
    if not args.quiet:
        for board in boards:
            print(render_text(board))
            print()
    n_rows = sum(len(t["rows"]) for t in site["tasks"])
    n_runs = sum(len(r["runs"]) for t in site["tasks"] for r in t["rows"])
    print(f"wrote {page} and {data_js} ({data_js.stat().st_size / 1024:.0f} KB): {len(site['tasks'])} task(s), "
          f"{n_rows} row(s), {n_runs} run(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
