"""Benchmark descriptions cite context files by their path inside context/: rtlscout places the contents of a
benchmark's context/ directory at the agent workspace root (subfolders kept), so `context/starting_point.v` names a
path that does not exist there, and every agent lost one step to a failed read before 2026-10-12."""
import re

from conftest import REPO

CONTEXT_FILE = re.compile(r"context/[A-Za-z0-9_.-]+\.(?:py|sv|v)\b")


def test_descriptions_cite_context_files_by_bare_name():
    offenders = [str(f.relative_to(REPO)) for f in sorted((REPO / "benchmarks").glob("*/description.txt"))
                 if CONTEXT_FILE.search(f.read_text())]
    assert not offenders, "descriptions citing context/<file> (the workspace is flat):\n" + "\n".join(offenders)
