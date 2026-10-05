import json
import re
from pathlib import Path

from rtlscout_bench import record, site
from rtlscout_bench.record import load_leaderboard, save_leaderboard

from conftest import MATRIX, REPO, adopt, make_run

TEMPLATE = (REPO / "site" / "index.html").read_text()


def _site_data(out="_site") -> dict:
    text = Path(out, "data.js").read_text()
    assert text.startswith("window.RTLSCOUT_DATA = ") and text.rstrip().endswith(";")
    return json.loads(text[len("window.RTLSCOUT_DATA = "):].rstrip().rstrip(";"))


def _seed_and_baseline(tmp_path):
    adopt([make_run(tmp_path / "raw", rid, costs=((True, 1000.0), (False, None), (True, b)))
           for rid, b in (("20260301_101500", 800.0), ("20260301_101502", 700.0))], origin="seed", suite="pre-t1")
    lb = load_leaderboard(Path("data"), "demo-adp")
    lb.update(title="Demo design", description="A demo.", metric_label="area × delay", unit="µm²·ps",
              flow="Synthesis.")
    lb["baselines"]["verilog"] = dict(cost=1000.0, area=50.0, delay=20.0, suite="pre-t1")
    lb["notes"]["model-a"] = "A note."
    lb["footnotes"] = ["A general footnote."]
    save_leaderboard(Path("data"), lb)


def test_build_writes_the_template_copy_and_one_data_file(bench, tmp_path, capsys):
    _seed_and_baseline(tmp_path)
    assert site.main([]) == 0
    out = capsys.readouterr().out
    assert "1  Model A  2  0.750 ± 0.071     0.700     -25.0%" in out
    assert "wrote _site/index.html and _site/data.js" in out and "1 task(s), 1 row(s), 2 run(s)" in out
    assert sorted(p.name for p in Path("_site").iterdir()) == ["data.js", "index.html"]
    assert Path("_site/index.html").read_text() == TEMPLATE

    d = _site_data()
    assert d["suite"] == "suite-t1" and d["open_weights_only"] is True and d["data_url"] is None
    (task,) = d["tasks"]
    assert (task["id"], task["title"], task["metric"], task["unit"], task["scale_exp"]) == \
        ("demo-adp", "Demo design", "area_delay_product", "µm²·ps", 3)
    assert task["baselines"]["verilog"]["cost"] == 1000.0 and task["footnotes"] == ["A general footnote."]
    (row,) = task["rows"]
    assert (row["entry"], row["label"], row["open"], row["seed"], row["suite"], row["note"]) == \
        ("model-a", "Model A", True, True, "pre-t1", "A note.")
    assert row["agg"]["n"] == 2 and row["agg"]["mean"] == 750.0 and row["price"] == [1.0, 4.0]
    run = row["runs"][0]
    assert run["id"] == "20260301_101500" and run["path"] == "runs/demo-adp/model-a/20260301_101500"
    assert run["best_cost"] == 800.0 and run["n_evals"] == 3 and run["n_failed"] == 1 and run["origin"] == "seed"
    assert run["traj"][0] == dict(e=1, s=2, c=1000.0, ok=True, t=2.5)
    assert "chat_log" not in json.dumps(d)                 # transcripts stay in the data repo, the page links to them


def test_empty_data_repo_builds_an_empty_state_page(bench, capsys):
    assert site.main(["--out", "preview"]) == 0
    d = _site_data("preview")
    assert [t["id"] for t in d["tasks"]] == ["demo-adp"] and d["tasks"][0]["rows"] == []
    assert "0 row(s), 0 run(s)" in capsys.readouterr().out


def test_build_fails_when_a_selected_model_is_not_open_weights(bench, tmp_path, capsys):
    Path("campaign_matrix.yaml").write_text(MATRIX.replace("open_weights_only: true", "open_weights_only: false"))
    adopt([make_run(tmp_path / "raw", model="example/model-c-closed")], entry="model-c")
    assert site.main([]) == 0                                # allowed while the policy is off
    Path("campaign_matrix.yaml").write_text(MATRIX)
    Path("_site/data.js").unlink()
    assert site.main([]) == 1
    err = capsys.readouterr().err
    assert "site build failed; nothing was written" in err and "is not marked open_weights" in err
    assert not Path("_site/data.js").exists()
    # excluding the run makes the build pass again: only selected runs are gated
    record.main(["--exclude", "demo-adp/model-c/20260301_101500", "--reason", "policy"])
    assert site.main([]) == 0


def test_build_fails_on_a_missing_transcript_or_mixed_row(bench, tmp_path, capsys):
    _seed_and_baseline(tmp_path)
    Path("data/runs/demo-adp/model-a/20260301_101500/chat_log.txt").unlink()
    assert site.main([]) == 1 and "chat_log.txt is missing" in capsys.readouterr().err
    Path("data/runs/demo-adp/model-a/20260301_101500/chat_log.txt").write_text("restored\n")
    p = Path("data/runs/demo-adp/model-a/20260301_101502/record.json")
    rec = json.loads(p.read_text())
    rec["suite"] = "suite-t1"
    p.write_text(json.dumps(rec))
    assert site.main([]) == 1 and "mixes runs measured under different conditions" in capsys.readouterr().err


def test_build_needs_template_and_data(bench, capsys):
    assert site.main(["--template", "nope.html"]) == 1
    assert site.main(["--data", "nowhere"]) == 1
    assert "Is the data submodule checked out?" in capsys.readouterr().err


def test_disabled_tasks_are_not_on_the_page(bench):
    site.main(["-q"])
    assert [t["id"] for t in _site_data()["tasks"]] == ["demo-adp"]         # demo-heavy is disabled


def test_data_repo_url_only_accepts_github():
    m = site._GITHUB_RE
    assert m.match("https://github.com/someone/rtlscout-bench-data.git").group(1) == "someone/rtlscout-bench-data"
    assert m.match("git@github.com:someone/rtlscout-bench-data.git").group(1) == "someone/rtlscout-bench-data"
    assert m.match("/home/someone/rtlscout-bench-data") is None and m.match("../rtlscout-bench-data.git") is None


# ------------------------------------------------------------------------------------------ the page template

def test_template_contains_no_results():
    assert "window.RTLSCOUT_DATA =" not in TEMPLATE and '<script src="data.js"></script>' in TEMPLATE
    assert not re.search(r"\d\.\d{3} ±", TEMPLATE)                       # no leaderboard numbers baked in
    for name in ("GLM", "Kimi", "Nemotron"):
        assert name not in TEMPLATE                                      # model names come from the data file only


def test_template_has_the_required_states_and_no_dropped_sections():
    assert "measured before ${esc(D.suite)}" in TEMPLATE                 # seed rows are labelled from the suite tag
    assert 'id="empty"' in TEMPLATE and "No results yet" in TEMPLATE and "No data file" in TEMPLATE
    assert 'id="task-wrap" hidden' in TEMPLATE and "D.tasks.length > 1" in TEMPLATE      # selector hidden for one task
    assert "Other studies" not in TEMPLATE and 'id="studies"' not in TEMPLATE
    assert "data-lang" not in TEMPLATE and "lang-spirehdl" not in TEMPLATE               # no language toggle
