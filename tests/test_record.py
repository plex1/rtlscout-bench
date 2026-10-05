import json
import os
from pathlib import Path

import pytest

from rtlscout_bench import record
from rtlscout_bench.config import load_matrix
from rtlscout_bench.record import RecordError, build_record, check_data, load_leaderboard, load_models, validate_record

from conftest import MATRIX, adopt, make_run


def _cond(entry="model-a"):
    m = load_matrix()
    return m.conditions(m.tasks["demo-adp"], m.entry(entry))


PROV = dict(suite="suite-t1", origin="campaign", rtlscout="0.2.0", spire_hdl="0.4.0", bench_commit="abc", campaign="c1")


def _load(entry="model-a", run_id="20260301_101500"):
    return json.loads(Path(f"data/runs/demo-adp/{entry}/{run_id}/record.json").read_text())


# ---------------------------------------------------------------------------------------------- building a record

def test_record_of_a_run_without_timing_fields(bench, tmp_path):
    run = make_run(tmp_path / "raw")
    rec = build_record(run, _cond(), PROV, load_models(Path("data")))
    assert validate_record(rec) == []
    assert (rec["schema"], rec["suite"], rec["origin"], rec["status"]) == (1, "suite-t1", "campaign", "completed")
    assert (rec["task"], rec["entry"], rec["benchmark"], rec["language"]) == \
        ("demo-adp", "model-a", "demo_verilog", "verilog")
    assert (rec["model"], rec["open_weights"], rec["reasoning_effort"], rec["max_steps"]) == \
        ("openrouter:example/model-a", True, "default", 10)
    assert rec["flags"] == ["--skip-cec"] and rec["env"] == {}
    assert rec["technology"] == "asap7" and rec["target_delay"] is None
    assert rec["started"] == "2026-03-01T10:15"
    assert rec["best"] == dict(cost=800.0, area=40.0, delay=20.0, eval=3, step=6, file="design_3.sv")
    assert (rec["n_evals"], rec["n_failed"], rec["steps_used"], rec["runtime_min"]) == (4, 1, 10, 10.0)
    assert rec["tokens"] == dict(inp=200_000, out=10_000, cache_w=0, cache_r=0)
    assert rec["usd"] == pytest.approx(0.24) and rec["price_per_mtok"] == [1.0, 4.0]      # 0.2 M * $1 + 0.01 M * $4
    first, failed = rec["evals"][0], rec["evals"][1]
    assert first == dict(e=1, s=2, ok=True, c=1000.0, area=50.0, delay=20.0, power=1.5, td=500.0, ctx=1000, t_min=2.0,
                         tok_in=None, tok_out=None)
    assert failed["ok"] is False and failed["c"] is None and failed["area"] is None and failed["td"] is None
    # minutes since start come from the eval_N/result.json modification times
    assert [e["t_min"] for e in rec["evals"]] == [2.0, 4.0, 6.0, 8.0]


def test_eval_times_survive_a_run_stamped_in_another_time_zone(bench, tmp_path):
    run = make_run(tmp_path / "raw", tz_offset_h=-2.0)        # stamp written by a clock two hours ahead of UTC
    rec = build_record(run, _cond(), PROV, {})
    assert [e["t_min"] for e in rec["evals"]] == [2.0, 4.0, 6.0, 8.0]


def test_no_eval_times_rather_than_wrong_ones_when_mtimes_are_gone(bench, tmp_path):
    run = make_run(tmp_path / "raw", run_id="20200101_000000", with_mtimes=False)   # files carry today's mtime
    rec = build_record(run, _cond(), PROV, {})
    assert [e["t_min"] for e in rec["evals"]] == [None] * 4 and validate_record(rec) == []


def test_no_eval_times_when_all_files_share_one_mtime(bench, tmp_path):
    run = make_run(tmp_path / "raw")                 # as after a copy that did not keep modification times
    end = (run / "result.json").stat().st_mtime
    for p in run.glob("eval_*/result.json"):
        os.utime(p, (end, end))
    rec = build_record(run, _cond(), PROV, {})
    assert [e["t_min"] for e in rec["evals"]] == [None] * 4


