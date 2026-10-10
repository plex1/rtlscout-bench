#!/usr/bin/env python3
"""Generate vectors.dat for the pdpu_dot16 benchmark.

Oracle = the reference RTL itself (PDPU is architecturally inexact vs ideal
posit math due to ALIGN_WIDTH truncation, so the ONLY correct golden is the
reference implementation): directed + random stimuli are simulated on
context/starting_point.v with a print harness, and the observed outputs
become the expected values.

vectors.dat line format (hex, no 0x):  operands_a operands_b acc expected
"""
import random
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
REF = BENCH / "context" / "starting_point.v"
OUT = BENCH / "vectors.dat"
random.seed(20260807)

# ---- posit8 (es=2) special values -----------------------------------------
P8 = {
    "zero": 0x00, "nar": 0x80, "minpos": 0x01, "maxpos": 0x7F,
    "neg_minpos": 0xFF, "neg_maxpos": 0x81, "one": 0x40, "neg_one": 0xC0,
    "half": 0x38, "two": 0x48, "rg_lo1": 0x02, "rg_lo2": 0x03,
    "rg_hi1": 0x7E, "rg_hi2": 0x7D, "neg_rg": 0x82, "es_pat1": 0x44,
    "es_pat2": 0x4C, "es_pat3": 0x34, "frac_ones": 0x47, "frac_max": 0x4F,
}
P16 = {
    "zero": 0x0000, "nar": 0x8000, "minpos": 0x0001, "maxpos": 0x7FFF,
    "neg_minpos": 0xFFFF, "neg_maxpos": 0x8001, "one": 0x4000,
    "neg_one": 0xC000, "half": 0x3800, "big": 0x7000, "neg_big": 0x9000,
    "small": 0x0800, "frac": 0x4ABC, "neg_frac": 0xB544,
}


def neg8(v):
    return (-v) & 0xFF


def pack(lanes):
    x = 0
    for i, v in enumerate(lanes):
        x |= (v & 0xFF) << (8 * i)
    return x


def gen_stimuli():
    S = []
    p8 = list(P8.values())
    p16 = list(P16.values())

    # 1. all-lanes-identical sweeps over special x special x special acc
    for va in p8:
        for vb in (P8["zero"], P8["one"], P8["maxpos"], P8["minpos"],
                   P8["neg_one"], P8["nar"]):
            for acc in (P16["zero"], P16["one"], P16["maxpos"],
                        P16["nar"], P16["neg_one"]):
                S.append((pack([va] * 16), pack([vb] * 16), acc))

    # 2. single-lane isolation: one active lane, others zero
    for lane in range(16):
        for va in p8:
            for acc in (P16["zero"], P16["small"], P16["neg_big"]):
                a = [0] * 16
                b = [0] * 16
                a[lane] = va
                b[lane] = P8["one"]
                S.append((pack(a), pack(b), acc))

    # 3. cancellation: pairs of lanes that cancel exactly, tiny accumulator
    for v in (P8["one"], P8["maxpos"], P8["half"], P8["two"], P8["frac_max"]):
        S.append((pack([v, neg8(v)] * 8),
                  pack([P8["one"]] * 16), P16["zero"]))
        S.append((pack([v, v, neg8(v), neg8(v)] * 4),
                  pack([P8["one"]] * 16), P16["minpos"]))
        S.append((pack([v, neg8(v)] + [0] * 14), pack([P8["one"]] * 16),
                  P16["neg_minpos"]))

    # 4. alignment-limit stress: max exponent spread across lanes (products
    #    differing by >ALIGN_WIDTH positions -> truncation paths)
    for acc in (P16["zero"], P16["maxpos"], P16["minpos"], P16["neg_big"]):
        S.append((pack([P8["maxpos"], P8["minpos"], P8["one"], P8["rg_lo1"]] * 4),
                  pack([P8["maxpos"], P8["minpos"], P8["one"], P8["rg_hi1"]] * 4),
                  acc))
        S.append((pack([P8["minpos"]] * 16), pack([P8["minpos"]] * 16), acc))
        S.append((pack([P8["maxpos"]] * 16), pack([P8["maxpos"]] * 16), acc))

    # 5. random lanes with special accs, and special lanes with random accs
    for _ in range(400):
        S.append((random.getrandbits(128), random.getrandbits(128),
                  random.choice(p16)))
    for _ in range(400):
        lanes_a = [random.choice(p8) for _ in range(16)]
        lanes_b = [random.choice(p8) for _ in range(16)]
        S.append((pack(lanes_a), pack(lanes_b), random.getrandbits(16)))

    # 6. pure random bulk
    seen = set()
    while len(S) < 10000:
        t = (random.getrandbits(128), random.getrandbits(128),
             random.getrandbits(16))
        if t not in seen:
            seen.add(t)
            S.append(t)
    return S


HARNESS = """
module harness;
  integer fd, rc;
  string line;
  logic [127:0] a, b;
  logic [15:0] acc;
  logic [15:0] res;
  pdpu_top dut (.operands_a(a), .operands_b(b), .acc(acc), .result_o(res));
  initial begin
    fd = $fopen("stimuli.txt", "r");
    if (fd == 0) $fatal(1, "no stimuli.txt");
    while (!$feof(fd)) begin
      rc = $fgets(line, fd);
      if (rc == 0) continue;
      rc = $sscanf(line, "%h %h %h", a, b, acc);
      if (rc != 3) continue;
      #1;
      $display("VEC %032h %032h %04h %04h", a, b, acc, res);
    end
    $fclose(fd);
    $finish;
  end
endmodule
"""


def main():
    stim = gen_stimuli()
    print(f"{len(stim)} stimuli")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "harness.sv").write_text(HARNESS)
        (td / "stimuli.txt").write_text(
            "\n".join(f"{a:032x} {b:032x} {c:04x}" for a, b, c in stim) + "\n")
        subprocess.run(["verilator", "--binary", "--timing", "-Wno-fatal",
                        "--top-module", "harness", "-o", "simv",
                        str(REF), str(td / "harness.sv")],
                       cwd=td, check=True, capture_output=True)
        r = subprocess.run([str(td / "obj_dir" / "simv")], cwd=td,
                           capture_output=True, text=True, check=True)
    rows = [l.split()[1:] for l in r.stdout.splitlines() if l.startswith("VEC ")]
    assert len(rows) == len(stim), (len(rows), len(stim))
    with open(OUT, "w") as f:
        f.write("# operands_a operands_b acc expected_result "
                "(hex; oracle = reference RTL, pdpu_dot16 gen_vectors.py)\n")
        for a, b, c, y in rows:
            f.write(f"{a} {b} {c} {y}\n")
    print(f"wrote {OUT} ({len(rows)} vectors)")


if __name__ == "__main__":
    main()
