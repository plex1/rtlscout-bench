# RTL Scout leaderboard: user manual

Updating the leaderboard is two commands, run from the root of an `rtlscout-bench` checkout inside the rtlscout
container:

```bash
python -m rtlscout_bench.campaign      # runs whatever the matrix still needs
python -m rtlscout_bench.publish       # records, shows the table diff, asks, pushes
```

The page then rebuilds and deploys by itself. Adding a model is one new block in `campaign_matrix.yaml` before the
first command.

Contents

1. [Quick start](#1-quick-start)
2. [Setup](#2-setup)
3. [Adding a task, adding a model](#3-adding-a-task-adding-a-model)
4. [Running a campaign](#4-running-a-campaign)
5. [Publishing](#5-publishing)
6. [Looking at results locally](#6-looking-at-results-locally)
7. [How a score is computed](#7-how-a-score-is-computed)
8. [The data repository](#8-the-data-repository)
9. [Adding a benchmark to the suite](#9-adding-a-benchmark-to-the-suite)
10. [Upgrading rtlscout](#10-upgrading-rtlscout)
11. [Submitting results](#11-submitting-results)
12. [Troubleshooting](#12-troubleshooting)

Conventions. Every command is run from the root of the checkout. Output shown in this manual is what the command
printed when the manual was written; where your numbers will differ, the text says what to look for. Commands
that write to `data/` change the data submodule's working tree; nothing is public until it is pushed.

---

## 1. Quick start

You have a checkout with the data submodule and a running rtlscout container (see [Setup](#2-setup)).

**First command: run what is missing.**

<!-- TO-VERIFY -->
```bash
python -m rtlscout_bench.campaign
```

It reads `campaign_matrix.yaml`, counts the runs each enabled entry already has for the current suite, measures
the starting point of each task once, and launches the missing runs, two at a time. Each finished run prints a
`[done]` line; the last line names the campaign file under `runs/campaigns/`. Nothing is written to `data/` and
nothing is public yet. To see what it would do without starting anything:

```bash
# doctest
python -m rtlscout_bench.campaign --dry-run
```
<!-- doctest-expect: · parallel 2 -->

```text
suite suite-v1 · rtlscout not installed · spire-hdl not installed · parallel 2
  tpu-adp / glm-5.2                        0 of 3 runs done, 3 to launch
  tpu-adp / kimi-k3                        0 of 3 runs done, 3 to launch
  tpu-adp / nemotron-3-ultra               0 of 3 runs done, 3 to launch
  tpu-adp / kimi-k3-high                   0 of 3 runs done, 3 to launch
[baseline] tpu-adp/verilog: python -m rtlscout.run_eval benchmarks/tpu_verilog/context/starting_point.v --benchmark benchmarks/tpu_verilog --cost-metric area_delay_product --skip-cec --json
[run] tpu-adp/glm-5.2 r0: python -m rtlscout.run_benchmark --benchmark tpu_verilog --language verilog --model openrouter:z-ai/glm-5.2 --max-steps 60 --benchmarks-root benchmarks --runs-dir runs/tpu-adp/glm-5.2 --cost-metric area_delay_product --skip-cec
[run] tpu-adp/kimi-k3 r0: python -m rtlscout.run_benchmark --benchmark tpu_verilog --language verilog --model openrouter:moonshotai/kimi-k3 --max-steps 60 --benchmarks-root benchmarks --runs-dir runs/tpu-adp/kimi-k3 --cost-metric area_delay_product --skip-cec
... (10 more [run] lines: every entry three times, the kimi-k3-high ones prefixed with RTLSCOUT_REASONING_EFFORT=high)
dry run: 1 baseline(s) and 12 run(s) would be launched, nothing was started
```

(The first line reports the installed rtlscout and spire-hdl versions; this output was taken outside the
container, where they are not installed. A dry run works there; a real launch does not.)

**Second command: record, look, publish.**

<!-- TO-VERIFY -->
```bash
python -m rtlscout_bench.publish
```

It turns the finished runs into records in `data/`, checks the data, commits and pushes the data repository,
prints the leaderboard as published and as it would become, and asks whether to publish (`[y/N]`). On `y` it
commits the new `data` pointer in `rtlscout-bench` and pushes; the Pages workflow then rebuilds and deploys the
site. On anything else the page stays as it is. `--dry-run` shows the same tables and changes nothing; an example is in
[Publishing](#5-publishing).

**How long it takes and what it costs.** A full campaign of the shipped matrix is 12 runs of 60 agent steps. As
a reference, the twelve seed runs that are on the page took between 39 and 115 minutes each (75 on average, 15
run-hours in total) and cost $59.90 in total at list prices: $8.11 for the three GLM 5.2 runs, $23.54 and $22.18
for the two Kimi K3 trios, $6.07 for Nemotron 3 Ultra. Those ran six at a time against a shorter vector file
than `suite-v1` ships, so expect evaluations, and therefore runs, to take longer; the token cost depends on the
model's behaviour, not on the vector file. Measuring a starting point costs no API money.

---

## 2. Setup

**Prerequisites**

- Docker, and the rtlscout image by its registry name, `ghcr.io/huawei-csl/rtlscout:slim` (about 3 GB). It holds
  the EDA tools (Yosys, OpenROAD, OpenSTA, Verilator, sv2v) and a Python environment.
- An `.env` file in the checkout root with the provider keys your entries need, for the shipped matrix
  `OPENROUTER_API_KEY=...`. It is listed in `.gitignore`; never commit it.
- Push access to both repositories if you are going to publish.

**Clone with the data submodule, start the container, install**

<!-- TO-VERIFY -->
```bash
docker pull ghcr.io/huawei-csl/rtlscout:slim
git clone --recurse-submodules https://github.com/plex1/rtlscout-bench.git && cd rtlscout-bench
docker run --rm -it -v "$PWD":/work -w /work --env-file .env ghcr.io/huawei-csl/rtlscout:slim bash
```

Inside the container:

```bash
. /home/vscode/pyenv_eda/bin/activate
uv pip install -e .
python -c "import importlib.metadata as m; print('rtlscout', m.version('rtlscout'))"
```

```text
rtlscout 0.2.0
```

`uv pip install -e .` installs this repository's tooling and pulls `rtlscout` by git URL at the commit pinned in
`pyproject.toml`; the last line must print that version. The image's Python environment is a `uv` environment,
which is why the command is `uv pip` and not `pip`.

**Smoke test, no API key, no cost.** rtlscout ships a scripted `fake:` model. First check the engine alone:

```bash
# doctest: rtlscout
python -m rtlscout.run_benchmark --benchmark simple_adder --model fake:simple_adder_pass --runs-dir /tmp/rtlscout-smoke-runs
```
<!-- doctest-expect: Best: PASS -->

```text
Best design saved: /tmp/rtlscout-smoke-runs/simple_adder/simple_adder_pass/20261006_064516/best_design
Results stored in: /tmp/rtlscout-smoke-runs/simple_adder/simple_adder_pass/20261006_064516

Best: PASS | 308 transistors (step 1)
```

The run must end with a line starting `Best: PASS`. Then run this repository's whole pipeline (campaign, record,
check, site) on the same fake model, using the smoke matrix in `tests/smoke/` and a scratch directory, so the real
`data/` is not touched:

```bash
# doctest: rtlscout
rm -rf /tmp/rtlscout-bench-smoke && mkdir -p /tmp/rtlscout-bench-smoke && cp -r tests/smoke/data /tmp/rtlscout-bench-smoke/data
python -m rtlscout_bench.campaign --matrix tests/smoke/campaign_matrix.yaml --data /tmp/rtlscout-bench-smoke/data --runs-dir /tmp/rtlscout-bench-smoke/runs --name smoke
python -m rtlscout_bench.record --matrix tests/smoke/campaign_matrix.yaml --data /tmp/rtlscout-bench-smoke/data /tmp/rtlscout-bench-smoke/runs
python -m rtlscout_bench.record --matrix tests/smoke/campaign_matrix.yaml --data /tmp/rtlscout-bench-smoke/data --check
python -m rtlscout_bench.site --matrix tests/smoke/campaign_matrix.yaml --data /tmp/rtlscout-bench-smoke/data --out /tmp/rtlscout-bench-smoke/_site
```
<!-- doctest-expect: campaign smoke: 1 of 1 runs completed, 1 baseline(s) measured -->
<!-- doctest-expect: recorded 1, already recorded 0, skipped 0, refused 0 -->
<!-- doctest-expect: : OK -->
<!-- doctest-expect: 1 task(s), 1 row(s), 1 run(s) -->

```text
suite smoke · rtlscout 0.2.0+git.f4152b6 · spire-hdl 0.4.0 · parallel 2
  adder-transistors / fake-adder           0 of 1 runs done, 1 to launch
[start] baseline adder-transistors/verilog
[start] adder-transistors/fake-adder r0
[baseline] adder-transistors/verilog: ok  cost 308.00
[done] adder-transistors/fake-adder r0: completed  best 308.00  run 20261006_064929  (0 min)
campaign smoke: 1 of 1 runs completed, 1 baseline(s) measured -> /tmp/rtlscout-bench-smoke/runs/campaigns/smoke.json
campaign /tmp/rtlscout-bench-smoke/runs/campaigns/smoke.json
  recorded adder-transistors/fake-adder/20261006_064929  best 308.00
recorded 1, already recorded 0, skipped 0, refused 0
data check: 1 records (1 selected, 0 excluded), 1 leaderboard file(s), 1 models, open-weights policy off: OK
adder-transistors: adder-transistors  [cost; lower is better]
starting point (Verilog): 308.00 = 308.000
#  Entry                n  mean ± sd  best run  vs. start  runtime (min)  $/run  suite
-  -------------------  -  ---------  --------  ---------  -------------  -----  -----
1  Scripted fake model  1    308.000   308.000      +0.0%              0    0.0  smoke

wrote /tmp/rtlscout-bench-smoke/_site/index.html and /tmp/rtlscout-bench-smoke/_site/data.js (1 KB): 1 task(s), 1 row(s), 1 run(s)
```

Look for `campaign smoke: 1 of 1 runs completed, 1 baseline(s) measured`, `recorded 1`, a `data check: ... OK`
line, and `wrote /tmp/rtlscout-bench-smoke/_site/index.html`.

**Without the container.** Everything that only reads files (validating the matrix, the data checks, tables,
building the site, a publish dry run, the tests) needs just Python 3.12 or later and `pyyaml`:

```bash
pip install pyyaml pytest
pip install --no-deps -e .
pytest -q
```

```text
..........................................ss............................ [ 61%]
.............................................                            [100%]
115 passed, 2 skipped in 5.46s
```

The two skipped tests are the smoke commands above, which need rtlscout and the EDA tools; inside the container
nothing is skipped.

---

## 3. Adding a task, adding a model

Both are edits of `campaign_matrix.yaml`, the one file that configures campaigns. After any edit, validate it:

```bash
# doctest
python -m rtlscout_bench.config
```
<!-- doctest-expect: jobs (enabled task x enabled entry) -->
<!-- doctest-expect: enabled task(s) -->

```text
matrix: campaign_matrix.yaml
suite:  suite-v1
policy: open_weights_only = true

tasks
  tpu-adp            enabled   benchmark tpu           metric area_delay_product       --skip-cec
  jpeg-adp           disabled  benchmark jpeg_idct_2d  metric area_delay_product       --skip-cec
  qr-adp-fast        disabled  benchmark qr            metric area_delay_product_fast  --skip-cec RTLSCOUT_ADP_FAST_TIMEOUT=3600 heavy
  tpu-area-at-500    disabled  benchmark tpu           metric area                     target_delay=500 --skip-cec

entries
  glm-5.2                    enabled   openrouter:z-ai/glm-5.2                        verilog, 60 steps, effort default, x3  -> tpu-adp
  kimi-k3                    enabled   openrouter:moonshotai/kimi-k3                  verilog, 60 steps, effort default, x3  -> tpu-adp
  nemotron-3-ultra           enabled   openrouter:nvidia/nemotron-3-ultra-550b-a55b   verilog, 60 steps, effort default, x3  -> tpu-adp
  kimi-k3-high               enabled   openrouter:moonshotai/kimi-k3                  verilog, 60 steps, effort high, x3  -> tpu-adp
  glm-5.2-spire              disabled  openrouter:z-ai/glm-5.2                        spirehdl, 60 steps, effort default, x3, --fsm-optimize  -> tpu-adp
  glm-5.2-spire-autoconfig   disabled  openrouter:z-ai/glm-5.2                        spirehdl, 60 steps, effort default, x3, --fsm-optimize --arith-autoconfig  -> tpu-adp

jobs (enabled task x enabled entry)
  tpu-adp / glm-5.2                        benchmarks/tpu_verilog       3 runs
  tpu-adp / kimi-k3                        benchmarks/tpu_verilog       3 runs
  tpu-adp / nemotron-3-ultra               benchmarks/tpu_verilog       3 runs
  tpu-adp / kimi-k3-high                   benchmarks/tpu_verilog       3 runs

OK: 1 enabled task(s), 4 enabled entries, 12 runs for a full campaign
```

A mistake is reported with every problem at once and exit status 2. This is what a misspelt reasoning effort and
a scoring flag on an entry look like (taken from a scratch matrix with invented entries):

```text
campaign_matrix.yaml: invalid matrix
  - entry model-a-high: reasoning_effort must be one of default, low, medium, high
  - entry model-a-spire: --skip-netlist-sim is a scoring flag and belongs to the task, not to an entry (a different objective is a new task)
```

### Adding a task

A task is *what is scored*: a benchmark plus its cost metric and the conditions that belong to that metric.

```yaml
tasks:
  tpu-area-at-500:                     # the task id: a directory level in the data repo, and the page's table
    benchmark: tpu                     # a pair in benchmarks/: tpu_verilog and tpu_spire
    cost_metric: area                  # rtlscout --cost-metric
    target_delay: 500                  # rtlscout --target-delay, in ps (optional)
    flags: [--skip-cec]                # scoring flags: --skip-cec, --skip-netlist-sim (optional)
    env: {RTLSCOUT_PPA_TIMEOUT: "2400"}    # environment of every run and baseline of this task (optional)
    heavy: false                       # true: the scheduler never runs two heavy tasks at once (optional)
    enabled: true                      # false keeps the task in the file without running or showing it
```

| field | meaning |
|---|---|
| `benchmark` | name of a benchmark pair. The directory per language is found by suffix: `<name>_verilog`, `<name>_spire` |
| `cost_metric` | any metric rtlscout registers, e.g. `area_delay_product`, `area_delay_product_fast`, `area`, `delay`, `transistors` |
| `target_delay`, `technology`, `energy_exp` | passed to rtlscout as `--target-delay`, `--technology`, `--energy-exp` when set |
| `flags` | scoring flags, passed to every run and to the baseline evaluation |
| `env` | environment variables for the runs of this task, typically evaluation timeouts ([Troubleshooting](#12-troubleshooting)) |
| `heavy` | scheduling hint for designs whose evaluations take tens of minutes |
| `enabled` | default `true` |

**Scores are never compared across tasks.** Units differ, and a fast metric uses a different flow than the full
one. Every task gets its own table, and the page shows a task selector once more than one task is enabled. For
the same reason an entry cannot override a task's metric, target delay or scoring flags: a different objective
is a new task, so all rows of one table are comparable by construction.

The first time a run of a new task is recorded, `data/leaderboards/<task>.json` is created with placeholder
texts. Fill in `title`, `description`, `metric_label`, `unit` and `flow` there; the page shows them
([The data repository](#8-the-data-repository)).

### Adding a model

An entry is *who is scored*: a model under one fixed set of agent conditions. One entry is one leaderboard row.

```yaml
entries:
  - id: kimi-k3-high                   # row id: a directory level in the data repo. Never reuse an id for
                                       # different conditions
    model: openrouter:moonshotai/kimi-k3   # <provider>:<model>, exactly as rtlscout's --model takes it
    reasoning_effort: high             # the rest is optional and falls back to the defaults block
```

| field | default (shipped `defaults`) | meaning |
|---|---|---|
| `id` | required | lower-case letters, digits, `.`, `_`, `-`. Written into every record |
| `model` | required | `<provider>:<model>`. Any spec rtlscout accepts works; the provider's key must be in `.env` |
| `language` | `verilog` | `verilog` or `spirehdl`. Selects the benchmark directory of the pair |
| `repeats` | `3` | runs the entry should have per task |
| `max_steps` | `60` | agent step budget, `--max-steps` |
| `reasoning_effort` | `default` | `default` leaves the provider's default; `low`, `medium`, `high` set `RTLSCOUT_REASONING_EFFORT` |
| `agent_backend` | `react` | rtlscout's built-in loop; the only value accepted for now |
| `flags` | `[]` | agent-side rtlscout flags, e.g. `--fsm-optimize`; appended to the task's flags. An entry's own list replaces the default list |
| `tasks` | `[tpu-adp]` | the tasks this entry runs |
| `enabled` | `true` | `false` keeps the entry in the file without running it |

**The same model twice.** Two rows of one model are two entries with different ids, as `kimi-k3` and
`kimi-k3-high` above. Their runs live in different directories and never mix. The page derives the row label
from the model's display name plus what distinguishes the row ("Kimi K3 · high effort", "GLM 5.2 · Spire HDL").

**The model also needs a row in `data/models.json`**: display name, vendor, `open_weights`, and list prices in
USD per million tokens. Without prices the run is still recorded but has no dollar figure.

```json
"openrouter:moonshotai/kimi-k3": {
 "name": "Kimi K3", "vendor": "Moonshot AI", "open_weights": true,
 "price_per_mtok": {"input": 3.0, "output": 15.0}, "price_note": "via OpenRouter, no prompt caching"
}
```

**The open-weights policy.** `policy: {open_weights_only: true}` at the top of the matrix restricts the
leaderboard to models marked `"open_weights": true` in `data/models.json`. While it is on:

- `campaign` refuses to launch a model that is not marked, before any money is spent;
- `record` refuses to write a record for one;
- `record --check`, `site` and `publish` fail if the data contains or selects one.

A model that is missing from `models.json` counts as not marked. This is the refusal, and next to it the other
thing `record` refuses, a transcript that contains an API-key pattern (`sk-ant-`, `sk-or-`, `API_KEY=`). Both
were produced in a scratch checkout with invented model names and synthetic run directories:

```text
$ python -m rtlscout_bench.record raw/demo_verilog/example_model-c-closed/20260301_101500 --task demo-adp --entry model-c --origin manual --rtlscout 0.2.0
recording 1 run(s) as demo-adp/model-c (origin manual, suite suite-t1)
  REFUSED  demo-adp/model-c/20260301_101500: raw/demo_verilog/example_model-c-closed/20260301_101500: model openrouter:example/model-c-closed is not marked "open_weights": true in data/models.json and policy.open_weights_only is on; nothing was written
recorded 0, already recorded 0, skipped 0, refused 1
exit status 1

$ python -m rtlscout_bench.record raw/demo_verilog/example_model-a/20260301_101502 --task demo-adp --entry model-a --origin manual --rtlscout 0.2.0
recording 1 run(s) as demo-adp/model-a (origin manual, suite suite-t1)
  REFUSED  demo-adp/model-a/20260301_101502: raw/demo_verilog/example_model-a/20260301_101502/chat_log.txt: contains an API-key pattern at line 6 (sk-or-), line 6 (API_KEY=); refusing to copy this run. Transcripts are published verbatim: remove the run or rotate the key.
recorded 0, already recorded 0, skipped 0, refused 1
exit status 1
```

The report names the file, the line and the pattern, never the matching text.

---

## 4. Running a campaign

A campaign measures the starting point of each task and launches the runs the matrix still needs.

```bash
python -m rtlscout_bench.campaign --baselines-only     # starting points only: a few minutes, no API cost
```

```text
suite suite-v1 · rtlscout 0.2.0+git.f4152b6 · spire-hdl 0.4.0 · parallel 2
  tpu-adp / glm-5.2                        0 of 3 runs done, 3 to launch
  tpu-adp / kimi-k3                        0 of 3 runs done, 3 to launch
  tpu-adp / nemotron-3-ultra               0 of 3 runs done, 3 to launch
  tpu-adp / kimi-k3-high                   0 of 3 runs done, 3 to launch
[start] baseline tpu-adp/verilog
[baseline] tpu-adp/verilog: ok  cost 4,624,408.88
campaign 20261006_064936: 0 of 0 runs completed, 1 baseline(s) measured -> runs/campaigns/20261006_064936.json
```

<!-- TO-VERIFY -->
```bash
python -m rtlscout_bench.campaign --parallel 2         # everything that is missing
```

**What "missing" means.** For every enabled (task, entry) the runner counts the runs that are finished, belong
to the current `suite`, were made under exactly the entry's current conditions, and are not excluded: those
already recorded in `data/`, plus finished runs of earlier invocations still waiting under `runs/`. It launches
`repeats` minus that count. Seed runs carry another suite tag and never count. So:

- **Resuming after an interruption** is running the same command again. Finished runs are kept; a run that was
  cut off is launched again. Ctrl-C stops the running processes and keeps what finished.
- **One entry only**, or **more runs than `repeats`**: `--entry <id>` restricts the campaign to that entry (and
  launches it even if it is disabled in the matrix); `-n <N>` launches exactly N more runs instead of topping up.

```bash
# doctest
python -m rtlscout_bench.campaign --entry glm-5.2 -n 1 --dry-run
```
<!-- doctest-expect: dry run: -->

```text
suite suite-v1 · rtlscout not installed · spire-hdl not installed · parallel 2
  tpu-adp / glm-5.2                        0 of 3 runs done, 1 to launch
[baseline] tpu-adp/verilog: python -m rtlscout.run_eval benchmarks/tpu_verilog/context/starting_point.v --benchmark benchmarks/tpu_verilog --cost-metric area_delay_product --skip-cec --json
[run] tpu-adp/glm-5.2 r0: python -m rtlscout.run_benchmark --benchmark tpu_verilog --language verilog --model openrouter:z-ai/glm-5.2 --max-steps 60 --benchmarks-root benchmarks --runs-dir runs/tpu-adp/glm-5.2 --cost-metric area_delay_product --skip-cec
dry run: 1 baseline(s) and 1 run(s) would be launched, nothing was started
```

**Baselines.** The starting point of a (task, language) is measured with `python -m rtlscout.run_eval` on the
benchmark's shipped `context/starting_point.*`, with the task's metric and scoring flags. It is measured once
per suite and rtlscout version and reused after that; `--baselines-only` measures it again on purpose. A run's
own first evaluation is never used as the baseline.

**Parallelism.** `--parallel` defaults to 2. Two to three concurrent runs is the limit for one machine: every
evaluation runs synthesis, timing analysis and a gate-level simulation. Tasks marked `heavy` never overlap,
whatever `--parallel` says. Runs are launched two seconds apart and jobs are interleaved across entries (first
one run of every entry, then the second of every entry), so concurrent runs usually talk to different providers.
Run one campaign at a time per checkout.

**Where raw output goes.** Everything a campaign writes is under `runs/`, which is scratch and not in git. A raw
run directory of the seed runs is 4 to 9 MB:

```text
runs/<task>/<entry>/<benchmark>/<model>/<run-id>/    the run directory rtlscout writes
runs/logs/<campaign>/<task>__<entry>__r<k>.log       stdout and stderr of each run; baseline__<task>__<language>.log
runs/campaigns/<campaign>.json                       conditions, versions, baselines and the run list
```

The campaign name defaults to its start time; `--name` sets it. `runs/` can be deleted once its runs are
recorded: the records keep everything the page and the tables need.

**Watching progress.** The campaign prints `[start]` and `[done]` lines. For one run in detail, follow its log;
for the whole campaign, print its status table (it re-reads the run directories):

```bash
tail -f runs/logs/<campaign>/tpu-adp__glm-5.2__r0.log
python -m rtlscout_bench.tables --campaign runs/campaigns/<campaign>.json
```

For the smoke campaign of [Setup](#2-setup) (`--campaign /tmp/rtlscout-bench-smoke/runs/campaigns/smoke.json`) the
status table is:

```text
# Campaign smoke · smoke · rtlscout 0.2.0+git.f4152b6

| Task | Entry | n | Start | Best (median) | vs. start |
|---|---|---|---|---|---|
| `adder-transistors` | `fake-adder` | 1 | 308 | 308 (308) | +0.0% |

## Runs

| Task | Entry | Rep | Run | Status | Best (eval) | Evals |
|---|---|---|---|---|---|---|
| `adder-transistors` | `fake-adder` | 0 | `20261006_064929` | completed | 308 (eval 1) | 1 |
```

**What a failed run looks like.** Each run ends in one of three states, shown in its `[done]` line, in the
campaign file and in the status table:

| status | meaning | what happens next |
|---|---|---|
| `completed` | rtlscout wrote `result.json` and reported no error | counts towards `repeats`; `record` selects it |
| `ended-early` | a run directory exists, but the process crashed or was killed before writing `result.json`, or rtlscout reported an error for the run | does not count, so the next campaign launches a replacement. `record` skips it if there is no `result.json`; if there is one, the run is recorded and listed under `exclude` with the error as the reason |
| `launch-failed` | rtlscout exited before creating a run directory, e.g. an invalid model spec or a benchmark that was not found | does not count. Read the log named in the `not completed:` line at the end of the campaign |

A campaign with any run or baseline that is not `completed`/`ok` exits with status 1 and lists them. A single
API error inside a run does not end it: rtlscout spends that step and continues.

---

## 5. Publishing

`publish` wraps recording, committing the data and bumping the pointer. Look first, with a dry run:

```bash
python -m rtlscout_bench.publish --dry-run
```

This output was taken when the twelve seed runs were recorded in `data/` but not yet committed:

```text
1. recording new runs from runs/ (dry run: into a temporary copy of the data)
   recorded 0, already recorded 0, skipped 0, refused 0 (no campaign files)
2. data check: 12 records (12 selected, 0 excluded): OK
3. dry run: data/ not committed (12 new run record(s) would be)

4. leaderboard as published (data @ 323c78a)
tpu-adp: logikbench tpu  [area × delay, 10^6 µm²·ps; lower is better]
starting point (Verilog): 4,624,408.88 = 4.624
(no runs selected)

   leaderboard after this publish
tpu-adp: logikbench tpu  [area × delay, 10^6 µm²·ps; lower is better]
starting point (Verilog): 4,624,408.88 = 4.624
#  Entry                  n      mean ± sd  best run  vs. start  runtime (min)      $/run  suite
-  ---------------------  -  -------------  --------  ---------  -------------  ---------  ------
1  GLM 5.2                3  2.760 ± 0.018     2.744     -40.3%        83 ± 18  2.7 ± 0.3  pre-v1
2  Kimi K3                3  2.908 ± 0.314     2.609     -37.1%        81 ± 31  7.8 ± 1.4  pre-v1
3  Kimi K3 · high effort  3  3.424 ± 0.874     2.721     -26.0%        58 ± 18  7.4 ± 1.3  pre-v1
4  Nemotron 3 Ultra       3  4.646 ± 0.116     4.574      +0.5%         78 ± 3  2.0 ± 0.1  pre-v1

   changes
   tpu-adp: + row added    GLM 5.2 [glm-5.2]: n=3, mean 2.760 ± 0.018 (pre-v1)
   tpu-adp: + row added    Kimi K3 [kimi-k3]: n=3, mean 2.908 ± 0.314 (pre-v1)
   tpu-adp: + row added    Kimi K3 · high effort [kimi-k3-high]: n=3, mean 3.424 ± 0.874 (pre-v1)
   tpu-adp: + row added    Nemotron 3 Ultra [nemotron-3-ultra]: n=3, mean 4.646 ± 0.116 (pre-v1)

dry run: nothing was written to data/, committed or pushed
```

**What `publish` does, step by step**

| step | action | changes the page? |
|---|---|---|
| 1 | records every finished run of the campaigns under `runs/` into `data/`, exactly as `python -m rtlscout_bench.record` | no |
| 2 | validates `data/`, exactly as `record --check`; stops here on any error, with nothing committed | no |
| 3 | commits `data/` and pushes it to the data repository | no |
| 4 | prints the leaderboard "as published" and "after this publish", and the changes | no |
| 5 | asks `Publish? ... [y/N]` | no |
| 6 | commits the new `data` pointer in `rtlscout-bench` (`publish: data @ <commit>`) and pushes | **yes**: the Pages workflow redeploys |

"As published" is the data commit that the last commit of `rtlscout-bench` points at: exactly what the deployed
page was built from. A dry run does steps 1, 2 and 4 on a temporary copy of `data/`.

**Reading the changes**

| line | meaning |
|---|---|
| `+ row added` | an entry appears on the page for the first time |
| `~ row changed` | an entry's set of runs changed; old and new n, mean and sd, and how many runs came and went |
| `- row removed` | an entry no longer has any selected run |
| `! run excluded` | a run is in the data but does not count, with the reason |
| `~ starting point` | the baseline of a task changed. Every "vs. start" of that task moves with it; a change you did not expect means the evaluation flow changed |

When reruns of an entry are recorded, that entry's seed runs are superseded automatically: the row switches from
the seed runs to the new ones in one step and appears here as `~ row changed ... (pre-v1) -> ... (suite-v1)`.

**Why step 5 exists.** It is the moment numbers become public, and a campaign can produce a bad result: a run
cut short by a provider outage, a crashed run, a model that edited the design before measuring the starting
point. Answering no leaves the data committed and the page unchanged.

**Excluding a bad run, and recording why.** A run is never deleted to improve a row; it is taken out of the
selection with a reason, which is shown on the page under "Runs not counted":

```bash
python -m rtlscout_bench.record --exclude tpu-adp/nemotron-3-ultra/20260727_104159 --reason "edited the design before measuring the starting point"
python -m rtlscout_bench.tables
python -m rtlscout_bench.record --include tpu-adp/nemotron-3-ultra/20260727_104159
```

```text
excluded tpu-adp/nemotron-3-ultra/20260727_104159: edited the design before measuring the starting point
updated data/leaderboards/tpu-adp.json; commit it in the data repo to make it count
tpu-adp: logikbench tpu  [area × delay, 10^6 µm²·ps; lower is better]
starting point (Verilog): 4,624,408.88 = 4.624
#  Entry                  n      mean ± sd  best run  vs. start  runtime (min)      $/run  suite
-  ---------------------  -  -------------  --------  ---------  -------------  ---------  ------
1  GLM 5.2                3  2.760 ± 0.018     2.744     -40.3%        83 ± 18  2.7 ± 0.3  pre-v1
2  Kimi K3                3  2.908 ± 0.314     2.609     -37.1%        81 ± 31  7.8 ± 1.4  pre-v1
3  Kimi K3 · high effort  3  3.424 ± 0.874     2.721     -26.0%        58 ± 18  7.4 ± 1.3  pre-v1
4  Nemotron 3 Ultra       2  4.579 ± 0.007     4.574      -1.0%         79 ± 0  2.0 ± 0.1  pre-v1
excluded: 1 run(s)
  nemotron-3-ultra/20260727_104159: edited the design before measuring the starting point
included tpu-adp/nemotron-3-ultra/20260727_104159
updated data/leaderboards/tpu-adp.json; commit it in the data repo to make it count
```

(The three commands were run one after the other on the seed data to show the mechanism; the third undoes the
first, and the published leaderboard counts that run.) `--exclude` without `--reason` is an error. After an
exclusion, run `publish` again: it commits the changed leaderboard file and shows the new table. An excluded
run no longer counts towards the entry's `repeats`, so the next campaign launches a replacement.

**Without pushing.** `--no-push` makes both commits locally and pushes neither repository; push the data
repository first, then `rtlscout-bench`, when you are ready. `-m "<message>"` sets the data commit message
(default: derived from the new runs, e.g. `tpu-adp: glm-5.2 +3`); `--yes` skips the question.

**Undoing a publish.** The page follows the `data` pointer of `rtlscout-bench` `main`. Revert the pointer bump
and push; the page redeploys from the previous data commit. The data commit itself stays in the data repository.

<!-- TO-VERIFY -->
```bash
git log --oneline -3                   # find the commit "publish: data @ <commit>"
git revert <that commit> && git push
git submodule update                   # move the local data/ checkout back to the pointer
```

---

## 6. Looking at results locally

**The page.** Build it and open it in a browser. It is a static folder, one HTML page plus one generated data
file, and works from `file://`:

```bash
# doctest
python -m rtlscout_bench.site
```
<!-- doctest-expect: wrote _site/index.html and _site/data.js -->

```text
tpu-adp: logikbench tpu  [area × delay, 10^6 µm²·ps; lower is better]
starting point (Verilog): 4,624,408.88 = 4.624
#  Entry                  n      mean ± sd  best run  vs. start  runtime (min)      $/run  suite
-  ---------------------  -  -------------  --------  ---------  -------------  ---------  ------
1  GLM 5.2                3  2.760 ± 0.018     2.744     -40.3%        83 ± 18  2.7 ± 0.3  pre-v1
2  Kimi K3                3  2.908 ± 0.314     2.609     -37.1%        81 ± 31  7.8 ± 1.4  pre-v1
3  Kimi K3 · high effort  3  3.424 ± 0.874     2.721     -26.0%        58 ± 18  7.4 ± 1.3  pre-v1
4  Nemotron 3 Ultra       3  4.646 ± 0.116     4.574      +0.5%         78 ± 3  2.0 ± 0.1  pre-v1

wrote _site/index.html and _site/data.js (25 KB): 1 task(s), 4 row(s), 12 run(s)
```

Then open `_site/index.html`. The build reads `data/` as it is in your working tree, so it previews recorded but
unpublished runs. `_site/` is not committed anywhere; CI builds the same thing from the published commits.
`--out <dir>` writes elsewhere. The build fails, and writes nothing, if a selected run fails the data check or
one row would mix runs measured under different conditions.

**Tables.** The same numbers as text, markdown or LaTeX:

```bash
# doctest
python -m rtlscout_bench.tables
python -m rtlscout_bench.tables --format md
python -m rtlscout_bench.tables --format tex
python -m rtlscout_bench.tables --runs
python -m rtlscout_bench.tables --head-to-head
```
<!-- doctest-expect: lower is better -->
<!-- doctest-expect: | # | Entry | n | -->
<!-- doctest-expect: \begin{tabular} -->
<!-- doctest-expect: | Entry | Run | best -->

The first prints the table shown above. `--format md`:

```text
### logikbench tpu (`tpu-adp`)

Task `tpu-adp`: logikbench tpu. Starting point: Verilog 4.624 (10^6 µm²·ps). Numbers: best area × delay reached per run, mean ± sample sd over n independent runs; `best run` is the single best run, `vs. start` the mean relative to the unmodified starting point, runtime the wall-clock per run, $/run the API cost from recorded token usage at list prices.

| # | Entry | n | area × delay, mean ± sd (10^6 µm²·ps) | best run | vs. start | runtime (min) | $/run | suite |
|---|---|---|---|---|---|---|---|---|
| 1 | GLM 5.2 | 3 | 2.760 ± 0.018 | 2.744 | -40.3% | 83 ± 18 | 2.7 ± 0.3 | pre-v1 |
| 2 | Kimi K3 | 3 | 2.908 ± 0.314 | 2.609 | -37.1% | 81 ± 31 | 7.8 ± 1.4 | pre-v1 |
| 3 | Kimi K3 · high effort | 3 | 3.424 ± 0.874 | 2.721 | -26.0% | 58 ± 18 | 7.4 ± 1.3 | pre-v1 |
| 4 | Nemotron 3 Ultra | 3 | 4.646 ± 0.116 | 4.574 | +0.5% | 78 ± 3 | 2.0 ± 0.1 | pre-v1 |
```

`--runs` lists every selected run (first rows shown):

```text
### Runs of `tpu-adp`

| Entry | Run | best (10^6 µm²·ps) | area | delay | best at eval / step | evals / failed | min | $ | suite | rtlscout |
|---|---|---|---|---|---|---|---|---|---|---|
| GLM 5.2 | `glm-5.2/20260727_085949` | 2.744 | 4350 | 630.9 | 20 / 52 | 24 / 6 | 62 | 2.45 | pre-v1 | pre-0.2.0 (unpackaged, 2026-07) |
| GLM 5.2 | `glm-5.2/20260727_085945` | 2.755 | 4349 | 633.6 | 30 / 52 | 33 / 6 | 95 | 2.97 | pre-v1 | pre-0.2.0 (unpackaged, 2026-07) |
| GLM 5.2 | `glm-5.2/20260727_085947` | 2.780 | 4338 | 640.9 | 30 / 56 | 33 / 7 | 93 | 2.69 | pre-v1 | pre-0.2.0 (unpackaged, 2026-07) |
```

`--format tex` prints a `booktabs` table. `--head-to-head` pairs the Verilog and the Spire HDL row of the same
model; it has rows once a Spire HDL entry has runs. `--task <id>` restricts any of them to one task and
`-o <file>` writes to a file.

---

## 7. How a score is computed

**The benchmark.** `suite-v1` has one design: the `tpu` benchmark of LogikBench, a weight-stationary 8x8 systolic
matrix-multiply tile (8-bit signed operands, 32-bit accumulators, 15-cycle latency). The agent starts from the
unmodified upstream RTL (`benchmarks/tpu_verilog/context/starting_point.v`) and must preserve its behaviour
cycle by cycle: the testbench replays a recorded trace of the original design, compares `c_valid` on every cycle
and `c_data` whenever it is valid.

**The objective.** The task `tpu-adp` scores a design by its area-delay product: the cell area of the
synthesized netlist in µm² times its critical-path delay in ps. The critical path bounds the clock period, and
the testbench fixes how many cycles the job takes, so throughput is proportional to 1 / delay and **throughput
per unit of silicon is proportional to 1 / (area × delay)**. Halving the product means the same die area
delivers twice the compute, whether through a smaller circuit, a faster one, or both. Neither axis can be bought
with the other at face value, and because the cycle count is pinned by the testbench the agent cannot trade
latency for area either.

**The evaluation flow.** Every `run_evaluation` call of the agent, and the baseline evaluation, does the same:

1. lint and simulate the RTL against `tb.sv` and `vectors.dat` with Verilator;
2. synthesize with Yosys and run static timing analysis with OpenROAD on ASAP7, giving area, delay and power;
3. re-simulate the gate-level netlist against the same testbench.

An evaluation passes only if all of it passes. The task's `--skip-cec` flag skips rtlscout's additional
combinational equivalence check against the golden reference; the cycle-accurate testbench, at RTL and at gate
level, is what decides correctness here.

**The score of a run** is the lowest cost among its passing evaluations. rtlscout snapshots that design as
`best_design/`.

**What counts as a valid run.** A run counts when it is `completed` (rtlscout finished and reported no error),
was made under the entry's conditions for the suite, and is not excluded. Repeats are independent launches;
there is no sampling seed. A completed run in which no evaluation passed has no score: it stays in the row's
run list, is reported as "without a passing design", and is not part of the mean.

**The starting point** ("vs. start") is the shipped starting design evaluated once with the same metric and
flags. For `tpu-adp` in Verilog the seed campaigns measured 4,624,408.88 µm²·ps (4,604 µm² × 1,004.4 ps). To
measure it yourself, inside the container:

```bash
python -m rtlscout.run_eval benchmarks/tpu_verilog/context/starting_point.v --benchmark benchmarks/tpu_verilog --cost-metric area_delay_product --skip-cec
```

```text
Workdir:  /tmp/run_eval_geqf4suf (sandbox)
Design:   starting_point.v
Language: verilog
Metric:   area_delay_product
Top:      tpu

[get_ppa] shortened 43636 over-long netlist identifiers
=== Evaluation Result ===
Correctness: PASS
  Lint: OK
  Sim:  OK
  Checks (fails/tot): 0/6782
Cost: OK
  area_delay_product: 4624408.882038144
  Metrics:
    area_delay_product: 4624408.882038144
    delay: 1004.4171
    area: 4604.072234570822
    power_probabilistic_fixed_clock: 0.0421
    target_delay: 500.0
    worst_slack: -518.57
  Worst timing path:
    [the critical path, about 55 lines]

Duration: 213.7s
```

With rtlscout 0.2.0 and the suite-v1 vectors this reproduces the seed campaigns' number: 6,782 checks pass and the
cost is 4,624,408.88.

**The row.** Per entry the table reports the mean and the sample standard deviation (n - 1 in the denominator)
of the run scores, the best single run, and the mean relative to the starting point.

**Runtime** is the wall-clock duration of the agent run as measured by rtlscout, in minutes. It depends on how
many runs share the machine, so compare runtimes only between runs of one campaign.

**Cost** is the run's token usage times list prices: input tokens × input price + output tokens × output price,
plus cache writes and cache reads at their own prices where a provider reports them (both default to the input
price), in USD per million tokens from `data/models.json` at the time the run is recorded. The record stores
the token counts and the prices used, so the figure can be recomputed.

---

## 8. The data repository

`data/` is a git submodule: the separate repository `rtlscout-bench-data`. It holds data only.

```text
data/
  runs/<task>/<entry>/<run-id>/
      record.json        everything a table or plot needs about the run
      best_design/       the workspace when the best design was found, plus _best_meta.json
      summary.txt        the agent's own write-up
      chat_log.txt       the full transcript: system prompt, tool calls, evaluation reports
  campaigns/<name>.json  versions, measured starting points and run list of one campaign
  leaderboards/<task>.json   which runs count, exclusions with reasons, notes, starting points, page texts
  models.json            display name, vendor, open weights and list prices per model
  README.md              the schema, field by field, and the provenance rules
```

**What is stored and what is not.** Per run: the record, the best design, the summary and the transcript, 0.3 to
0.6 MB together for the seed runs. Not stored: the per-evaluation workspaces, netlists and flow logs of the raw
run directory.
They stay in `runs/` as scratch. Because they are not kept, the record takes everything a later table or plot
could need out of them at recording time.

**The record**, abridged from a seed run (`data/runs/tpu-adp/glm-5.2/20260727_085945/record.json`):

```json
{
 "schema": 1,
 "suite": "pre-v1",
 "origin": "seed",
 "status": "completed",
 "rtlscout": "pre-0.2.0 (unpackaged, 2026-07)",
 "spire_hdl": null,
 "bench_commit": "<commit of rtlscout-bench that recorded it>",
 "task": "tpu-adp",
 "benchmark": "tpu_verilog",
 "language": "verilog",
 "cost_metric": "area_delay_product",
 "target_delay": null,
 "technology": "asap7",
 "entry": "glm-5.2",
 "model": "openrouter:z-ai/glm-5.2",
 "open_weights": true,
 "reasoning_effort": "default",
 "agent_backend": "react",
 "max_steps": 60,
 "flags": ["--skip-cec"],
 "env": {},
 "started": "2026-07-27T08:59",
 "best": {"cost": 2755208.05, "area": 4348.56, "delay": 633.59, "eval": 30, "step": 52, "file": "design_csa_nr.sv"},
 "evals": [
  {"e": 1, "s": 6, "ok": true, "c": 4624408.88, "area": 4604.07, "delay": 1004.42, "power": 1.72, "td": 500.0, "ctx": 12807, "t_min": 4.8, "tok_in": null, "tok_out": null},
  ...
 ],
 "n_evals": 33,
 "n_failed": 6,
 "steps_used": 60,
 "runtime_min": 94.6,
 "tokens": {"inp": 3407049, "out": 81126, "cache_w": 0, "cache_r": 0},
 "usd": 2.97,
 "price_per_mtok": [0.81, 2.55]
}
```

| group | fields |
|---|---|
| provenance | `schema`, `suite`, `origin` (`campaign`, `seed`, `manual`), `status` (`completed`, `ended-early`), `rtlscout`, `spire_hdl`, `bench_commit`, `campaign` |
| conditions | `task`, `benchmark`, `language`, `cost_metric`, `target_delay`, `technology`, `entry`, `model`, `open_weights`, `reasoning_effort`, `agent_backend`, `max_steps`, `flags`, `env` |
| result | `started`, `best` (cost, area, delay, the evaluation and step it appeared at, the file), `n_evals`, `n_failed`, `steps_used`, `runtime_min`, `tokens`, `usd`, `price_per_mtok` |
| per evaluation (`evals`) | `e` number, `s` agent step, `ok`, `c` cost, `area`, `delay`, `power`, `td` target delay, `ctx` context-window tokens, `t_min` minutes since run start, `tok_in` / `tok_out` cumulative tokens |

The field-by-field description with types is in `data/README.md`. Conditions and versions of campaign runs are
taken from the campaign file written at launch, not from the checkout at recording time. `t_min`, `tok_in` and
`tok_out` come from fields rtlscout writes per evaluation (`elapsed_s`, `cumulative_token_usage`).

**Checking the data.** The same check runs in the data repository's CI on every push and pull request:

```bash
# doctest
python -m rtlscout_bench.record --check
```
<!-- doctest-expect: data check: -->
<!-- doctest-expect: : OK -->

```text
data check: 12 records (12 selected, 0 excluded), 1 leaderboard file(s), 3 models, open-weights policy on: OK
```

It verifies the schema of every record, that each record sits in the directory its fields name, the open-weights
policy, that every run has its transcript and (if it has a best design) its `best_design/`, that no stored text
contains an API-key pattern, and that the leaderboard files only refer to runs that exist and give a reason for
every exclusion. Errors are listed one per line and the exit status is 1.

**How seed runs are marked.** The first twelve runs on the page were made in July 2026, before the suite was
fixed and before rtlscout was an installable package. They went through the same recorder as any run, adopted
from their old run directories by naming their place in the matrix and stating what they are:

```bash
OLD=<the old workspace>/runs/tpu_verilog
SEED='--task tpu-adp --origin seed --suite pre-v1'
python -m rtlscout_bench.record $OLD/z-ai_glm-5.2/20260727_0859{45,47,49} --entry glm-5.2 $SEED --rtlscout "pre-0.2.0 (unpackaged, 2026-07)"
python -m rtlscout_bench.record $OLD/moonshotai_kimi-k3/20260725_1856{48,50,52} --entry kimi-k3 $SEED --rtlscout "pre-0.2.0 (unpackaged, 2026-07)" --reasoning-effort-source "campaign notes"
python -m rtlscout_bench.record $OLD/moonshotai_kimi-k3/20260726_0638{28,30,32} --entry kimi-k3-high $SEED --rtlscout "pre-0.2.0 (unpackaged, 2026-07)" --reasoning-effort-source "campaign notes"
python -m rtlscout_bench.record $OLD/nvidia_nemotron-3-ultra-550b-a55b/20260727_10{4159,4201,4203} --entry nemotron-3-ultra $SEED --rtlscout "pre-0.2.0 (unpackaged, 2026-07)"
```

```text
recording 3 run(s) as tpu-adp/glm-5.2 (origin seed, suite pre-v1)
  recorded tpu-adp/glm-5.2/20260727_085945  best 2,755,208.05  evals 33 (33 timed)  94.6 min  $2.97
  recorded tpu-adp/glm-5.2/20260727_085947  best 2,780,231.70  evals 33 (33 timed)  93.1 min  $2.69
  recorded tpu-adp/glm-5.2/20260727_085949  best 2,744,444.65  evals 24 (24 timed)  61.9 min  $2.45
recorded 3, already recorded 0, skipped 0, refused 0
recording 3 run(s) as tpu-adp/kimi-k3 (origin seed, suite pre-v1)
  recorded tpu-adp/kimi-k3/20260725_185648  best 3,235,639.89  evals 26 (26 timed)  54.7 min  $6.65
  recorded tpu-adp/kimi-k3/20260725_185650  best 2,608,914.73  evals 28 (28 timed)  73.9 min  $9.39
  recorded tpu-adp/kimi-k3/20260725_185652  best 2,878,607.07  evals 26 (26 timed)  114.6 min  $7.50
recorded 3, already recorded 0, skipped 0, refused 0
recording 3 run(s) as tpu-adp/kimi-k3-high (origin seed, suite pre-v1)
  recorded tpu-adp/kimi-k3-high/20260726_063828  best 4,402,733.35  evals 22 (22 timed)  39.3 min  $6.24
  recorded tpu-adp/kimi-k3-high/20260726_063830  best 2,721,093.74  evals 21 (21 timed)  74.6 min  $8.76
  recorded tpu-adp/kimi-k3-high/20260726_063832  best 3,147,515.92  evals 28 (28 timed)  61.1 min  $7.18
recorded 3, already recorded 0, skipped 0, refused 0
recording 3 run(s) as tpu-adp/nemotron-3-ultra (origin seed, suite pre-v1)
  recorded tpu-adp/nemotron-3-ultra/20260727_104159  best 4,779,646.51  evals 29 (29 timed)  74.7 min  $1.97
  recorded tpu-adp/nemotron-3-ultra/20260727_104201  best 4,574,150.51  evals 31 (31 timed)  79.3 min  $2.01
  recorded tpu-adp/nemotron-3-ultra/20260727_104203  best 4,584,494.70  evals 31 (31 timed)  78.8 min  $2.09
recorded 3, already recorded 0, skipped 0, refused 0
```

What marks them:

- `"suite": "pre-v1"`, `"origin": "seed"`, `"rtlscout": "pre-0.2.0 (unpackaged, 2026-07)"` in every record. The
  page labels their rows "measured before suite-v1", and campaigns never count them towards `repeats`.
- The old run directories do not record the reasoning effort. The split of the two Kimi K3 entries by launch
  date comes from the campaign notes, and those six records say so: `"reasoning_effort_source": "campaign notes"`.
- They have no per-evaluation token counts (`tok_in`, `tok_out` are `null`). Their `t_min` was reconstructed at
  recording time from the modification time of each `eval_<n>/result.json` in the old run directory ("timed" in
  the output above); the raw directories are not kept, so this could not be done later.
- They were checked against the benchmark's original 830-cycle vector file. `suite-v1` ships 6,782 cycles, of
  which the first 830 are identical.

Adopting other run directories works the same way with `--origin manual`; `--suite` then defaults to the
matrix's suite.

---

## 9. Adding a benchmark to the suite

A benchmark pair is two self-contained directories under `benchmarks/`, one per language, with a byte-identical
testbench and vector file:

```text
benchmarks/<name>_verilog/            benchmarks/<name>_spire/
  description.txt     the task statement the agent receives
  metadata.json       name (= directory name), module_name, language, starting_point, and a source block
  tb.sv               self-checking testbench
  vectors.dat         stimulus and expected outputs (any *.dat files are provided to the testbench)
  context/starting_point.{v,sv,py}    the design the agent starts from, and the baseline
  _debug/             optional, not seen by the agent: upstream originals, what regenerates the vectors
```

A benchmark directory is a pure input. How it is scored is decided by a task in the matrix, not by the
directory.

1. Copy the directories in. `metadata.json` must have a `source` block that names the upstream repository, the
   commit and the licence. Keep vector files small enough for plain git.
2. Add the upstream attribution to `benchmarks/NOTICE`, and the licence text if it is not there yet.
3. Add a task to `campaign_matrix.yaml` ([Adding a task](#adding-a-task)) and validate. An enabled task whose
   benchmark directory is missing or incomplete is an error:

   ```text
   campaign_matrix.yaml: invalid matrix
     - task demo-adp: benchmark directory benchmarks/absent_verilog (verilog entries) is missing or incomplete (needs description.txt, metadata.json, tb.sv)
   ```

4. Measure the starting points and check that they pass:

   <!-- TO-VERIFY -->
   ```bash
   python -m rtlscout_bench.campaign --baselines-only --task <task-id>
   ```

5. Bump the suite: set `suite:` in the matrix to the new tag, commit, and tag that commit.

   <!-- TO-VERIFY -->
   ```bash
   git tag -a suite-v2 -m "suite-v2: adds <name>" && git push origin suite-v2
   ```

Every run record carries the suite tag, and campaigns count only runs of the current suite. Bumping the suite
therefore means every enabled entry is rerun on every enabled task by the next campaign; rows of the previous
suite stay on the page, labelled, until their reruns are published. Each added task multiplies campaign cost:
one more benchmark × entries × repeats. If you only add a task and leave the existing benchmarks and their
scoring untouched, you can keep the suite tag: the existing rows are unaffected, and only the new task needs
runs.

---

## 10. Upgrading rtlscout

The rtlscout version is pinned by git commit (or tag) in `pyproject.toml`:

```toml
dependencies = [
    "rtlscout @ git+https://github.com/huawei-csl/rtlscout@f4152b6f7a50d5d345543f46e01819118c32cbca",
```

To move the leaderboard to a new version, change the commit or tag, commit, and reinstall inside the container:

<!-- TO-VERIFY -->
```bash
uv pip install -e .
python -c "import importlib.metadata as m; print('rtlscout', m.version('rtlscout'))"
python -m rtlscout_bench.campaign --baselines-only
```

The version is captured when a campaign is launched and written into every record of that campaign, and a
starting point measured with another version is not reused, so the baselines are measured again.

**What must be rerun** depends on what the new version changes:

- **The evaluation flow** (synthesis, timing, simulation, metric definitions). The re-measured starting point
  tells you: `publish` reports `~ starting point (verilog): <old> -> <new>` when it moved. If it did, existing
  rows are no longer comparable with new runs: bump the suite tag and let the next campaign rerun every entry.
- **The agent** (loop, prompts, tools). The starting point does not show this. Runs before and after are not
  runs under the same conditions: bump the suite tag here too.
- **Neither** (packaging, fixes outside the flow and the agent). Nothing must be rerun. New runs carry the new
  version, which the page shows per row.

---

## 11. Submitting results

Submissions from outside are not open yet. This is the procedure; `CONTRIBUTING.md` has the same in short.

A submission is a pull request to the **data** repository with the run folders, the campaign file, and the
updated leaderboard file (plus a `models.json` row for a new model). All of it is produced by the tooling:

<!-- TO-VERIFY -->
```bash
python -m rtlscout_bench.campaign --entry <your-entry>
python -m rtlscout_bench.record
python -m rtlscout_bench.record --check
git -C data checkout -b <your-branch> && git -C data add -A && git -C data commit -m "<task>: <entry>, n=3, <suite>"
```

A new model additionally needs an entry in `campaign_matrix.yaml`, as a pull request to `rtlscout-bench`.

**How submissions are verified**

1. The data repository's CI runs the data checks and builds the site from the pull request.
2. A maintainer re-evaluates every submitted best design against the suite's own testbench and vectors (the
   `--benchmark` directory provides them, not the submitted folder) and compares the cost with `best.cost` of
   the record:

   ```bash
   python -m rtlscout.run_eval data/runs/tpu-adp/<entry>/<run-id>/best_design/<best.file> --benchmark benchmarks/tpu_verilog --cost-metric area_delay_product --skip-cec
   ```

   For the seed run `glm-5.2/20260727_085945` (`best.file` is `design_csa_nr.sv`, `best.cost` is 2755208.05) the
   result block reads:

   ```text
   === Evaluation Result ===
   Correctness: PASS
     Lint: OK
     Sim:  OK
     Checks (fails/tot): 0/6782
   Cost: OK
     area_delay_product: 2755208.0549845924
   ```

   All twelve seed designs were re-evaluated this way with rtlscout 0.2.0: each passes the 6,782 checks of the
   suite-v1 testbench and reproduces its recorded cost.

3. The pull request is merged and the maintainer publishes: `python -m rtlscout_bench.publish` shows the table
   before and after, and bumps the pointer.

All runs of a campaign are submitted, not a selection. A run that should not count is excluded with a reason.

---

## 12. Troubleshooting

**Provider timeouts and rate limits.** rtlscout retries a failed model call and, if it still fails, spends that
agent step and continues, so a short outage costs steps, not the run. Symptoms are `WARNING: API error on step`
lines in the run's log and runs that use their steps on few evaluations. Two environment variables adjust the
client: `RTLSCOUT_LLM_TIMEOUT` (seconds to wait for a response, default 300) and `RTLSCOUT_LLM_RETRIES` (default
3). If a provider throttles, lower `--parallel`. A run damaged by an outage is excluded with a reason
([Publishing](#5-publishing)) and replaced by the next campaign.

**Evaluation timeouts.** A design that is correct but whose evaluation exceeds a time limit is reported as a
failed evaluation. The limits, in seconds, and the variables that raise them:

| variable | default | limits |
|---|---|---|
| `RTLSCOUT_SIM_TIMEOUT` | 900 | one Verilator lint or simulation step, at RTL or at gate level |
| `RTLSCOUT_PPA_TIMEOUT` | 1800 | the synthesis and timing flow behind the area, delay and area × delay metrics |
| `RTLSCOUT_ADP_FAST_TIMEOUT` | 600 | the `area_delay_product_fast` flow |
| `RTLSCOUT_YOSYS_STAT_TIMEOUT` | 60 | Yosys statistics for the cell-count and transistor metrics |
| `SPIREHDL_TIMEOUT` | 60 | compiling a Spire HDL design to Verilog |

(Defaults as of the rtlscout version this manual was written against.)

Set them in the task's `env:` block in the matrix, not in your shell: the matrix value applies to the baseline
and to every run of the task, and is written into each record. A variable exported in the shell also reaches
the runs but is recorded nowhere.

**Colliding run directories.** rtlscout names a run directory by its start time to the second. The campaign
runner keeps them apart by giving every (task, entry) its own directory under `runs/` and launching two seconds
apart. Collisions are possible if you start two campaigns at once in one checkout, or launch
`rtlscout.run_benchmark` by hand into the same `--runs-dir`: two runs then write into one directory and neither
is usable. Run one campaign at a time, and give hand-launched runs their own `--runs-dir`.

**A recursive clone fails at the submodule.** `data` is registered with a URL relative to this repository
(`../rtlscout-bench-data.git`), so it is looked for next to wherever you cloned from. A clone of a fork looks for
a fork of the data repository under the same owner. Point the submodule at the data repository you want and
fetch it:

<!-- TO-VERIFY -->
```bash
git config submodule.data.url https://github.com/plex1/rtlscout-bench-data.git
git submodule update --init
```

If the tools report `data/models.json not found`, the submodule is not checked out: `git submodule update --init`.

**Verilator and sequential user-defined primitives.** Verilator 5.040 mis-simulates sequential user-defined
primitives, which some vendor cell models use for flip-flops; a gate-level simulation of such a netlist can be
silently wrong. rtlscout's image recipe carries a fix (a Verilator built with it shows `(mod)` in its version
string). The image published as `ghcr.io/huawei-csl/rtlscout:slim` when this manual was written predates the fix:

```bash
verilator --version
```

```text
Verilator 5.040 2025-08-30 rev v5.040
```

The leaderboard does not depend on it: on ASAP7, rtlscout re-simulates netlists with functional cell models that
it generates from the liberty file, and these contain no such primitives
(`NETLIST_FUNCTIONAL_SIM_CELL_MODELS` in `rtlscout/tech_eval/ppa_extract/core/template.py`, on by default). The fix
matters when that switch is turned off or a task uses a technology without functional models; measure such a task
only with a Verilator that reports `(mod)`.

**`rtlscout is not installed in this environment`.** `campaign` was started outside the container, or before
`uv pip install -e .`. Dry runs, tables, the site build and the data checks work without rtlscout; launching
runs does not.

**The site build fails.** The message lists the reason per run. A missing transcript or best design means a run
folder is incomplete; `mixes runs measured under different conditions` means one entry has selected runs that
differ in suite, model, step budget, flags or another condition: exclude one set, or give the new conditions a
new entry id.
