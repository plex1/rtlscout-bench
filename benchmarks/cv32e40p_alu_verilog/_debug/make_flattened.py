#!/usr/bin/env python3
"""Mechanically flatten the pristine CV32E40P ALU sources into one self-contained file.

Transformations (no logic edits):
  1. cv32e40p_alu.sv: drop `import cv32e40p_pkg::*;`, retype the enum port
     `input alu_opcode_e operator_i` as `input logic [6:0] operator_i`, and inline the
     ALU_* / VEC_MODE* constants from cv32e40p_pkg.sv as localparams inside the module.
  2. Append cv32e40p_popcnt.sv, cv32e40p_ff_one.sv, cv32e40p_alu_div.sv verbatim
     (they do not reference the package).

Output: ../context/starting_point.sv
"""
import re
from pathlib import Path

HERE = Path(__file__).parent
ORIG = HERE / "orig"
OUT = HERE.parent / "context" / "starting_point.sv"

pkg = (ORIG / "cv32e40p_pkg.sv").read_text()

# Pull the ALU opcode enum values out of the package.
ops = re.findall(r"(ALU_\w+)\s*=\s*(7'b[01]+)", pkg)
assert len(ops) == 57, f"expected 57 opcodes, got {len(ops)}"
vecs = re.findall(r"parameter\s+(VEC_MODE\d+)\s*=\s*(2'b[01]+)", pkg)
assert len(vecs) == 3, vecs

lp = ["  // ---- constants inlined from cv32e40p_pkg.sv (mechanical flatten) ----"]
for name, val in ops:
    lp.append(f"  localparam logic [6:0] {name} = {val};")
for name, val in vecs:
    lp.append(f"  localparam logic [1:0] {name} = {val};")
localparams = "\n".join(lp) + "\n"

alu = (ORIG / "cv32e40p_alu.sv").read_text()
# Drop the package import (keep the module header line intact).
alu, n = re.subn(r"module cv32e40p_alu\s*\n\s*import cv32e40p_pkg::\*;\s*\n", "module cv32e40p_alu\n", alu)
assert n == 1, "import strip failed"
# Retype the enum port.
alu, n = re.subn(r"input\s+alu_opcode_e\s+operator_i,", "input logic [ 6:0] operator_i,", alu)
assert n == 1, "operator_i retype failed"
# Insert the localparams right after the port list closes.
alu, n = re.subn(r"(input\s+logic ex_ready_i\s*\n\);\n)", r"\1\n" + localparams, alu)
assert n == 1, "localparam insertion failed"

header = """\
// -----------------------------------------------------------------------------
// Self-contained (flattened) copy of the CV32E40P ALU.
//
// Source:  https://github.com/openhwgroup/cv32e40p  (rtl/cv32e40p_alu.sv,
//          rtl/cv32e40p_popcnt.sv, rtl/cv32e40p_ff_one.sv, rtl/cv32e40p_alu_div.sv,
//          rtl/include/cv32e40p_pkg.sv), master branch, fetched 2026-07-15.
// License: Solderpad Hardware License v0.51 (headers retained below).
//
// Mechanical changes only (no logic edits):
//   * cv32e40p_pkg import removed; ALU_* opcode and VEC_MODE* constants inlined
//     as localparams; the alu_opcode_e port is typed logic [6:0].
//   * The four modules are concatenated into this single file.
// Pristine originals: see _debug/orig/ in this benchmark's source directory.
// -----------------------------------------------------------------------------

"""

parts = [header, alu]
for f in ["cv32e40p_popcnt.sv", "cv32e40p_ff_one.sv", "cv32e40p_alu_div.sv"]:
    parts.append("\n" + (ORIG / f).read_text())

OUT.write_text("".join(parts))
print(f"wrote {OUT} ({len(OUT.read_text().splitlines())} lines)")
