"""Tooling for the RTL Scout leaderboard: campaign runner, run recorder, tables, site generator.

Every tool is run as a module, from the root of an rtlscout-bench checkout:

    python -m rtlscout_bench.config      # validate and print campaign_matrix.yaml
    python -m rtlscout_bench.campaign    # launch what the matrix still needs
    python -m rtlscout_bench.record      # raw run dirs -> data/
    python -m rtlscout_bench.tables      # leaderboard tables (markdown / LaTeX)
    python -m rtlscout_bench.site        # data/ -> _site/
    python -m rtlscout_bench.publish     # record + commit data + table diff + pointer bump
"""

__version__ = "0.1.0"
