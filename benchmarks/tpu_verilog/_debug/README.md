# tpu_verilog — derivation & vector generation

Source: [zeroasiccorp/logikbench](https://github.com/zeroasiccorp/logikbench)
commit `e8c7c43` (MIT), `benchmarks/large/tpu/rtl/` (verbatim copies in
`orig/`). `context/starting_point.v` = tpu_pe.v + tpu_array.v + tpu.v
concatenated, unmodified. Defaults kept: N=8, DW=8, ACCW=32.

## Regenerate vectors.dat
1. `python3 gen_stimuli.py` → `stimuli.txt` (830 cycles: directed
   zero/identity/max/min tiles, random tiles, back-to-back A streams,
   a_valid gaps, mid-drain weight reload, 12-tile random soak).
2. `verilator --binary --timing -Wno-fatal -Wno-WIDTH -Wno-UNUSED
   -Wno-DECLFILENAME --top-module tb -o simg tb_golden_dump.sv orig/tpu.v
   orig/tpu_array.v orig/tpu_pe.v && ./obj_dir/simg` → `golden.txt`.
3. `paste -d' ' stimuli.txt golden.txt > ../vectors.dat`.

Alignment: 4 reset cycles, then per line apply inputs post-negedge, wait one
negedge, record/compare. 328 result rows in the trace.

Verified: starting_point.v PASS 830/830. Note: the original's reset is
synchronous; the spire sibling's is async (spire emission) — identical under
this tb (reset only at t=0). Shared vectors.dat with `tpu_spire`.

## Baseline (starting point, 2026-07-21)

asap7 `area_delay_product` = **4.624e6** (area 4,604 µm² × delay 1,004.4 ps),
harness eval PASS 830/830 (`--skip-cec`, netlist re-sim on).
