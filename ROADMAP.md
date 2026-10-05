# Roadmap

Short and public. What exists is described in [README.md](README.md) and [docs/manual.md](docs/manual.md).

## Now

- `suite-v1`: the `tpu` pair (Verilog and Spire HDL starting points, one shared testbench).
- One task, `tpu-adp`: area × delay on ASAP7, 60 agent steps, three runs per entry.
- Four entries, all open-weights models, Verilog only.
- Seed data on the page: the same four entries as measured in July 2026 with a pre-release rtlscout, labelled
  "measured before suite-v1".

## Next

- Rerun the four entries under `suite-v1` with the packaged rtlscout and replace the seed rows.
- Switch on the Spire HDL entries that are already in the matrix (disabled), and bring back a language view on
  the page once there is something to compare.
- Result-versus-spend plots. Runs made with rtlscout 0.2.0 or later record the elapsed time and the cumulative
  token usage of every evaluation, so "best result after N minutes" and "best result after N dollars" become
  exact; the seed runs only have the time.
- Further open-weights entries. Each is one block in `campaign_matrix.yaml`.

## Later

- More tasks. Candidates are benchmark pairs with a recorded upstream source and licence, vector files small
  enough for plain git, and evaluations short enough to keep a campaign affordable. Every added task multiplies
  the campaign cost: one more benchmark × entries × repeats.
- Results from other people: a pull request to the data repository, checked automatically and re-evaluated by a
  maintainer before it is merged. See [CONTRIBUTING.md](CONTRIBUTING.md).
- Other agent backends as an entry field, each as its own clearly labelled row.
- Installing rtlscout from a package index instead of a git tag.
- Starting campaigns automatically when the matrix changes, on a self-hosted runner. Campaigns need API keys,
  cost money and occupy a machine for hours, so they are started by hand for now.

## Not planned

- Ranking across tasks. Scores of different tasks have different units and flows; each task keeps its own table.
- Committing the built site. It is rebuilt from a code commit and a data commit every time.
