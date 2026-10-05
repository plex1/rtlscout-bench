"""publish end to end, against local bare repositories standing in for the two remotes. No network."""
import json
import re
import shutil
from pathlib import Path

import pytest

from rtlscout_bench import campaign, publish, record

from conftest import MODELS, git, stub_env


@pytest.fixture
def repos(bench, tmp_path, monkeypatch):
    """The bench fixture turned into a git checkout with ``data`` as a submodule, each with a bare 'origin'."""
    for var, val in (("GIT_AUTHOR_NAME", "Test"), ("GIT_AUTHOR_EMAIL", "test@example.invalid"),
                     ("GIT_COMMITTER_NAME", "Test"), ("GIT_COMMITTER_EMAIL", "test@example.invalid")):
        monkeypatch.setenv(var, val)
    data_remote, bench_remote, seed = tmp_path / "data.git", tmp_path / "bench.git", tmp_path / "data-seed"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(data_remote))
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bench_remote))
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    (seed / "models.json").write_text(json.dumps(MODELS, indent=1))
    (seed / "README.md").write_text("data\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "data skeleton")
    git(seed, "push", "-q", str(data_remote), "main")

    shutil.rmtree(bench / "data")
    git(bench, "init", "-q", "-b", "main")
    (bench / ".gitignore").write_text("runs/\n_site/\n")
    git(bench, "submodule", "add", "-q", str(data_remote), "data")
    git(bench, "add", "-A")
    git(bench, "commit", "-q", "-m", "bench skeleton")
    git(bench, "remote", "add", "origin", str(bench_remote))
    git(bench, "push", "-q", "origin", "main")
    stub_env(monkeypatch)
    return dict(bench=bench, data=bench / "data", data_remote=data_remote, bench_remote=bench_remote)


def _pointer(repo: Path, rev: str = "HEAD") -> str:
    return git(repo, "ls-tree", rev, "data").split()[2]


def _run_campaign(capsys, name="c1"):
    assert campaign.main(["--name", name]) == 0
    capsys.readouterr()


def test_dry_run_shows_the_change_and_touches_nothing(repos, capsys):
    _run_campaign(capsys)
    head = git(repos["data"], "rev-parse", "HEAD")
    assert publish.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1. recording new runs from runs/ (dry run: into a temporary copy of the data)" in out
    assert "recorded 4, already recorded 0" in out and "2. data check: 4 records (4 selected, 0 excluded): OK" in out
    assert "3. dry run: data/ not committed (4 new run record(s) would be)" in out
    assert "4. leaderboard as published (data @ " in out and "(no runs selected)" in out.split("leaderboard after")[0]
    assert "demo-adp: + row added    Model A · high effort [model-a-high]: n=2, mean 0.720 ± 0.000 (suite-t1)" in out
    assert "demo-adp: ~ starting point (verilog): none -> 1,000.00" in out
    assert "dry run: nothing was written to data/, committed or pushed" in out
    assert git(repos["data"], "status", "--porcelain") == "" and git(repos["data"], "rev-parse", "HEAD") == head
    assert not list((repos["data"]).glob("runs/*/*/*/record.json"))
    assert git(repos["bench"], "status", "--porcelain") == ""


def test_publish_commits_and_pushes_data_then_the_pointer(repos, capsys):
    _run_campaign(capsys)
    old_pointer = _pointer(repos["bench"])
    assert publish.main(["--yes"]) == 0
    out = capsys.readouterr().out
    data_head = git(repos["data"], "rev-parse", "HEAD")
    assert f"3. committed data @ {data_head[:7]}: demo-adp: model-a +2, model-a-high +2" in out
    assert "pushed data to origin main" in out
    assert f"6. rtlscout-bench: committed 'publish: data @ {data_head[:7]}'" in out
    # both remotes received the commits, and the published pointer is the new data commit
    assert git(repos["data_remote"], "rev-parse", "main") == data_head != old_pointer
    assert git(repos["bench_remote"], "rev-parse", "main") == git(repos["bench"], "rev-parse", "HEAD")
    assert _pointer(repos["bench_remote"], "main") == data_head
    assert git(repos["bench"], "log", "-1", "--format=%s") == f"publish: data @ {data_head[:7]}"
    assert git(repos["bench"], "show", "--stat", "--format=", "HEAD").split()[0] == "data"       # nothing else in it
    tracked = git(repos["data"], "ls-files").splitlines()
    assert "campaigns/c1.json" in tracked and "leaderboards/demo-adp.json" in tracked
    assert sum(1 for f in tracked if f.endswith("record.json")) == 4

    # a second publish finds nothing new
    assert publish.main(["--yes"]) == 0
    out = capsys.readouterr().out
    assert "recorded 0, already recorded 4" in out
    assert "nothing to publish: the data pointer is already at this commit" in out
    assert "(none: the page would look the same)" in out


def test_declining_keeps_the_data_commit_but_not_the_page(repos, capsys, monkeypatch):
    _run_campaign(capsys)
    old_pointer = _pointer(repos["bench"])
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert publish.main([]) == 0
    out = capsys.readouterr().out
    data_head = git(repos["data"], "rev-parse", "HEAD")
    assert "not published. The data commit stays; the page is unchanged." in out
    assert git(repos["data_remote"], "rev-parse", "main") == data_head
    assert _pointer(repos["bench"]) == old_pointer == _pointer(repos["bench_remote"], "main")

    # leave one run out, then publish for real
    run_id = sorted(p.name for p in (repos["data"] / "runs/demo-adp/model-a").iterdir())[0]
    record.main(["--exclude", f"demo-adp/model-a/{run_id}", "--reason", "provider outage"])
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert publish.main(["-m", "exclude one run"]) == 0
    out = capsys.readouterr().out
    assert f"demo-adp: ! run excluded model-a/{run_id}: provider outage" in out
    after = out.split("leaderboard after this publish")[1]
    assert re.search(r"^2  Model A\s+1\s+0\.800\s", after, re.M) and f"model-a/{run_id}: provider outage" in after
    assert git(repos["data"], "log", "-1", "--format=%s") == "exclude one run"
    assert _pointer(repos["bench_remote"], "main") == git(repos["data"], "rev-parse", "HEAD")


def test_no_push_commits_locally_only(repos, capsys):
    _run_campaign(capsys)
    remote_data = git(repos["data_remote"], "rev-parse", "main")
    remote_bench = git(repos["bench_remote"], "rev-parse", "main")
    assert publish.main(["--yes", "--no-push"]) == 0
    out = capsys.readouterr().out
    assert "--no-push: data repository not pushed" in out and "--no-push: not pushed" in out
    assert git(repos["data_remote"], "rev-parse", "main") == remote_data
    assert git(repos["bench_remote"], "rev-parse", "main") == remote_bench
    assert _pointer(repos["bench"]) == git(repos["data"], "rev-parse", "HEAD") != remote_data


def test_failed_data_check_commits_nothing(repos, capsys):
    _run_campaign(capsys)
    record.main([])
    one = next((repos["data"] / "runs").glob("*/*/*/chat_log.txt"))
    one.write_text("leaked sk-or-" + "v1-000\n")
    head = git(repos["data"], "rev-parse", "HEAD")
    assert publish.main(["--yes"]) == 1
    out = capsys.readouterr().out
    assert "API-key pattern sk-or- at line 1" in out and "nothing was committed" in out
    assert git(repos["data"], "rev-parse", "HEAD") == head


def test_publishing_from_another_branch_is_refused(repos, capsys):
    _run_campaign(capsys)
    git(repos["bench"], "checkout", "-q", "-b", "experiment")
    assert publish.main(["--yes"]) == 1
    assert "the page deploys from main" in capsys.readouterr().err
    assert git(repos["bench_remote"], "rev-parse", "main") == git(repos["bench"], "rev-parse", "main")


def test_data_must_be_a_git_checkout(bench, capsys):
    assert publish.main(["--dry-run"]) == 1
    assert "is not a git checkout of the data repository" in capsys.readouterr().err
