# rtlscout-bench

**User manual: [docs/manual.md](docs/manual.md)** · Roadmap: [ROADMAP.md](ROADMAP.md) · Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

The benchmark suite, the campaign tooling and the page template of the RTL Scout leaderboard: how far can an LLM
agent push a hardware design? Each model drives the [RTL Scout](https://github.com/huawei-csl/rtlscout) agent for
a fixed number of steps on the same starting design, with a self-checking testbench and a full synthesis flow in
the loop. The score is the best verified cost the agent reached.

Updating the leaderboard is two commands:

```bash
python -m rtlscout_bench.campaign      # runs whatever the matrix still needs
python -m rtlscout_bench.publish       # records, shows the table diff, asks, pushes
```

The page then rebuilds and deploys by itself. Adding a model is one new block in `campaign_matrix.yaml` before the
first command.

## How it is organised

| what | where |
|---|---|
| the engine: agent loop and evaluation flow | the [`rtlscout`](https://github.com/huawei-csl/rtlscout) package, pinned to a commit or tag in `pyproject.toml` |
| the suite: benchmark directories | `benchmarks/` (this repository) |
| what is scored and who is scored | `campaign_matrix.yaml`: `tasks` and `entries` |
| the tooling | the `rtlscout_bench` package (this repository) |
| the page template (contains no results) | `site/index.html` |
| run records, best designs, summaries, transcripts | [`rtlscout-bench-data`](https://github.com/plex1/rtlscout-bench-data), the `data/` submodule |
| raw campaign output | `runs/`, local scratch, not in git |
| the built site | `_site/`, not committed: CI builds it from this commit plus the data commit it points at |

Code lives here, data lives in the data repository, and the deployed site is always exactly one commit of each.
Bumping the `data` submodule pointer is the publish action, so every published number can be traced and rebuilt.

## The tools

Every tool runs as a module from the root of a checkout and reads `campaign_matrix.yaml`.

| command | does |
|---|---|
| `python -m rtlscout_bench.config` | validates the matrix and prints tasks, entries and the resulting jobs |
| `python -m rtlscout_bench.campaign` | measures the starting points and launches the runs the matrix still needs; resumable |
| `python -m rtlscout_bench.record` | raw run directories to records and artefacts in `data/`; `--check` validates `data/` |
| `python -m rtlscout_bench.tables` | leaderboard tables as text, markdown or LaTeX |
| `python -m rtlscout_bench.site` | `data/` to `_site/` (the page and one data file) |
| `python -m rtlscout_bench.publish` | record, commit the data, show the table before and after, bump the pointer |

## Look at the current results locally

No container and no API key are needed to build the page from the published data:

```bash
git clone --recurse-submodules https://github.com/plex1/rtlscout-bench.git && cd rtlscout-bench
pip install pyyaml && pip install --no-deps -e .
python -m rtlscout_bench.site          # then open _site/index.html
```

Running campaigns needs the rtlscout container image and provider API keys; see the manual, "Setup".

## Scope today

- **suite-v1** is one design in two languages: `benchmarks/tpu_verilog` and `benchmarks/tpu_spire`, a
  weight-stationary 8x8 systolic matrix-multiply tile from [LogikBench](https://github.com/zeroasiccorp/logikbench)
  (MIT), scored by area × delay on ASAP7. See `benchmarks/README.md`.
- **Open-weights models only.** `policy.open_weights_only` in the matrix makes the recorder and the site build
  refuse any model that is not marked open-weights in `data/models.json`.
- The first rows on the page are seed data measured before the suite was fixed. They are labelled as such and
  are replaced by reruns under `suite-v1`.

## Tests

```bash
pip install pyyaml pytest && pip install --no-deps -e .
pytest
```

The tests use synthetic run directories and a stand-in for the rtlscout package, so they need neither the EDA
tools nor a model. They also execute the commands of the manual that are marked as checkable.

## Licence

MIT, see `LICENSE`. The benchmark starting points derive from LogikBench (MIT); see `benchmarks/NOTICE` and
`benchmarks/LICENSE`. The run data lives in the data repository under CC-BY-4.0.
