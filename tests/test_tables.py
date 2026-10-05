import json
import statistics
from pathlib import Path

import pytest

from rtlscout_bench import record, tables
from rtlscout_bench.record import load_leaderboard, save_leaderboard
from rtlscout_bench.tables import BoardError, aggregate, diff_boards, load_board, scale_exponent

from conftest import MATRIX, adopt, make_run

DATA = Path("data")


def _three_runs(tmp_path, entry="model-a", bests=(800.0, 700.0, 900.0), start="2026030110", **kw):
    runs = [make_run(tmp_path / f"raw-{entry}", f"{start[:8]}_{start[8:]}{i:02d}00",
                     costs=((True, 1000.0), (False, None), (True, b)), **kw) for i, b in enumerate(bests)]
    assert adopt(runs, entry=entry) == 0
    return runs


def _set_baseline(cost=1000.0, language="verilog"):
    lb = load_leaderboard(DATA, "demo-adp")
    lb.update(title="Demo design", metric_label="area × delay", unit="µm²·ps")
    lb["baselines"][language] = dict(cost=cost, area=cost / 20, delay=20.0, suite="suite-t1")
    save_leaderboard(DATA, lb)


def test_aggregate_uses_the_sample_standard_deviation():
    recs = [dict(best=dict(cost=c), runtime_min=m, usd=u)
            for c, m, u in ((2.0, 10, 1.0), (4.0, 20, 2.0), (9.0, 30, 6.0))]
    a = aggregate(recs)
    assert a["n"] == 3 and a["mean"] == 5.0 and a["min"] == 2.0 and a["median"] == 4.0 and a["max"] == 9.0
    assert a["sd"] == pytest.approx(statistics.stdev([2.0, 4.0, 9.0]))
    assert a["sd"] != pytest.approx(statistics.pstdev([2.0, 4.0, 9.0]))
    assert a["runtime"] == 20 and a["runtime_sd"] == pytest.approx(10.0) and a["usd"] == 3.0
    assert aggregate([dict(best=dict(cost=3.0), runtime_min=5, usd=None)]) == dict(
        n=1, no_pass=0, mean=3.0, sd=0.0, min=3.0, max=3.0, median=3.0, runtime=5, runtime_sd=0.0, usd=None,
        usd_sd=None)


def test_runs_without_a_passing_design_are_counted_separately():
    a = aggregate([dict(best=dict(cost=3.0), runtime_min=5, usd=1.0), dict(best=None, runtime_min=7, usd=1.0)])
    assert (a["n"], a["no_pass"], a["mean"], a["runtime"]) == (1, 1, 3.0, 6)
    assert aggregate([dict(best=None, runtime_min=7, usd=1.0)]) is None


@pytest.mark.parametrize("value, exp", [(4_624_408.88, 6), (2.3e11, 9), (523_808, 3), (812.0, 0), (None, 0), (0, 0)])
def test_scale_exponent(value, exp):
    assert scale_exponent(value) == exp


def test_board_rows_are_ranked_and_labelled(bench, tmp_path):
    _three_runs(tmp_path, "model-a", (800.0, 700.0, 900.0))
    _three_runs(tmp_path, "model-a-high", (600.0, 650.0, 700.0), start="2026030211")
    _set_baseline()
    board = load_board(DATA, "demo-adp", "suite-t1")
    assert [r["entry"] for r in board["rows"]] == ["model-a-high", "model-a"]
    assert [r["label"] for r in board["rows"]] == ["Model A · high effort", "Model A"]
    top = board["rows"][0]
    assert top["agg"]["mean"] == pytest.approx(650.0) and top["agg"]["sd"] == pytest.approx(50.0)
    assert top["agg"]["n"] == 3
    assert top["seed"] is False and top["open_weights"] is True and top["vendor"] == "Example Lab"
    assert board["scale_exp"] == 3 and board["baselines"]["verilog"]["cost"] == 1000.0


