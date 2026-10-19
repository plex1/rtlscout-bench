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


def test_row_labels_name_the_effort_and_the_language():
    rec = dict(reasoning_effort="default", language="verilog")
    assert tables._label("Model A", rec) == "Model A"                               # default, vendor default unknown
    assert tables._label("Model A", dict(rec, reasoning_effort_default="max")) == "Model A (max)"
    assert tables._label("Model A", dict(rec, reasoning_effort="high")) == "Model A (high)"
    assert tables._label("Model A", dict(rec, reasoning_effort="high", reasoning_effort_default="max")) == "Model A (high)"
    assert tables._label("Model A", dict(rec, reasoning_effort="off")) == "Model A (thinking off)"
    assert tables._label("Model A", dict(rec, reasoning_effort="off", language="spirehdl")) == \
        "Model A (thinking off) · Spire HDL"


def test_board_rows_are_ranked_and_labelled(bench, tmp_path):
    _three_runs(tmp_path, "model-a", (800.0, 700.0, 900.0))
    _three_runs(tmp_path, "model-a-high", (600.0, 650.0, 700.0), start="2026030211")
    _set_baseline()
    board = load_board(DATA, "demo-adp", "suite-t1")
    assert [r["entry"] for r in board["rows"]] == ["model-a-high", "model-a"]
    assert [r["label"] for r in board["rows"]] == ["Model A (high)", "Model A"]
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
    added = "+ row added    Model A (high) [model-a-high]: n=1, mean 0.600"
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


# ------------------------------------------------------------------------------------------ the overall table

def _board(task, rows, base=1000.0, title=None):
    """A resolved task board with only the fields the overall table reads. ``rows``: (entry, mean or None, suite)."""
    def row(entry, mean, suite):
        agg = None if mean is None else dict(n=3, no_pass=0, mean=mean, sd=10.0, min=mean - 10, max=mean + 10,
                                             median=mean, runtime=100.0, runtime_sd=5.0, usd=1.0, usd_sd=0.1)
        runs = [] if mean is None else [dict(best=dict(cost=mean))]        # one run as good as the mean
        return dict(entry=entry, label=entry.upper(), name=entry.upper(), model=f"x/{entry}", vendor="V",
                    open_weights=True, language="verilog", reasoning_effort="default", suite=suite, agg=agg, runs=runs)
    rows = sorted((row(*r) for r in rows), key=lambda r: (r["agg"] is None, r["agg"]["mean"] if r["agg"] else 0))
    return dict(task=task, title=title or task, baselines={"verilog": dict(cost=base)} if base else {}, rows=rows,
                excluded=[], scale_exp=0)


def test_overall_ranks_complete_entries_only_and_scores_both_ways():
    boards = [_board("t1", [("a", 500.0, "s2"), ("b", 600.0, "s2"), ("c", 400.0, "s2"), ("p", 300.0, "s1")]),
              _board("t2", [("a", 900.0, "s2"), ("b", 800.0, "s2"), ("c", 950.0, "s1")], base=1000.0)]
    o = tables.overall_board(boards)
    assert [t["task"] for t in o["tasks"]] == ["t1", "t2"] and o["n_complete"] == 3
    by = {r["entry"]: r for r in o["rows"]}
    # ranks per task among the three complete entries (p is ignored although it leads t1)
    assert [by[e]["cells"]["t1"]["rank"] for e in "abc"] == [2, 3, 1]
    assert [by[e]["cells"]["t2"]["rank"] for e in "abc"] == [2, 1, 3]
    assert by["a"]["mean_rank"] == 2.0 and by["b"]["mean_rank"] == 2.0 and by["c"]["mean_rank"] == 2.0
    # headroom-normalized: t1 best is c (400), t2 best is b (800)
    assert by["c"]["cells"]["t1"]["score"] == pytest.approx(1.0) and by["a"]["cells"]["t1"]["score"] == pytest.approx(500 / 600)
    assert by["b"]["cells"]["t2"]["score"] == pytest.approx(1.0) and by["c"]["cells"]["t2"]["score"] == pytest.approx(0.25)
    assert by["a"]["score"] == pytest.approx((500 / 600 + 0.5) / 2)
    # geomean of the cost ratios, and the mixed-suite label
    assert by["a"]["geomean"] == pytest.approx((0.5 * 0.9) ** 0.5) and by["a"]["reduction_pct"] == pytest.approx(100 * (1 - (0.45) ** 0.5))
    assert by["c"]["suite"] == "s2 / s1" and by["a"]["suite"] == "s2"
    assert by["a"]["usd_total"] == pytest.approx(2.0)
    # the partial entry keeps its cells and names the missing task; it sorts last
    assert by["p"]["complete"] is False and by["p"]["missing"] == ["t2"] and by["p"]["cells"]["t1"]["pct"] == pytest.approx(-70.0)
    assert by["p"]["cells"]["t1"]["pos"] == 1
    # penalized scores for the partial entry: on t1 it would rank first (300 < 400), t2 not run = worst rank 4
    assert by["p"]["cells"]["t1"]["rank"] == 1 and by["p"]["mean_rank"] == pytest.approx((1 + 4) / 2)
    # t1 score against the complete field's headroom (best 400): 700/600; t2 not run = 0 (nobody scored below 0)
    assert by["p"]["cells"]["t1"]["score"] == pytest.approx(700 / 600) and by["p"]["score"] == pytest.approx((700 / 600 + 0) / 2)
    assert by["p"]["geomean"] is None and o["worst_rank"] == 4
    assert o["rows"][-1]["entry"] == "p"
    # all three tie on mean rank: the geomean breaks the tie (c: 0.4*0.95, a: 0.5*0.9, b: 0.6*0.8)
    assert [r["entry"] for r in o["rows"][:3]] == ["c", "a", "b"]


