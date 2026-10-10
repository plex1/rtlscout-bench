"""Record new runs, commit the data repository, show what changes on the leaderboard, then bump the data pointer.

    python -m rtlscout_bench.publish              # the whole update; asks before the page changes
    python -m rtlscout_bench.publish --dry-run    # show the table before and after; change nothing
    python -m rtlscout_bench.publish --no-push    # commit locally in both repositories, push nothing

Steps:
  1. record every finished run of the campaigns under runs/ into data/  (as ``python -m rtlscout_bench.record``)
  2. validate data/                                                      (as ``record --check``)
  3. commit data/ and push it to the data repository
  4. print the leaderboard as published and as it would become, and the differences
  5. ask for confirmation
  6. commit the new ``data`` submodule pointer in rtlscout-bench and push; the Pages workflow redeploys the site

"As published" is the data commit that the last commit of rtlscout-bench points at, which is exactly what the
deployed page was built from. Step 3 alone never changes the page; step 6 is the publish action. Answering "no"
at step 5 leaves the data committed and the page as it was.

``--dry-run`` works on a temporary copy of data/: nothing is written to data/, committed or pushed.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import Matrix, MatrixError, load_matrix
from .record import _campaign_files, check_data, load_models, record_campaign
from .tables import BoardError, diff_boards, load_board, render_text

_NEW_RECORD_RE = re.compile(r"^runs/([^/]+)/([^/]+)/[^/]+/record\.json$")


class GitError(Exception):
    pass


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {repo}:\n{(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def identity_args(submodule: Path) -> list[str]:
    """`-c user.name=… -c user.email=…` of the superproject's configuration, so a commit made in the submodule
    carries the same identity as the commits around it (a submodule checkout has its own config)."""
    top = git(submodule, "rev-parse", "--show-superproject-working-tree", check=False)
    out: list[str] = []
    for key in ("user.name", "user.email"):
        value = git(Path(top), "config", key, check=False) if top else ""
        if value:
            out += ["-c", f"{key}={value}"]
    return out


def is_git_checkout(path: Path) -> bool:
    """True when *path* is the top level of a git working tree (a submodule checkout counts)."""
    try:
        top = git(path, "rev-parse", "--show-toplevel")
    except (GitError, OSError):
        return False
    return Path(top).resolve() == path.resolve()


def pointer_commit(bench: Path, sub: str) -> str | None:
    """The commit of submodule *sub* that the bench repository's HEAD records, or None."""
    try:
        out = git(bench, "ls-tree", "HEAD", sub)
    except GitError:
        return None
    parts = out.split()
    return parts[2] if len(parts) >= 3 and parts[1] == "commit" else None


