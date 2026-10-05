# The benchmark suite

`suite-v1` is one design in two languages. Both directories share a byte-identical testbench and vector file, so a
result on one is checked against exactly the same behaviour as a result on the other.

| directory | starting point | used by |
|---|---|---|
| `tpu_verilog/` | `context/starting_point.v`, the upstream RTL | every Verilog entry of the `tpu-*` tasks |
| `tpu_spire/` | `context/starting_point.py`, a Spire HDL port of the same design | every Spire HDL entry of the `tpu-*` tasks |

The design is the `tpu` benchmark of [LogikBench](https://github.com/zeroasiccorp/logikbench) (commit `e8c7c43`, MIT):
a weight-stationary 8x8 systolic matrix-multiply tile with 8-bit signed operands and 32-bit accumulators. See
`NOTICE` for what is taken from upstream and `LICENSE` for the upstream licence text.

## What a benchmark directory contains

| file | role |
|---|---|
| `description.txt` | the task statement the agent receives |
| `metadata.json` | name, top module, language, golden reference, and a `source` block with origin and licence |
| `tb.sv` | self-checking testbench; replays `vectors.dat` and compares cycle by cycle |
| `vectors.dat` | one line per clock cycle: stimulus and the expected outputs of the upstream RTL |
| `context/starting_point.*` | the design the agent starts from; also the baseline that scores are compared to |
| `_debug/` | not seen by the agent: upstream originals and what is needed to regenerate the vectors |

A benchmark directory is a pure input. How it is scored (cost metric, flags, timeouts) is decided by the `tasks`
section of `campaign_matrix.yaml`, not here.

## The vector file

`vectors.dat` has 6,782 cycles. Its first 830 lines are the original vector set (directed tiles, random tiles,
back-to-back streams, valid gaps, mid-drain weight reloads, a random soak); the rest was appended on 2026-08-26
(both valids high during a weight load, sign checkerboards, one-hot rows, a long protocol soak, dense throughput).
The generator is deterministic, so the stimulus half can be reproduced exactly:

```bash
cd benchmarks/tpu_verilog/_debug
python3 gen_stimuli.py                      # -> stimuli.txt, prints "6782 cycles"
cut -d' ' -f1-4 ../vectors.dat | cmp - stimuli.txt && echo identical
```

The expected-output half comes from simulating the upstream RTL in `orig/` with `tb_golden_dump.sv`;
`_debug/README.md` has the Verilator command. That file is the original derivation note from 2026-07-21 and still
speaks of 830 cycles in places; the numbers above are the current ones.
