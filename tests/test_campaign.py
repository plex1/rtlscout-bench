import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rtlscout_bench import campaign, record
from rtlscout_bench.config import load_matrix
from rtlscout_bench.record import load_leaderboard

from conftest import MATRIX, STUB, adopt, make_run, stub_env

DATA, RUNS = Path("data"), Path("runs")


def _campaign_file(name="c1") -> dict:
    return json.loads((RUNS / "campaigns" / f"{name}.json").read_text())


# ----------------------------------------------------------------------------------------------- commands

def test_run_command_uses_module_form_and_matrix_fields(bench):
    m = load_matrix()
    cmd = campaign.run_cmd(m, m.tasks["demo-adp"], m.entry("model-a-spire"), RUNS)
    assert cmd == ["python", "-m", "rtlscout.run_benchmark", "--benchmark", "demo_spire", "--language", "spirehdl",
                   "--model", "openrouter:example/model-a", "--max-steps", "10", "--benchmarks-root", "benchmarks",
                   "--runs-dir", "runs/demo-adp/model-a-spire", "--cost-metric", "area_delay_product",
                   "--skip-cec", "--fsm-optimize"]


def test_baseline_command_takes_scoring_flags_only(bench):
    Path("campaign_matrix.yaml").write_text(MATRIX.replace(
        "    flags: [--skip-cec]\n",
        "    flags: [--skip-cec, --skip-netlist-sim]\n    target_delay: 500\n    technology: nangate45\n"))
    m = load_matrix()
    assert campaign.baseline_cmd(m, m.tasks["demo-adp"], "spirehdl") == [
        "python", "-m", "rtlscout.run_eval", "benchmarks/demo_spire/context/starting_point.py", "--benchmark",
        "benchmarks/demo_spire", "--cost-metric", "area_delay_product", "--target-delay", "500", "--technology",
        "nangate45", "--skip-cec", "--skip-netlist-sim", "--json"]


def test_environment_sets_effort_and_never_inherits_it(bench, monkeypatch):
    m = load_matrix()
    monkeypatch.setenv("RTLSCOUT_REASONING_EFFORT", "low")
    assert "RTLSCOUT_REASONING_EFFORT" not in campaign.run_env(m.tasks["demo-adp"], m.entry("model-a"))
    assert campaign.run_env(m.tasks["demo-adp"], m.entry("model-a-high"))["RTLSCOUT_REASONING_EFFORT"] == "high"
    assert campaign.run_env(m.tasks["demo-heavy"], None)["RTLSCOUT_ADP_FAST_TIMEOUT"] == "60"


def test_parse_eval_json_skips_header_lines():
    text = "Workdir:  /tmp/x (sandbox)\nMetric:   area\n\n{\n  \"cost_value\": 12.5,\n  \"metrics\": {\"area\": 1}\n}\n"
    assert campaign.parse_eval_json(text) == {"cost_value": 12.5, "metrics": {"area": 1}}
    assert campaign.parse_eval_json("Traceback ...\nno json here") == {}


# --------------------------------------------------------------------------------------------------- plan

def _plan(**kw):
    jobs, lines = campaign.plan(load_matrix(), DATA, RUNS, **kw)
    return [(j["task"].id, j["entry"].id, j["repeat"]) for j in jobs], lines


def test_plan_tops_up_enabled_entries_and_interleaves_them(bench):
    jobs, lines = _plan()
    assert jobs == [("demo-adp", "model-a", 0), ("demo-adp", "model-a-high", 0),
                    ("demo-adp", "model-a", 1), ("demo-adp", "model-a-high", 1)]
    assert "0 of 2 runs done, 2 to launch" in lines[0]