def test_new_runs_use_elapsed_s_and_cumulative_tokens(bench, tmp_path):
    run = make_run(tmp_path / "raw", elapsed=True, with_mtimes=False)
    rec = build_record(run, _cond(), PROV, load_models(Path("data")))
    assert [e["t_min"] for e in rec["evals"]] == [2.0, 4.0, 6.0, 8.0]
    assert [(e["tok_in"], e["tok_out"]) for e in rec["evals"]] == [(50_000, 2_500), (100_000, 5_000), (150_000, 7_500),
                                                                   (200_000, 10_000)]
    assert "tok_cw" not in rec["evals"][0]           # cache counters only appear when a provider reports them


def test_run_without_a_passing_design_has_no_best(bench, tmp_path):
    run = make_run(tmp_path / "raw", costs=((False, None), (False, None)))
    rec = build_record(run, _cond(), PROV, {})
    assert rec["best"] is None and rec["n_failed"] == 2 and validate_record(rec) == []


def test_run_that_ended_with_an_error_is_marked(bench, tmp_path):
    run = make_run(tmp_path / "raw", error="provider returned 503 at step 4")
    rec = build_record(run, _cond(), PROV, {})
    assert rec["status"] == "ended-early" and "503" in rec["error"]


@pytest.mark.parametrize("kwargs, expected", [
    (dict(model="example/other"), "model is 'example/other'"),
    (dict(benchmark="demo_spire"), "benchmark is 'demo_spire'"),
    (dict(cost_metric="area"), "cost metric is 'area'"),
    (dict(num_steps=11), "more than the entry's max_steps"),
])
def test_run_that_does_not_match_the_entry_is_refused(bench, tmp_path, kwargs, expected):
    run = make_run(tmp_path / "raw", **kwargs)
    with pytest.raises(RecordError, match=expected):
        build_record(run, _cond(), PROV, {})


def test_unfinished_run_is_refused(bench, tmp_path):
    run = make_run(tmp_path / "raw")
    (run / "result.json").unlink()
    with pytest.raises(RecordError, match="no result.json"):
        build_record(run, _cond(), PROV, {})


def test_unknown_price_gives_no_dollar_figure(bench, tmp_path):
    rec = build_record(make_run(tmp_path / "raw"), _cond(), PROV, {})
    assert rec["usd"] is None and rec["price_per_mtok"] is None and rec["open_weights"] is None


def test_cache_tokens_are_priced_and_the_cache_price_is_kept(bench, tmp_path):
    run = make_run(tmp_path / "raw")
    res = json.loads((run / "result.json").read_text())
    res["token_usage"].update(cache_creation_input_tokens=1_000_000, cache_read_input_tokens=2_000_000)
    (run / "result.json").write_text(json.dumps(res))
    models = {"openrouter:example/model-a": {"open_weights": True, "price_per_mtok":
                                             {"input": 1.0, "output": 4.0, "cache_write": 1.25, "cache_read": 0.1}}}
    rec = build_record(run, _cond(), PROV, models)
    assert rec["usd"] == pytest.approx(0.24 + 1.25 + 0.2) and rec["price_cache_per_mtok"] == [1.25, 0.1]


# ------------------------------------------------------------------------------------------- writing + refusals

def test_adopting_run_dirs_writes_record_artefacts_and_selection(bench, tmp_path, capsys):
    runs = [make_run(tmp_path / "raw", rid) for rid in ("20260301_101500", "20260301_101502")]
    assert adopt(runs, origin="seed", suite="pre-t1", extra=["--reasoning-effort-source", "campaign notes"]) == 0
    assert "recorded 2, already recorded 0" in capsys.readouterr().out
    dest = Path("data/runs/demo-adp/model-a/20260301_101500")
    assert sorted(p.name for p in dest.iterdir()) == ["best_design", "chat_log.txt", "record.json", "summary.txt"]
    assert (dest / "best_design" / "_best_meta.json").is_file() and not list(dest.glob("eval_*"))
    rec = _load()
    assert (rec["suite"], rec["origin"], rec["rtlscout"], rec["spire_hdl"]) == ("pre-t1", "seed", "0.0.test", None)
    assert rec["reasoning_effort_source"] == "campaign notes" and "campaign" not in rec
    assert load_leaderboard(Path("data"), "demo-adp")["runs"] == {"model-a": ["20260301_101500", "20260301_101502"]}
    # one evaluation per line: a record stays readable in a diff
    lines = (dest / "record.json").read_text().splitlines()
    assert sum(1 for line in lines if line.startswith('  {"e":')) == 4
    # recording again changes nothing
    assert adopt(runs, origin="seed", suite="pre-t1") == 0
    assert "recorded 0, already recorded 2" in capsys.readouterr().out