def test_seed_rows_are_flagged_against_the_current_suite(bench, tmp_path):
    adopt([make_run(tmp_path / "raw")], origin="seed", suite="pre-t1")
    assert load_board(DATA, "demo-adp", "suite-t1")["rows"][0]["seed"] is True
    assert load_board(DATA, "demo-adp", "pre-t1")["rows"][0]["seed"] is False


def test_one_row_never_mixes_conditions(bench, tmp_path):
    _three_runs(tmp_path, "model-a", (800.0, 700.0))
    p = DATA / "runs/demo-adp/model-a/20260301_100000/record.json"
    rec = json.loads(p.read_text())
    rec["max_steps"] = 40
    p.write_text(json.dumps(rec))
    with pytest.raises(BoardError, match="differ in max_steps"):
        load_board(DATA, "demo-adp", "suite-t1")


def test_selected_run_without_a_record_is_an_error(bench, tmp_path):
    _three_runs(tmp_path, "model-a", (800.0,))
    lb = load_leaderboard(DATA, "demo-adp")
    lb["runs"]["model-a"].append("20270101_000000")
    save_leaderboard(DATA, lb)
    with pytest.raises(BoardError, match="does not exist"):
        load_board(DATA, "demo-adp", "suite-t1")


def test_text_markdown_and_latex_tables(bench, tmp_path, capsys):
    _three_runs(tmp_path, "model-a", (800.0, 700.0, 900.0))
    _set_baseline()
    assert tables.main([]) == 0
    text = capsys.readouterr().out
    assert "demo-adp: Demo design  [area × delay, 10^3 µm²·ps; lower is better]" in text
    assert "starting point (Verilog): 1,000.00 = 1.000" in text
    row = next(line for line in text.splitlines() if "Model A" in line)
    assert row.split() == ["1", "Model", "A", "3", "0.800", "±", "0.100", "0.700", "-20.0%", "10", "±", "0", "0.2",
                           "±", "0.0", "suite-t1"]
    assert tables.main(["--format", "md"]) == 0
    md = capsys.readouterr().out
    assert "| 1 | Model A | 3 | 0.800 ± 0.100 | 0.700 | -20.0% | 10 ± 0 | 0.2 ± 0.0 | suite-t1 |" in md
    assert "mean ± sample sd over n independent runs" in md and "Starting point: Verilog 1.000 (10^3 µm²·ps)." in md
    assert tables.main(["--format", "tex", "-o", "t.tex"]) == 0
    tex = Path("t.tex").read_text()
    assert r"1 & Model A & 3 & 0.800 $\pm$ 0.100 & 0.700 & -20.0\% & 10 $\pm$ 0 & 0.2 $\pm$ 0.0 \\" in tex
    assert r"\begin{tabular}" in tex and r"\label{tab:demo-adp}" in tex