def test_overall_scores_against_the_best_single_run():
    boards = [_board("t1", [("a", 500.0, "s"), ("b", 600.0, "s"), ("p", 450.0, "s")]), _board("t2", [("a", 900.0, "s"), ("b", 800.0, "s")])]
    rows = {r["entry"]: r for r in boards[0]["rows"]}
    rows["b"]["runs"] = [dict(best=dict(cost=640.0)), dict(best=dict(cost=200.0)), dict(best=None)]   # one deep run, one without a pass
    rows["p"]["runs"] = [dict(best=dict(cost=100.0))]                                                  # a partial entry's run does not set the scale
    o = tables.overall_board(boards)
    t = {x["task"]: x for x in o["tasks"]}
    assert (t["t1"]["start"], t["t1"]["best"]) == (1000.0, 200.0) and (t["t2"]["start"], t["t2"]["best"]) == (1000.0, 800.0)
    by = {r["entry"]: r for r in o["rows"]}
    assert by["a"]["cells"]["t1"]["score"] == pytest.approx(500 / 800) and by["b"]["cells"]["t1"]["score"] == pytest.approx(400 / 800)
    assert by["b"]["cells"]["t2"]["score"] == pytest.approx(1.0)                 # its only run is its mean: the best run known
    assert by["p"]["cells"]["t1"]["score"] == pytest.approx(550 / 800)           # scored against the ranked field's reference
    assert tables.headroom_score(1000.0, 200.0, 1000.0) == 0.0 and tables.headroom_score(1000.0, None, 500.0) == 0.0
    assert tables.headroom_score(1000.0, 1200.0, 500.0) == 0.0                   # nothing beat the start: no headroom


def test_overall_handles_no_headroom_and_no_passing_runs():
    boards = [_board("t1", [("a", 1000.0, "s"), ("b", 1100.0, "s")]), _board("t2", [("a", 700.0, "s"), ("b", None, "s")])]
    o = tables.overall_board(boards)
    by = {r["entry"]: r for r in o["rows"]}
    assert o["n_complete"] == 1 and by["a"]["complete"] and not by["b"]["complete"]
    assert by["a"]["cells"]["t1"]["score"] == 0.0        # nobody improved on t1: the task contributes 0
    assert by["a"]["score"] == pytest.approx(0.5) and by["a"]["mean_rank"] == 1.0
    assert by["b"]["missing"] == ["t2"] and "t2" not in by["b"]["cells"]
    assert by["b"]["mean_rank"] == pytest.approx((2 + 2) / 2) and by["b"]["score"] == pytest.approx((0.0 + 0.0) / 2)
    # a worse-than-start complete entry lowers the penalty floor for the others' missing tasks
    boards2 = [_board("t1", [("a", 500.0, "s"), ("b", 1200.0, "s"), ("q", 450.0, "s")]), _board("t2", [("a", 700.0, "s"), ("b", 600.0, "s")])]
    o2 = tables.overall_board(boards2); q = {r["entry"]: r for r in o2["rows"]}["q"]
    assert q["cells"]["t1"]["score"] == pytest.approx(550 / 500) and q["score"] == pytest.approx((550 / 500 + 0.0) / 2)
    assert "t3" not in {t["task"] for t in tables.overall_board(boards + [_board("t3", [("a", 1.0, "s")], base=None)])["tasks"]}


def test_overall_text_and_diff():
    boards = [_board("t1", [("a", 500.0, "s"), ("b", 600.0, "s")]), _board("t2", [("a", 900.0, "s"), ("b", 800.0, "s"), ("q", 100.0, "s")])]
    o = tables.overall_board(boards)
    text = tables.render_overall_text(o)
    assert text.splitlines()[0].startswith("overall: 2 task(s) (t1, t2); ranked by mean rank among the 2 entries")
    assert "1  A      1.50   0.92" in text.replace("  ", " ").replace("  ", " ") or "A" in text
    assert "partial: Q (1 of 2 tasks: t2 -90.0%; penalized mean rank 2.00, score 2.25)" in text
    assert tables.diff_overall(None, o) == ["overall: ranking (mean rank) A > B", "overall: + partial entry Q (1 of 2 tasks)"]
    assert tables.diff_overall(o, o) == []
    o2 = tables.overall_board([_board("t1", [("a", 500.0, "s"), ("b", 300.0, "s")]), boards[1]])
    assert tables.diff_overall(o, o2) == ["overall: ranking (mean rank) A > B -> B > A"]