def export_commit(repo: Path, commit: str, dest: Path) -> None:
    """Unpack the tree of *commit* into *dest* (no .git)."""
    arch = subprocess.Popen(["git", "-C", str(repo), "archive", commit], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    tar = subprocess.run(["tar", "-x", "-C", str(dest)], stdin=arch.stdout, capture_output=True)
    arch.stdout.close()
    err = arch.stderr.read().decode(errors="replace")
    if arch.wait() != 0 or tar.returncode != 0:
        detail = err.strip() or tar.stderr.decode(errors="replace")
        raise GitError(f"cannot read commit {commit[:12]} of {repo}: {detail}")


def boards_of(data: Path, matrix: Matrix) -> dict[str, dict]:
    models = load_models(data)
    return {t.id: load_board(data, t.id, matrix.suite, models) for t in matrix.enabled_tasks()}


def new_records(data: Path) -> dict[tuple[str, str], int]:
    """Uncommitted new run records in the data checkout, counted per (task, entry)."""
    counts: dict[tuple[str, str], int] = {}
    for line in git(data, "status", "--porcelain", "--untracked-files=all").splitlines():
        if line[:2] in ("??", "A ", "AM"):
            m = _NEW_RECORD_RE.match(line[3:].strip().strip('"'))
            if m:
                counts[(m.group(1), m.group(2))] = counts.get((m.group(1), m.group(2)), 0) + 1
    return counts


def default_message(counts: dict[tuple[str, str], int]) -> str:
    if not counts:
        return "update leaderboard curation"
    by_task: dict[str, list[str]] = {}
    for (task, entry), n in sorted(counts.items()):
        by_task.setdefault(task, []).append(f"{entry} +{n}")
    return "; ".join(f"{task}: {', '.join(parts)}" for task, parts in by_task.items())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rtlscout_bench.publish", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", help="matrix file (default: ./campaign_matrix.yaml)")
    ap.add_argument("--data", default="data", help="data repository checkout (default: the data submodule)")
    ap.add_argument("--runs-dir", default="runs", help="where the campaigns wrote their raw output (default: runs)")
    ap.add_argument("--dry-run", action="store_true", help="show what would change; write, commit and push nothing")
    ap.add_argument("--no-push", action="store_true", help="commit in both repositories but push neither")
    ap.add_argument("-y", "--yes", action="store_true", help="do not ask before bumping the data pointer")
    ap.add_argument("-m", "--message", help="commit message for the data repository (default: derived from the "
                                            "new runs)")
    args = ap.parse_args(argv)
    try:
        matrix = load_matrix(args.matrix)
    except MatrixError as exc:
        print(f"{exc.path}: invalid matrix\n" + "\n".join(f"  - {p}" for p in exc.problems), file=sys.stderr)
        return 2
    bench, data, runs_dir = matrix.path.resolve().parent, Path(args.data), Path(args.runs_dir)
    if not is_git_checkout(data):
        print(f"error: {data} is not a git checkout of the data repository (git submodule update --init)",
              file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="rtlscout_bench_publish_"))
    try:
        return _publish(args, matrix, bench, data, runs_dir, tmp)
    except (GitError, BoardError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _publish(args, matrix: Matrix, bench: Path, data: Path, runs_dir: Path, tmp: Path) -> int:
    sub = str(data.resolve().relative_to(bench)) if data.resolve().is_relative_to(bench) else None
    if sub and not git(bench, "ls-files", "-s", "--", sub, check=False).startswith("160000"):
        sub = None                                   # inside the checkout but not a submodule of it
    published = pointer_commit(bench, sub) if sub else None

    # the leaderboard as published
    before: dict[str, dict] = {}
    if published:
        (tmp / "published").mkdir()
        export_commit(data, published, tmp / "published")
        before = boards_of(tmp / "published", matrix)

    # 1. record (a dry run records into a copy)
    work = data
    if args.dry_run:
        work = tmp / "data"
        shutil.copytree(data, work, ignore=shutil.ignore_patterns(".git"))
    models = load_models(work)
    files = _campaign_files(runs_dir) if runs_dir.is_dir() else []
    print(f"1. recording new runs from {runs_dir}/"
          + (" (dry run: into a temporary copy of the data)" if args.dry_run else ""))
    total = dict(recorded=0, already=0, skipped=0, refused=0)
    for f in files:
        print(f"campaign {f}")
        counts = record_campaign(f, work, matrix, models)
        for k in total:
            total[k] += counts[k]
    print(f"   recorded {total['recorded']}, already recorded {total['already']}, skipped {total['skipped']}, "
          f"refused {total['refused']}" + ("" if files else " (no campaign files)"))

    # 2. validate
    errors, warnings, n = check_data(work, matrix)
    print(f"2. data check: {n['records']} records ({n['selected']} selected, {n['excluded']} excluded): "
          + ("OK" if not errors else f"{len(errors)} error(s)"))
    for w in warnings:
        print(f"   warning: {w}")
    if errors:
        for e in errors:
            print(f"   ERROR: {e}")
        print("nothing was committed. Fix the data (or exclude the run) and run publish again.")
        return 1
    after = boards_of(work, matrix)

    # 3. commit + push the data repository
    if args.dry_run:
        pending = new_records(data)
        n_pending = sum(pending.values()) + total["recorded"]
        print(f"3. dry run: data/ not committed ({n_pending} new run record(s) would be)")
    else:
        if git(data, "status", "--porcelain"):
            msg = args.message or default_message(new_records(data))
            git(data, "add", "-A")
            git(data, *identity_args(data), "commit", "-m", msg)
            print(f"3. committed data @ {git(data, 'rev-parse', '--short', 'HEAD')}: {msg}")
        else:
            print(f"3. data/ has nothing new to commit (HEAD {git(data, 'rev-parse', '--short', 'HEAD')})")
        if args.no_push:
            print("   --no-push: data repository not pushed")
        else:
            git(data, "push", "origin", "HEAD:refs/heads/main")
            print("   pushed data to origin main")

    # 4. the table, before and after
    print("\n4. leaderboard as published" + (f" (data @ {published[:7]})" if published else " (nothing published yet)"))
    for task_id in after:
        print(render_text(before[task_id]) if task_id in before else f"{task_id}: (not published)")
    print("\n   leaderboard after this publish")
    changes = []
    for task_id, board in after.items():
        print(render_text(board))
        changes += [f"{task_id}: {line}" for line in diff_boards(before.get(task_id), board)]
    print("\n   changes")
    for line in changes or ["(none: the page would look the same)"]:
        print(f"   {line}")

    if args.dry_run:
        print("\ndry run: nothing was written to data/, committed or pushed")
        return 0
    if not sub:
        print(f"\n{data} is not a submodule of {bench}: data committed, no pointer to bump")
        return 0
    head = git(data, "rev-parse", "HEAD")
    if head == published:
        print("\nnothing to publish: the data pointer is already at this commit")
        return 0

    # 5. the gate
    if not args.yes:
        try:
            answer = input(f"\n5. Publish? This points rtlscout-bench at data @ {head[:7]}"
                           f"{'' if args.no_push else ' and pushes; the page redeploys'} [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("not published. The data commit stays; the page is unchanged. To leave a run out:\n"
                  "  python -m rtlscout_bench.record --exclude <task>/<entry>/<run-id> --reason \"...\"\n"
                  "then run publish again.")
            return 0

    # 6. the pointer bump
    branch = git(bench, "rev-parse", "--abbrev-ref", "HEAD")
    if not args.no_push and branch != "main":
        print(f"error: rtlscout-bench is on branch '{branch}'; the page deploys from main. Switch to main (or use "
              f"--no-push) and run publish again.", file=sys.stderr)
        return 1
    git(bench, "add", sub)
    git(bench, "commit", "-m", f"publish: data @ {head[:7]}", "--", sub)
    print(f"6. rtlscout-bench: committed 'publish: data @ {head[:7]}'")
    if args.no_push:
        print("   --no-push: not pushed. Push both repositories (data first) to deploy.")
    else:
        git(bench, "push", "origin", "HEAD:refs/heads/main")
        print("   pushed to origin main; the Pages workflow rebuilds and deploys the site")
    return 0


if __name__ == "__main__":
    sys.exit(main())