def test_seed_origin_must_say_what_it_is(bench, tmp_path):
    run = make_run(tmp_path / "raw")
    with pytest.raises(SystemExit):
        record.main([str(run), "--task", "demo-adp", "--entry", "model-a", "--origin", "seed"])
    with pytest.raises(SystemExit):
        record.main([str(run)])                         # a raw run dir needs --task/--entry/--origin


def test_model_not_marked_open_weights_is_refused_while_the_policy_is_on(bench, tmp_path, capsys):
    run = make_run(tmp_path / "raw", model="example/model-c-closed")
    assert adopt([run], entry="model-c") == 1
    out = capsys.readouterr().out
    assert "REFUSED" in out and 'is not marked "open_weights": true' in out and "refused 1" in out
    assert not list(Path("data/runs").rglob("record.json"))
    # a model that is missing from models.json is refused as well
    Path("campaign_matrix.yaml").write_text(MATRIX.replace("example/model-c-closed", "example/unlisted"))
    run2 = make_run(tmp_path / "raw2", model="example/unlisted")
    assert adopt([run2], entry="model-c") == 1 and not list(Path("data/runs").rglob("record.json"))


def test_policy_switch_off_allows_other_models(bench, tmp_path):
    Path("campaign_matrix.yaml").write_text(MATRIX.replace("open_weights_only: true", "open_weights_only: false"))
    run = make_run(tmp_path / "raw", model="example/model-c-closed")
    assert adopt([run], entry="model-c") == 0
    assert _load("model-c")["open_weights"] is False


@pytest.mark.parametrize("secret", ["sk-ant-" + "x" * 8, "sk-or-" + "v1-abc", "OPENROUTER_" + "API_KEY=abc"])
def test_transcript_with_a_key_pattern_is_refused(bench, tmp_path, capsys, secret):
    run = make_run(tmp_path / "raw", chat_extra=f"$ env\n{secret}\n")
    assert adopt([run]) == 1
    out = capsys.readouterr().out
    assert "REFUSED" in out and "API-key pattern at line 6" in out
    assert secret not in out                                   # the report never repeats the secret itself
    assert not list(Path("data/runs").rglob("*.txt"))


def test_key_pattern_in_a_best_design_file_is_refused(bench, tmp_path, capsys):
    run = make_run(tmp_path / "raw")
    (run / "best_design" / "notes.txt").write_text("export MY_" + "API_KEY=1\n")
    assert adopt([run]) == 1 and "notes.txt: contains an API-key pattern" in capsys.readouterr().out


def test_home_directory_paths_in_a_transcript_give_a_warning(bench, tmp_path, capsys):
    run = make_run(tmp_path / "raw", chat_extra="%Error: /home/someone/work/tb.sv:60")
    assert adopt([run]) == 0
    assert "warning  demo-adp/model-a/20260301_101500: chat_log.txt mentions /home/someone" in capsys.readouterr().out


def test_missing_path_is_an_error(bench, capsys):
    assert record.main(["no/such/dir", "--task", "demo-adp", "--entry", "model-a", "--origin", "manual"]) == 1
    assert "not found" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------- curation

def test_exclude_needs_a_reason_and_include_undoes_it(bench, tmp_path, capsys):
    adopt([make_run(tmp_path / "raw", rid) for rid in ("20260301_101500", "20260301_101502")])
    ref = "demo-adp/model-a/20260301_101502"
    with pytest.raises(SystemExit):
        record.main(["--exclude", ref])
    assert record.main(["--exclude", ref, "--reason", "provider outage at step 3"]) == 0
    lb = load_leaderboard(Path("data"), "demo-adp")
    assert lb["runs"] == {"model-a": ["20260301_101500"]}
    assert lb["exclude"] == [dict(entry="model-a", run="20260301_101502", reason="provider outage at step 3")]
    assert Path(f"data/runs/{ref}/record.json").is_file()       # an excluded run stays in the data repo
    assert record.main(["--include", ref]) == 0
    lb = load_leaderboard(Path("data"), "demo-adp")
    assert lb["runs"] == {"model-a": ["20260301_101500", "20260301_101502"]} and lb["exclude"] == []
    assert record.main(["--exclude", "demo-adp/model-a/20990101_000000", "--reason", "x"]) == 1