def test_recorded_runs_count_but_seed_excluded_and_mismatched_ones_do_not(bench, tmp_path):
    adopt([make_run(tmp_path / "seed", "20260101_000000")], origin="seed", suite="pre-t1")       # seed: not counted
    assert len(_plan()[0]) == 4
    adopt([make_run(tmp_path / "a", "20260301_101500"), make_run(tmp_path / "a", "20260301_101502")])
    assert [j for j in _plan()[0] if j[1] == "model-a"] == []                                    # 2 of 2 done
    record.main(["--exclude", "demo-adp/model-a/20260301_101502", "--reason", "aborted"])        # needs a replacement
    assert [j for j in _plan()[0] if j[1] == "model-a"] == [("demo-adp", "model-a", 1)]
    # the entry's conditions changed after the run was recorded: the old run no longer counts
    Path("campaign_matrix.yaml").write_text(MATRIX.replace("max_steps: 10", "max_steps: 20"))
    assert [j for j in _plan()[0] if j[1] == "model-a"] == [("demo-adp", "model-a", 0), ("demo-adp", "model-a", 1)]


def test_plan_overrides_for_one_off_launches(bench):
    assert _plan(entries=["model-a-high"], n=1)[0] == [("demo-adp", "model-a-high", 0)]
    jobs, lines = _plan(entries=["model-a-spire"])                       # naming a disabled entry launches it
    assert jobs == [("demo-adp", "model-a-spire", 0), ("demo-adp", "model-a-spire", 1)]
    assert "entry is disabled" in lines[0]
    assert _plan(tasks=["demo-heavy"])[0] == []                          # no entry lists that task
    with pytest.raises(KeyError):
        _plan(entries=["nope"])


def test_dry_run_prints_the_commands_and_launches_nothing(bench, capsys):
    assert campaign.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "rtlscout not installed" in out
    assert ("[baseline] demo-adp/verilog: python -m rtlscout.run_eval benchmarks/demo_verilog/context/starting_point.v"
            in out)
    assert "[run] demo-adp/model-a-high r0: RTLSCOUT_REASONING_EFFORT=high python -m rtlscout.run_benchmark" in out
    assert "dry run: 1 baseline(s) and 4 run(s) would be launched, nothing was started" in out
    assert not RUNS.exists()


def test_real_launch_needs_rtlscout_installed(bench, capsys):
    assert campaign.main([]) == 1
    assert "rtlscout is not installed" in capsys.readouterr().err and not RUNS.exists()


def test_models_that_could_not_be_recorded_are_not_launched(bench, monkeypatch, capsys):
    stub_env(monkeypatch)
    assert campaign.main(["--entry", "model-c", "--name", "c1"]) == 1
    assert "not marked open_weights" in capsys.readouterr().err and not RUNS.exists()


# -------------------------------------------------------------------------- full round with the rtlscout stub

