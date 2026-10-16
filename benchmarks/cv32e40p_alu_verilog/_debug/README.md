# cv32e40p_alu_verilog — benchmark construction

Upstream: https://github.com/openhwgroup/cv32e40p, commit 6033d2b (master as of 2026-07-15), Solderpad 0.51.
`orig/` holds the five pristine files; `make_flattened.py` produces `../context/starting_point.sv` from them
(mechanical: package import removed, constants inlined, enum port typed `logic [6:0]`; every edit asserted once).

Vectors: `gen_stimuli.py` (seeded; per-family directed corners plus random fill, 20,000 vectors, 973 div/rem)
writes `stimuli.dat`; `gen_tb.sv` replays it against the pristine RTL and records `vectors.dat`;
`gen_divheavy_vectors.py` then replaces 2,027 random non-div lines with hard divisions (small divisors, random
signs), giving the 3,000 div/rem (15%) of the shipped `../vectors.dat`. `sanity_check.py` cross-checks the
recorded expectations against independent Python models for every unambiguous opcode subset (must print
`SANITY OK`). `NOTES.md` records the findings that shaped the driven envelope (swapped divider operands, ABS and
EXT decoder guarantees, comparison flag validity).

Testbench (`../tb.sv`, written for this suite): an equivalence checker. It embeds the flattened upstream ALU as
the reference (modules renamed `__ref`), drives both designs identically and compares `ready_o` at every
falling edge and the results at the reference's ready. Phases: the vector file (the reference is also checked
against the recorded expectations, which guards the harness), a divider sweep over every divisor and dividend
bit length (8,192 operations) and 1,000,000 fixed-seed random operations inside the driven envelope. With the
starting point: 1,028,192 checks, 0 errors, about 3 s of simulation after about 8 s of verilation.

The paper's task-level variants of this benchmark (free schedule, scored on area x runtime) live in the
rtlscout_integration repository under internal/task_level/benchmarks/; this suite uses the fixed-schedule
contract and area x delay like its other tasks.
