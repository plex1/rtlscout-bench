"""Run the commands of docs/manual.md that are marked as checkable, so stale instructions fail the build.

A fenced ``bash`` block whose first line is ``# doctest`` is executed in a scratch copy of this checkout and
must exit 0. ``# doctest: rtlscout`` marks blocks that need the rtlscout package and the EDA tools (the scripted
``fake:`` model, no API key); those are skipped where rtlscout is not installed. A block may be followed by
``<!-- doctest-expect: text -->`` comments: each text must occur in the block's output.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO, STUB

MANUAL = REPO / "docs" / "manual.md"
_BLOCK_RE = re.compile(r"^```bash\n(# doctest[^\n]*)\n(.*?)^```\n((?:<!-- doctest-expect: .*? -->\n)*)", re.S | re.M)
_EXPECT_RE = re.compile(r"<!-- doctest-expect: (.*?) -->")


def manual_blocks() -> list[tuple[str, str, list[str]]]:
    if not MANUAL.is_file():
        return []
    return [(m.group(1).strip(), m.group(2), _EXPECT_RE.findall(m.group(3)))
            for m in _BLOCK_RE.finditer(MANUAL.read_text())]


BLOCKS = manual_blocks()
HAVE_RTLSCOUT = (importlib.util.find_spec("rtlscout") is not None and shutil.which("yosys") is not None
                 and shutil.which("verilator") is not None)


@pytest.fixture(scope="module")
def checkout(tmp_path_factory):
    """A scratch copy of the checkout (no .git, no scratch output) with ``python`` on PATH = this interpreter."""
    if not (REPO / "data" / "models.json").is_file():
        pytest.skip("the data submodule is not checked out (git submodule update --init)")
    root = tmp_path_factory.mktemp("manual") / "rtlscout-bench"
    junk = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.egg-info", ".venv*")

    def ignore(directory, names):       # the scratch dirs runs/ and _site/ only at the top level: data/runs/ is data
        top = {"runs", "_site"} & set(names) if Path(directory) == REPO else set()
        return set(junk(directory, names)) | top

    shutil.copytree(REPO, root, ignore=ignore)
    bindir = root.parent / "bin"
    bindir.mkdir()
    wrapper = bindir / "python"         # a wrapper, not a symlink: a symlinked venv interpreter loses its venv
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    wrapper.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    env.pop("RTLSCOUT_REASONING_EFFORT", None)
    return root, env


def test_manual_has_checkable_blocks():
    assert MANUAL.is_file(), "docs/manual.md is missing"
    marks = [mark for mark, _, _ in BLOCKS]
    assert sum(1 for m in marks if m == "# doctest") >= 5 and any(m == "# doctest: rtlscout" for m in marks)
    assert set(marks) <= {"# doctest", "# doctest: rtlscout"}, f"unknown doctest marker in {set(marks)}"


def test_manual_opens_with_the_two_command_update():
    text = MANUAL.read_text()
    first_block = re.search(r"```bash\n(.*?)```", text, re.S).group(1)
    lines = [line.split("#")[0].strip() for line in first_block.strip().splitlines()]
    assert lines == ["python -m rtlscout_bench.campaign", "python -m rtlscout_bench.publish"]


@pytest.mark.parametrize("mark, script, expects", BLOCKS, ids=[f"block{i}" for i in range(len(BLOCKS))])
def test_manual_block(checkout, mark, script, expects):
    if mark == "# doctest: rtlscout" and not HAVE_RTLSCOUT:
        pytest.skip("needs the rtlscout package and the EDA tools (run inside the rtlscout container)")
    root, env = checkout
    proc = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=root, env=env, capture_output=True, text=True,
                          timeout=900)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"manual block failed:\n{script}\n--- output ---\n{out}"
    for text in expects:
        assert text in out, f"manual block no longer prints {text!r}:\n{script}\n--- output ---\n{out}"


def test_smoke_matrix_runs_the_whole_pipeline(tmp_path, monkeypatch):
    """The smoke matrix of the manual, with the rtlscout stub instead of the real package: campaign, record, site."""
    from rtlscout_bench import campaign, record, site
    from conftest import stub_env
    monkeypatch.chdir(REPO)
    stub_env(monkeypatch)
    data, runs, out = tmp_path / "data", tmp_path / "runs", tmp_path / "_site"
    shutil.copytree(REPO / "tests" / "smoke" / "data", data)
    common = ["--matrix", "tests/smoke/campaign_matrix.yaml", "--data", str(data)]
    assert campaign.main(common + ["--runs-dir", str(runs), "--name", "smoke"]) == 0
    assert record.main(common + [str(runs)]) == 0
    assert record.main(common + ["--check"]) == 0
    assert site.main(common + ["--out", str(out), "-q"]) == 0
    assert (out / "index.html").is_file() and '"entry":"fake-adder"' in (out / "data.js").read_text()
    assert STUB.is_dir() and not (REPO / "runs" / "campaigns" / "smoke.json").exists()