def test_runs_table_lists_every_selected_run(bench, tmp_path, capsys):
    _three_runs(tmp_path, "model-a", (800.0, 700.0))
    _set_baseline()
    capsys.readouterr()
    assert tables.main(["--runs"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("| Model A")]
    assert len(lines) == 2 and "`model-a/20260301_100100` | 0.700" in lines[0]
    assert "| 3 / 6 | 3 / 1 | 10 | 0.24 | suite-t1 | 0.0.test |" in lines[0]


def test_excluded_runs_are_listed_with_their_reason(bench, tmp_path, capsys):
    _three_runs(tmp_path, "model-a", (800.0, 700.0))
    record.main(["--exclude", "demo-adp/model-a/20260301_100100", "--reason", "testbench was edited"])
    board = load_board(DATA, "demo-adp", "suite-t1")
    assert board["rows"][0]["agg"]["n"] == 1
    assert board["excluded"] == [dict(entry="model-a", run="20260301_100100", reason="testbench was edited", cost=700.0,
                                      suite="suite-t1")]
    assert "model-a/20260301_100100: testbench was edited" in tables.render_text(board)


def test_head_to_head_pairs_languages_of_the_same_model(bench, tmp_path, capsys):
    Path("campaign_matrix.yaml").write_text(MATRIX)
    _three_runs(tmp_path, "model-a", (800.0, 700.0))
    _three_runs(tmp_path, "model-a-spire", (600.0, 650.0), start="2026030211", benchmark="demo_spire")
    _set_baseline(1000.0, "verilog")
    _set_baseline(1200.0, "spirehdl")
    assert tables.main(["--head-to-head"]) == 0
    out = capsys.readouterr().out
    assert "| Model A | 1.000 / 1.200 | 0.700 (0.750) | 0.600 (0.625) | -30.0% / -50.0% | Spire -14.3% |" in out
    board = load_board(DATA, "demo-adp", "suite-t1")
    assert sorted(r["label"] for r in board["rows"]) == ["Model A", "Model A · Spire HDL"]


def test_labels_that_collide_get_the_entry_id(bench, tmp_path):
    Path("campaign_matrix.yaml").write_text(
        MATRIX + "  - id: model-a-again\n    model: openrouter:example/model-a\n    max_steps: 5\n")
    _three_runs(tmp_path, "model-a", (800.0,))
    _three_runs(tmp_path, "model-a-again", (700.0,), start="2026030211", num_steps=5)
    board = load_board(DATA, "demo-adp", "suite-t1")
    assert [r["label"] for r in board["rows"]] == ["Model A · model-a-again", "Model A · model-a"]


def test_diff_of_two_leaderboard_states(bench, tmp_path):
    _three_runs(tmp_path, "model-a", (800.0, 700.0))
    before = load_board(DATA, "demo-adp", "suite-t1")
    _three_runs(tmp_path, "model-a-high", (600.0,), start="2026030211")
    adopt([make_run(tmp_path / "more", "20260305_090000", costs=((True, 500.0),))])
    record.main(["--exclude", "demo-adp/model-a/20260301_100000", "--reason", "aborted"])
    _set_baseline(1000.0)
    after = load_board(DATA, "demo-adp", "suite-t1")
    lines = diff_boards(before, after)
    added = "+ row added    Model A · high effort [model-a-high]: n=1, mean 0.600"
    changed = "~ row changed  Model A [model-a]: n=2, mean 0.750 ± 0.071"
    assert any(line.startswith(added) for line in lines)
    assert any(line.startswith(changed) and "runs +1 -1" in line for line in lines)
    assert "! run excluded model-a/20260301_100000: aborted" in lines
    assert "~ starting point (verilog): none -> 1,000.00" in lines
    assert diff_boards(after, after) == []
    assert [line[:11] for line in diff_boards(None, before)] == ["+ row added"]


def test_campaign_table_from_a_campaign_file(bench, tmp_path, capsys):
    runs_dir = Path("runs")
    run = make_run(runs_dir / "demo-adp" / "model-a", elapsed=True)
    camp = dict(schema=1, campaign=dict(name="c1", suite="suite-t1", rtlscout="0.2.0"),
                baselines={"demo-adp/verilog": dict(task="demo-adp", language="verilog", cost=1000.0, status="ok")},
                runs=[dict(task="demo-adp", entry="model-a", language="verilog", repeat=0, status="completed",
                           run_dir=str(run.relative_to(runs_dir)), run_id=run.name, best_cost=1.0, best_eval=1,
                           n_evals=4),
                      dict(task="demo-adp", entry="model-a", language="verilog", repeat=1, status="launch-failed",
                           run_dir=None, run_id=None, best_cost=None, best_eval=None, n_evals=0)])
    (runs_dir / "campaigns").mkdir()
    (runs_dir / "campaigns" / "c1.json").write_text(json.dumps(camp))
    assert tables.main(["--campaign", "runs/campaigns/c1.json"]) == 0
    out = capsys.readouterr().out
    assert "# Campaign c1 · suite-t1 · rtlscout 0.2.0" in out
    assert "| `demo-adp` | `model-a` | 2 | 1,000 | 800 (800) | -20.0% |" in out       # best re-read from the run folder
    assert "| `demo-adp` | `model-a` | 0 | `20260301_101500` | completed | 800 (eval 3) | 4 |" in out
    assert "| launch-failed |" in out
