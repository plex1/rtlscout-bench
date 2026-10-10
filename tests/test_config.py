from pathlib import Path

import json
import pytest
import yaml

from rtlscout_bench import config
from rtlscout_bench.config import MatrixError, load_matrix, parse_matrix

from conftest import MATRIX, REPO


def _parse(bench, mutate):
    raw = yaml.safe_load(MATRIX)
    mutate(raw)
    return parse_matrix(raw, bench / "campaign_matrix.yaml")


def _problems(bench, mutate) -> str:
    with pytest.raises(MatrixError) as exc:
        _parse(bench, mutate)
    return "\n".join(exc.value.problems)


def test_shipped_matrix_is_the_open_weights_first_campaign(monkeypatch):
    monkeypatch.chdir(REPO)
    m = load_matrix()
    assert m.suite == "suite-v1" and m.open_weights_only is True
    enabled = [e.id for e in m.entries if e.enabled]
    assert enabled[:4] == ["glm-5.2", "kimi-k3", "nemotron-3-ultra", "kimi-k3-high"]     # the first campaign
    models = json.loads((REPO / "data" / "models.json").read_text())["models"]
    assert all(models[m.entry(i).model]["open_weights"] for i in enabled), "every enabled entry is open-weights"
    assert [e.id for e in m.entries if not e.enabled] == ["glm-5.2-spire", "glm-5.2-spire-autoconfig"]
    assert [t.id for t in m.enabled_tasks()] == ["tpu-adp"]
    assert {t.id for t in m.tasks.values() if not t.enabled} == {"jpeg-adp", "qr-adp-fast", "tpu-area-at-500"}
    assert all(e.max_steps == 60 and e.repeats == 3 and e.agent_backend == "react" for e in m.entries)
    assert m.entry("kimi-k3-high").reasoning_effort == "high" and m.entry("kimi-k3").reasoning_effort == "default"
    assert m.entry("glm-5.2-spire-autoconfig").flags == ("--fsm-optimize", "--arith-autoconfig")
    assert m.tasks["tpu-adp"].flags == ("--skip-cec",) and m.tasks["qr-adp-fast"].heavy
    assert len(m.jobs()) == len(enabled) and sum(e.repeats for _, e in m.jobs()) == 3 * len(enabled)


def test_defaults_are_applied_and_overridden(bench):
    m = load_matrix()
    a, high, spire = m.entry("model-a"), m.entry("model-a-high"), m.entry("model-a-spire")
    assert (a.language, a.repeats, a.max_steps, a.reasoning_effort) == ("verilog", 2, 10, "default")
    assert a.tasks == ("demo-adp",)
    assert high.reasoning_effort == "high" and spire.language == "spirehdl" and spire.flags == ("--fsm-optimize",)
    assert [(t.id, e.id) for t, e in m.jobs()] == [("demo-adp", "model-a"), ("demo-adp", "model-a-high")]
    assert m.bench_dir(m.tasks["demo-adp"], "spirehdl").name == "demo_spire"


def test_conditions_carry_task_and_entry_settings(bench):
    m = load_matrix()
    c = m.conditions(m.tasks["demo-adp"], m.entry("model-a-spire"))
    assert c == dict(task="demo-adp", benchmark="demo_spire", language="spirehdl", cost_metric="area_delay_product",
                     target_delay=None, technology="asap7", entry="model-a-spire", model="openrouter:example/model-a",
                     reasoning_effort="default", agent_backend="react", max_steps=10,
                     flags=["--skip-cec", "--fsm-optimize"], env={})
    assert set(c) == set(config.CONDITION_FIELDS)


@pytest.mark.parametrize("mutate, expected", [
    (lambda r: r["entries"].append({"id": "model-a", "model": "openrouter:example/x"}), "duplicate id"),
    (lambda r: r["entries"][0].update(flags=["--skip-netlist-sim"]), "scoring flag and belongs to the task"),
    (lambda r: r["entries"][0].update(flags=["--cost-metric=area"]), "set by the tooling"),
    (lambda r: r["tasks"]["demo-adp"].update(flags=["--max-steps"]), "set by the tooling"),
    (lambda r: r["entries"][0].update(reasoning_effort="extreme"), "reasoning_effort must be one of"),
    (lambda r: r["entries"][0].update(agent_backend="opencode"), "agent_backend must be 'react'"),
    (lambda r: r["entries"][0].update(tasks=["nope"]), "unknown task 'nope'"),
    (lambda r: r["entries"][0].update(model="model-without-provider"), "'<provider>:<model>'"),
    (lambda r: r["entries"][0].update(repeats=0), "repeats must be a positive integer"),
    (lambda r: r["entries"][0].update(colour="blue"), "unknown key 'colour'"),
    (lambda r: r["entries"][0].update(id="Has Spaces"), "lower-case letters"),
    (lambda r: r["entries"][0].update(language="vhdl"), "language must be one of"),
    (lambda r: r["tasks"]["demo-adp"].pop("cost_metric"), "tasks.demo-adp.cost_metric: required"),
    (lambda r: r["tasks"]["demo-adp"].update(target_delay="fast"), "target_delay must be a number"),
    (lambda r: r.pop("suite"), "suite: required"),
    (lambda r: r["policy"].update(open_weights_only="yes"), "must be true or false"),
    (lambda r: r.update(leaderboard={}), "unknown key 'leaderboard'"),
])
def test_invalid_matrices_are_rejected_with_a_reason(bench, mutate, expected):
    assert expected in _problems(bench, mutate)


def test_all_problems_are_reported_together(bench):
    def mutate(r):
        r["entries"][0].update(reasoning_effort="extreme", repeats=-1)
        r["entries"][1].update(tasks=["nope"])
    text = _problems(bench, mutate)
    assert "reasoning_effort" in text and "repeats" in text and "unknown task" in text


def test_enabled_job_needs_its_benchmark_directory(bench):
    text = _problems(bench, lambda r: r["tasks"]["demo-adp"].update(benchmark="absent"))
    assert ("task demo-adp: benchmark directory benchmarks/absent_verilog (verilog entries) is missing or incomplete"
            in text)
    assert text.count("absent_verilog") == 1                # once per directory, not once per entry
    # a disabled example task may point at a benchmark that is not in the suite
    later = {"benchmark": "absent", "cost_metric": "area", "enabled": False}
    m = _parse(bench, lambda r: r["tasks"].update({"later": later}))
    assert not m.tasks["later"].enabled


def test_entry_flags_replace_default_flags(bench):
    m = _parse(bench, lambda r: (r["defaults"].update(flags=["--dont-touch-main-arith"]),
                                 r["entries"][1].update(flags=["--abc-optimize"])))
    assert m.entry("model-a").flags == ("--dont-touch-main-arith",)
    assert m.entry("model-a-high").flags == ("--abc-optimize",)


def test_cli_prints_resolved_matrix_and_fails_on_invalid(bench, capsys):
    assert config.main([]) == 0
    out = capsys.readouterr().out
    assert "suite:  suite-t1" in out and "demo-adp / model-a-high" in out
    assert "OK: 1 enabled task(s), 2 enabled entries, 4 runs for a full campaign" in out
    Path("campaign_matrix.yaml").write_text(MATRIX.replace("reasoning_effort: high", "reasoning_effort: maximal"))
    assert config.main([]) == 2
    assert "reasoning_effort must be one of" in capsys.readouterr().err
    assert config.main(["--matrix", "missing.yaml"]) == 2
