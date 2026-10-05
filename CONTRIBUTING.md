# Contributing

There are two kinds of contribution, and they go to two different repositories.

| you want to | goes to |
|---|---|
| submit results (runs of a model) | a pull request to the **data** repository, [`rtlscout-bench-data`](https://github.com/plex1/rtlscout-bench-data) |
| change the tooling, the page, the manual, the matrix or the suite | a pull request to this repository |

## Submitting results

Submissions are not open yet; this is the procedure they will follow. The scope is open-weights models: a run of a
model that is not marked `"open_weights": true` in `models.json` is refused by the recorder and by the checks.

A submission is a pull request to the data repository that contains

- the run folders, `runs/<task>/<entry>/<run-id>/`, each with `record.json`, `best_design/`, `summary.txt` and
  `chat_log.txt`;
- the campaign file, `campaigns/<name>.json`;
- the updated `leaderboards/<task>.json`, and a row in `models.json` if the model is new.

You do not write any of these by hand. Run the campaign with the tooling of this repository at the current suite
tag, inside the rtlscout container, and let the recorder produce them (manual: "Running a campaign",
"Publishing", "Submitting results"):

```bash
python -m rtlscout_bench.campaign --entry <your-entry>
python -m rtlscout_bench.record
python -m rtlscout_bench.record --check
```

A new model also needs an entry in `campaign_matrix.yaml`, as a pull request to this repository.

What happens to the pull request:

1. The data repository's checks run automatically: record schema, the open-weights policy, every run has its
   best design and transcript, no API-key patterns in any stored text, and the site builds.
2. A maintainer re-evaluates each submitted best design with the suite's own testbench and vectors:

   ```bash
   python -m rtlscout.run_eval data/runs/<task>/<entry>/<run-id>/best_design/<file> \
       --benchmark benchmarks/<benchmark> --cost-metric <metric> <the task's scoring flags>
   ```

   The cost must reproduce the `best.cost` of the record.
3. The pull request is merged, and the maintainer bumps the `data` pointer in this repository. That bump is what
   publishes the numbers.

Rules that keep the table honest:

- All runs of a campaign are submitted, not the best ones. A run that should not count is listed under `exclude`
  in the leaderboard file with a reason; it is not left out.
- Transcripts are published verbatim. Check them before you submit; the recorder refuses API-key patterns but
  cannot know what else you consider private.
- Records are not edited after they are written.

## Changing this repository

- Run the tests: `pytest`. They need `pyyaml` and `pytest` only.
- A change to a command, a flag, a matrix field or a record field updates `docs/manual.md` in the same commit.
  Commands in the manual marked `# doctest` are executed by the tests.
- A change to the record format updates the schema description in the data repository's `README.md` and bumps
  `SCHEMA` in `rtlscout_bench/record.py`.
- A change to a benchmark directory or to how a task is scored is a new suite tag, and the affected entries are
  rerun (manual: "Adding a benchmark to the suite", "Upgrading rtlscout").
- Style: lines up to 120 characters.

## Adding a benchmark

See the manual, "Adding a benchmark to the suite". In short: a self-contained directory under `benchmarks/` with
a recorded upstream source and licence, an entry in `benchmarks/NOTICE`, a task in `campaign_matrix.yaml`, and a
suite tag bump.
