# pdpu_dot16_pipe_verilog — provenance

`context/starting_point.v` is the upstream PDPU `pdpu_top_pipelined` (github.com/qleenju/PDPU, Apache-2.0) with N
raised from 4 to 16 and the few rewrites yosys's front end needs (listed in the file header); module hierarchy,
arithmetic and the five pipeline cut points are upstream verbatim.

`tb.sv` embeds its stimulus generator and the golden reference model (the combinational upstream unit, `pdpu_top__ref`):
2,000,000 vectors are streamed one per cycle and checked five edges later. `vectors.dat` is the directed set shared
with the combinational benchmark this one was derived from; `gen_vectors.py` regenerates it by simulating the reference
RTL (the unit is architecturally inexact against ideal posit arithmetic, so the reference is the only correct oracle).
