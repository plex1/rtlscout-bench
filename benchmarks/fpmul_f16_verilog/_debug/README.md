# fpmul_f16_verilog — provenance

`tb.sv` and `golden/design.v` come from the `fpmul_f16` benchmark of rtlscout (github.com/huawei-csl/rtlscout,
BSD-3-Clause-Clear). The golden reference is rtlscout's Spire HDL multiplier compiled to Verilog; the testbench embeds
the same reference and enumerates about 18.9 million operand pairs, so there is no vector file.

`context/starting_point.v` was written for this suite: a plain Verilog implementation of the same arithmetic, checked
to be formally equivalent to the golden reference (yosys-abc cec, run by every evaluation) and to pass the testbench.

The starting point is kept under 5,000 characters, rtlscout's cap for a `read_file` call without `offset`/`limit`, so
an agent sees the whole file in one read. (The first version had 6,590 characters; the logic is unchanged.)

Observed with GLM 5.3 Flash through OpenRouter (2026-10-10), kept here as a warning for future campaigns: every
request of the first campaign was served by the provider InferenceNet, whose endpoint dropped the `offset` argument
from the model's `read_file` calls and leaked raw `<tool_call>` text, so all three runs re-read the truncated file for
60 steps and never evaluated a design. Pinned to the vendor's own endpoint (Z.AI) the tool calls were correct, but the
first design step then reasoned to the 131,072-token output cap and returned nothing (35 minutes, no tool call).
The model's reasoning was hand-checking rounding cases of the multiplier. Neither failure is specific to the file
length; both make a 60-step campaign on this task unbounded in time and cost without a provider pin and an output cap.
