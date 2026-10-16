# cv32e40p_alu — benchmark construction notes

## Files here

- `orig/` — the five pristine upstream sources (github.com/openhwgroup/cv32e40p,
  master, fetched 2026-07-15): `cv32e40p_alu.sv`, `cv32e40p_popcnt.sv`,
  `cv32e40p_ff_one.sv`, `cv32e40p_alu_div.sv`, `cv32e40p_pkg.sv`.
- `make_flattened.py` — mechanically produces `../context/starting_point.sv`
  (package import removed, `alu_opcode_e` port retyped `logic [6:0]`, ALU_*/
  VEC_MODE* constants inlined as localparams, four modules concatenated).
  Regexes assert each transformation fired exactly once.
- `gen_stimuli.py` — deterministic (seeded) generator for `stimuli.dat`
  (20 000 input vectors: per-family directed corners + randomized fill;
  973 div/rem). See its docstring for the driven-envelope policy.
- `gen_tb.sv` — oracle harness: instantiates the PRISTINE `orig/` ALU (with the
  package) and replays `stimuli.dat` using the exact benchmark protocol,
  dumping `../vectors.dat` (inputs + sampled `result_o`/`comparison_result_o`).
  Rebuild + rerun:
  ```
  verilator --binary --sv --timing --top-module gen_tb -Wno-fatal \
      -Wno-DECLFILENAME -Wno-WIDTHEXPAND -Wno-UNUSEDSIGNAL -Wno-EOFNEWLINE \
      -Wno-BLKSEQ --timescale 1ns/10ps -o gen_simv \
      orig/cv32e40p_pkg.sv orig/cv32e40p_alu.sv orig/cv32e40p_popcnt.sv \
      orig/cv32e40p_ff_one.sv orig/cv32e40p_alu_div.sv gen_tb.sv
  ./obj_dir/gen_simv     # writes vectors.dat next to this file; copy to ../
  ```
- `sanity_check.py` — INDEPENDENT cross-check of `vectors.dat` against Python
  models for every unambiguous op subset (7 246 of the 20 000 vectors when last
  run): logic, plain add/sub, scalar shifts/ror, all 14 comparisons, min/max,
  scalar abs, bclr/bset, ff1/fl1/cnt/clb, and RISC-V M div/rem. Must print
  `SANITY OK`. This guards the oracle plumbing (field order, protocol,
  sampling) — a bug there would corrupt all 20k expected values identically.

## Findings that shaped the stimulus envelope

1. **Divider operands are swapped at the ALU boundary**: the dividend is
   `operand_b_i`, the divisor `operand_a_i` (`cv32e40p_alu.sv` instantiates
   `cv32e40p_alu_div` with "inputs A and B are swapped"). RISC-V M semantics
   confirmed by `sanity_check.py` including div-by-zero and INT_MIN/-1.
2. **ABS is only "absolute value" when `operand_b_i == 0`** (which the CPU
   decoder guarantees): the RTL reuses the comparator/adder, giving
   `(a >s b) ? a : (b - a)` for arbitrary b. Stimuli therefore drive ABS with
   b=0 only; anything else is a don't-care.
3. **EXT (unsigned extract) fills the upper bytes from `operand_c_i`** through
   the shuffle datapath; the decoder guarantees c=0, making it a zero-extend.
   Stimuli drive EXT/EXTS with c=0 only.
4. `comparison_result_o` is only meaningful (and only checked, `cchk=1`) for
   the 14 comparison opcodes.

## Protocol notes

Apply inputs at a negedge with `enable_i=1`, poll `ready_o` at following
negedges (cap 64), sample at the first high poll, then drop `enable_i` for one
cycle — without that idle cycle the serial divider FSM relaunches from FINISH→
IDLE while the div opcode is still applied. `gen_tb.sv` and `../tb.sv` must
stay in protocol lockstep.

Wall-clock: the 20k-vector sim runs in ~0.1 s (Verilator binary; ~48k cycles);
verilation adds ~8 s. Full `run_eval.py` incl. cost: ~11 s.