def test_campaign_record_resume_round_trip(bench, monkeypatch, capsys):
    stub_env(monkeypatch)
    assert campaign.main(["--name", "c1", "--parallel", "2"]) == 0
    out = capsys.readouterr().out
    assert "[baseline] demo-adp/verilog: ok  cost 1,000.00" in out and out.count("[done]") == 4
    assert "campaign c1: 4 of 4 runs completed, 1 baseline(s) measured -> runs/campaigns/c1.json" in out

    camp = _campaign_file()
    meta = camp["campaign"]
    assert (meta["suite"], meta["rtlscout"], meta["spire_hdl"], meta["parallel"]) == ("suite-t1", "0.2.0", "0.4.0", 2)
    assert meta["argv"] == ["--name", "c1", "--parallel", "2"] and "finished" in meta
    assert camp["baselines"]["demo-adp/verilog"]["cost"] == 1000.0
    runs = camp["runs"]
    assert sorted((r["entry"], r["repeat"]) for r in runs) == [("model-a", 0), ("model-a", 1), ("model-a-high", 0),
                                                               ("model-a-high", 1)]
    assert all(r["status"] == "completed" and r["returncode"] == 0 and r["n_evals"] == 3 for r in runs)
    assert len({(r["entry"], r["run_id"]) for r in runs}) == 4                    # run ids are unique within an entry
    one = next(r for r in runs if r["entry"] == "model-a-high")
    assert one["run_dir"] == f"demo-adp/model-a-high/demo_verilog/example_model-a/{one['run_id']}"
    assert one["best_cost"] == 720.0 and one["reasoning_effort"] == "high"
    assert one["cmd"][:3] == ["python", "-m", "rtlscout.run_benchmark"]
    assert next(r for r in runs if r["entry"] == "model-a")["best_cost"] == 800.0   # effort env reached only 'high'
    log = (RUNS / "logs" / "c1" / "demo-adp__model-a__r0.log").read_text()
    assert log.startswith("$ python -m rtlscout.run_benchmark")

    # nothing recorded yet, but the finished local runs already count: a second invocation has nothing to do
    assert campaign.main(["--name", "c2"]) == 0
    assert "nothing to do" in capsys.readouterr().out and not (RUNS / "campaigns" / "c2.json").exists()

    # record: versions and conditions come from the campaign file, timing and tokens from the new per-eval fields
    assert record.main([]) == 0
    assert "recorded 4, already recorded 0, skipped 0, refused 0" in capsys.readouterr().out
    rec = json.loads((DATA / "runs/demo-adp/model-a-high" / one["run_id"] / "record.json").read_text())
    assert (rec["origin"], rec["suite"], rec["rtlscout"], rec["spire_hdl"], rec["campaign"]) == \
        ("campaign", "suite-t1", "0.2.0", "0.4.0", "c1")
    assert rec["reasoning_effort"] == "high" and rec["best"]["cost"] == 720.0
    assert [e["t_min"] for e in rec["evals"]] == [0.5, 1.0, 1.5] and rec["evals"][2]["tok_in"] == 6000
    lb = load_leaderboard(DATA, "demo-adp")
    assert sorted(lb["runs"]) == ["model-a", "model-a-high"] and all(len(v) == 2 for v in lb["runs"].values())
    assert lb["baselines"]["verilog"] == dict(cost=1000.0, area=100.0, delay=10.0, suite="suite-t1", rtlscout="0.2.0",
                                              campaign="c1")

    # the published copy of the campaign file has no local paths
    pub = json.loads((DATA / "campaigns/c1.json").read_text())
    assert all(r["recorded"] and "run_dir" not in r and "log" not in r for r in pub["runs"])
    assert "log" not in pub["baselines"]["demo-adp/verilog"] and str(bench) not in json.dumps(pub)

    assert record.check_data(DATA, load_matrix())[0] == []
    assert record.main([]) == 0 and "recorded 0, already recorded 4" in capsys.readouterr().out
    assert campaign.main([]) == 0 and "nothing to do" in capsys.readouterr().out
    # one more run of one entry; its baseline is known for this suite and rtlscout version, so none is measured
    assert campaign.main(["--entry", "model-a", "-n", "1", "--name", "c3"]) == 0
    c3 = _campaign_file("c3")
    assert c3["baselines"] == {} and [(r["entry"], r["repeat"]) for r in c3["runs"]] == [("model-a", 2)]


