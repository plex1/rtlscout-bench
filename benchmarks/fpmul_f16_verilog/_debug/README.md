# fpmul_f16_verilog — provenance

`tb.sv` and `golden/design.v` come from the `fpmul_f16` benchmark of rtlscout (github.com/huawei-csl/rtlscout,
BSD-3-Clause-Clear). The golden reference is rtlscout's Spire HDL multiplier compiled to Verilog; the testbench embeds
the same reference and enumerates about 18.9 million operand pairs, so there is no vector file.

`context/starting_point.v` was written for this suite: a plain Verilog implementation of the same arithmetic, checked
to be formally equivalent to the golden reference (yosys-abc cec, run by every evaluation) and to pass the testbench.