def test_runs_of_a_new_suite_supersede_the_seed_runs_of_the_entry(bench, tmp_path, capsys):
    adopt([make_run(tmp_path / "old", "20260101_000000")], origin="seed", suite="pre-t1")
    adopt([make_run(tmp_path / "new", "20260301_101500")])
    out = capsys.readouterr().out
    assert "note     model-a/20260101_000000 (pre-t1) superseded by suite-t1 runs" in out
    lb = load_leaderboard(Path("data"), "demo-adp")
    assert lb["runs"] == {"model-a": ["20260301_101500"]}
    assert lb["exclude"] == [dict(entry="model-a", run="20260101_000000", reason="superseded by suite-t1 runs")]


def test_run_that_ended_early_is_recorded_but_not_selected(bench, tmp_path):
    adopt([make_run(tmp_path / "raw", error="provider returned 503")])
    lb = load_leaderboard(Path("data"), "demo-adp")
    assert lb["runs"] == {} and lb["exclude"][0]["reason"].startswith("ended early: provider returned 503")
    assert _load()["status"] == "ended-early"


# ------------------------------------------------------------------------------------------------ data check

def _check():
    return check_data(Path("data"), load_matrix())


def test_check_passes_on_recorded_data(bench, tmp_path, capsys):
    adopt([make_run(tmp_path / "raw")])
    errors, warnings, counts = _check()
    assert errors == [] and warnings == [] and counts["records"] == 1 and counts["selected"] == 1
    assert record.main(["--check"]) == 0
    assert ("data check: 1 records (1 selected, 0 excluded), 1 leaderboard file(s), 2 models, "
            "open-weights policy on: OK") in capsys.readouterr().out


def test_check_finds_broken_data(bench, tmp_path, capsys):
    adopt([make_run(tmp_path / "raw", rid) for rid in ("20260301_101500", "20260301_101502", "20260301_101504")])
    base = Path("data/runs/demo-adp/model-a")
    (base / "20260301_101500" / "chat_log.txt").unlink()
    (base / "20260301_101502" / "summary.txt").write_text("my key is sk-or-" + "v1-123\n")
    rec = _load(run_id="20260301_101504")
    del rec["tokens"]
    rec["n_evals"] = 99
    (base / "20260301_101504" / "record.json").write_text(json.dumps(rec))
    lb = load_leaderboard(Path("data"), "demo-adp")
    lb["runs"]["model-a"].append("20270101_000000")
    lb["exclude"].append(dict(entry="model-a", run="20260301_101500", reason=""))
    record.save_leaderboard(Path("data"), lb)
    errors, _, _ = _check()
    text = "\n".join(errors)
    for expected in ("20260301_101500: chat_log.txt is missing", "summary.txt: API-key pattern sk-or- at line 1",
                     "record.json: missing 'tokens'", "n_evals does not match",
                     "selected run model-a/20270101_000000 has no record",
                     "exclusion of model-a/20260301_101500 has no reason", "is both selected and excluded"):
        assert expected in text, expected
    assert record.main(["--check"]) == 1 and "error(s)" in capsys.readouterr().out


def test_check_enforces_the_open_weights_policy(bench, tmp_path):
    Path("campaign_matrix.yaml").write_text(MATRIX.replace("open_weights_only: true", "open_weights_only: false"))
    adopt([make_run(tmp_path / "raw", model="example/model-c-closed")], entry="model-c")
    assert _check()[0] == []
    Path("campaign_matrix.yaml").write_text(MATRIX)                 # policy back on: the stored run is now an error
    errors, _, _ = _check()
    assert any("model openrouter:example/model-c-closed is not marked open_weights" in e for e in errors)


def test_check_flags_a_record_in_the_wrong_directory(bench, tmp_path):
    adopt([make_run(tmp_path / "raw")])
    os.rename("data/runs/demo-adp/model-a", "data/runs/demo-adp/model-b")
    errors, _, _ = _check()
    assert any("directory does not match the record" in e for e in errors)


def test_validate_record_rejects_inconsistent_best(bench, tmp_path):
    rec = build_record(make_run(tmp_path / "raw"), _cond(), PROV, {})
    rec["best"]["cost"] = 1.0
    assert "best.cost is not the lowest passing cost among the evals" in validate_record(rec)
    assert validate_record([]) == ["not a JSON object"]