def test_failed_runs_are_reported_and_relaunched_on_resume(bench, monkeypatch, capsys):
    stub_env(monkeypatch)
    Path("campaign_matrix.yaml").write_text(
        MATRIX.replace("open_weights_only: true", "open_weights_only: false")
        + "  - id: crashy\n    model: openrouter:example/crash\n    repeats: 1\n"
        + "  - id: cut\n    model: openrouter:example/cut-short\n    repeats: 1\n")
    assert campaign.main(["--entry", "crashy", "--entry", "cut", "--name", "c1"]) == 1
    out = capsys.readouterr().out
    by_entry = {r["entry"]: r for r in _campaign_file()["runs"]}
    crashy = by_entry["crashy"]
    assert crashy["status"] == "launch-failed" and crashy["run_dir"] is None and crashy["returncode"] == 3
    assert by_entry["cut"]["status"] == "ended-early" and by_entry["cut"]["run_id"] is not None
    assert "not completed: demo-adp/crashy: launch-failed, see runs/logs/c1/demo-adp__crashy__r0.log" in out
    # neither counts as done, and record skips them without failing
    jobs, _ = campaign.plan(load_matrix(), DATA, RUNS, entries=["crashy", "cut"])
    assert len(jobs) == 2
    assert record.main([]) == 0                              # unfinished runs are skipped, not an error
    out = capsys.readouterr().out
    assert "skip     demo-adp/crashy r0: launch-failed (no run directory)" in out
    assert "skip     demo-adp/cut/" in out and "ended-early (no result.json: the run did not finish)" in out
    assert "recorded 0, already recorded 0, skipped 2, refused 0" in out


def test_baselines_only(bench, monkeypatch, capsys):
    stub_env(monkeypatch)
    assert campaign.main(["--baselines-only", "--name", "b1"]) == 0
    camp = _campaign_file("b1")
    assert camp["runs"] == [] and list(camp["baselines"]) == ["demo-adp/verilog"]
    assert camp["baselines"]["demo-adp/verilog"]["status"] == "ok"
    record.main([])
    assert load_leaderboard(DATA, "demo-adp")["baselines"]["verilog"]["cost"] == 1000.0
    assert (DATA / "campaigns/b1.json").is_file()


def test_heavy_tasks_never_overlap(bench, monkeypatch):
    stub_env(monkeypatch)
    Path("campaign_matrix.yaml").write_text(MATRIX.replace("    heavy: true\n    enabled: false\n", "    heavy: true\n")
                                            .replace("  tasks: [demo-adp]", "  tasks: [demo-heavy]"))
    peak = {"heavy": 0}
    orig = campaign.Campaign._exec

    def spy(self, cmd, env, log):
        peak["heavy"] = max(peak["heavy"], self.running_heavy)
        return orig(self, cmd, env, log)

    monkeypatch.setattr(campaign.Campaign, "_exec", spy)
    assert campaign.main(["--name", "h1", "--parallel", "3"]) == 0
    assert peak["heavy"] == 1 and len(_campaign_file("h1")["runs"]) == 4


def test_interrupt_stops_the_runs_and_keeps_the_campaign_file(bench):
    """Ctrl-C: running processes are stopped, the campaign file is written, and the same command resumes."""
    Path("campaign_matrix.yaml").write_text(MATRIX.replace("open_weights_only: true", "open_weights_only: false")
                                            + "  - id: slow\n    model: openrouter:example/slow\n    repeats: 2\n")
    # the real package is not installed here, so the child gets a tiny wrapper that reports a version
    Path("run_campaign.py").write_text(
        "import sys\nfrom rtlscout_bench import campaign\n"
        "campaign.installed_version = {'rtlscout': '0.2.0', 'spire-hdl': '0.4.0'}.get\n"
        "campaign.LAUNCH_STAGGER_S = 0.05\nsys.exit(campaign.main(sys.argv[1:]))\n")
    env = dict(os.environ, PYTHONPATH=str(STUB) + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "run_campaign.py", "--entry", "slow", "--name", "c1"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 30
    while time.time() < deadline and len(list(RUNS.glob("demo-adp/slow/*/*/*"))) < 2:      # both runs are under way
        time.sleep(0.05)
    proc.send_signal(signal.SIGINT)
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode == 130, out
    assert "interrupted: stopping 2 running process(es)" in out
    camp = _campaign_file()
    assert camp["campaign"]["interrupted"] is True
    assert [r["status"] for r in camp["runs"]] == ["ended-early", "ended-early"]
    jobs, lines = campaign.plan(load_matrix(), DATA, RUNS, entries=["slow"])
    assert len(jobs) == 2 and "0 of 2 runs done, 2 to launch" in lines[0]
