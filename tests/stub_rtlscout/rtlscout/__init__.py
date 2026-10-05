"""Test double for the rtlscout package: just enough of ``rtlscout.run_benchmark`` and ``rtlscout.run_eval`` to
exercise the campaign runner without EDA tools or a model. Put ``tests/stub_rtlscout`` on PYTHONPATH to use it.

It reproduces the interface the bench tooling relies on: the command-line flags, the run directory layout
(``<runs-dir>/<benchmark>/<model with / replaced by _>/<YYYYMMDD_HHMMSS>/``), the files in it, the
``Results stored in:`` line, and the JSON printed by ``run_eval --json``.
"""
