#!/usr/bin/env python3
"""Generate the stimulus file for the cv32e40p_alu benchmark (inputs only).

Writes `stimuli.dat`: one vector per line, hex fields
    op vm bma bmb ive cpx sbr csh a b c cchk
(cchk=1 marks the 14 comparison opcodes whose comparison_result_o output is checked;
for every other opcode comparison_result_o is a don't-care.)

The oracle harness (gen_tb.sv) replays these against the pristine CV32E40P RTL and
appends the sampled `result_o` / `comparison_result_o` to produce vectors.dat.

Deterministic: fixed seed, no environment dependence. Exactly 20000 vectors.

Driven input envelope (everything outside it is don't-care for candidates):
  * only the 57 defined 7-bit opcodes are driven
  * vector_mode_i in {00, 10, 11} (2'b01 never driven), restricted per op family
  * bmask_a/b nonzero only for the add/sub family (round/shift) and bit-manip ops
  * imm_vec_ext nonzero only for EXT/EXTS/INS
  * is_clpx only for ABS (vm16) and the add/sub family; is_subrot only for SUB (vm16);
    clpx_shift nonzero only when is_clpx
  * div/rem driven scalar-only (vm=0, bmask=0, flags=0)
  * ABS driven with operand_b == 0 only (decoder-guaranteed in the real core)
  * EXT/EXTS driven with operand_c == 0 only (decoder-guaranteed; makes EXT a
    clean zero-extend instead of filling upper bytes from c)
"""
import random
from pathlib import Path

R = random.Random(0xC0FFEE)
OUT = Path(__file__).parent / "stimuli.dat"

# ---- opcode encodings (from cv32e40p_pkg.sv) --------------------------------
OPS = {
    "ADD": 0b0011000, "SUB": 0b0011001, "ADDU": 0b0011010, "SUBU": 0b0011011,
    "ADDR": 0b0011100, "SUBR": 0b0011101, "ADDUR": 0b0011110, "SUBUR": 0b0011111,
    "XOR": 0b0101111, "OR": 0b0101110, "AND": 0b0010101,
    "SRA": 0b0100100, "SRL": 0b0100101, "ROR": 0b0100110, "SLL": 0b0100111,
    "BEXT": 0b0101000, "BEXTU": 0b0101001, "BINS": 0b0101010,
    "BCLR": 0b0101011, "BSET": 0b0101100, "BREV": 0b1001001,
    "FF1": 0b0110110, "FL1": 0b0110111, "CNT": 0b0110100, "CLB": 0b0110101,
    "EXTS": 0b0111110, "EXT": 0b0111111,
    "LTS": 0b0000000, "LTU": 0b0000001, "LES": 0b0000100, "LEU": 0b0000101,
    "GTS": 0b0001000, "GTU": 0b0001001, "GES": 0b0001010, "GEU": 0b0001011,
    "EQ": 0b0001100, "NE": 0b0001101,
    "SLTS": 0b0000010, "SLTU": 0b0000011, "SLETS": 0b0000110, "SLETU": 0b0000111,
    "ABS": 0b0010100, "CLIP": 0b0010110, "CLIPU": 0b0010111,
    "INS": 0b0101101,
    "MIN": 0b0010000, "MINU": 0b0010001, "MAX": 0b0010010, "MAXU": 0b0010011,
    "DIVU": 0b0110000, "DIV": 0b0110001, "REMU": 0b0110010, "REM": 0b0110011,
    "SHUF": 0b0111010, "SHUF2": 0b0111011, "PCKLO": 0b0111000, "PCKHI": 0b0111001,
}
assert len(OPS) == 57

CMP_OPS = {"LTS", "LTU", "LES", "LEU", "GTS", "GTU", "GES", "GEU", "EQ", "NE",
           "SLTS", "SLTU", "SLETS", "SLETU"}

VM32, VM16, VM8 = 0b00, 0b10, 0b11

# ---- op families: allowed vector modes + flag/bmask policy ------------------
ADD_FAMILY = ["ADD", "SUB", "ADDU", "SUBU", "ADDR", "SUBR", "ADDUR", "SUBUR"]
LOGIC = ["AND", "OR", "XOR"]
SHIFTS = ["SLL", "SRL", "SRA", "ROR"]
BITMANIP = ["BEXT", "BEXTU", "BINS", "BCLR", "BSET", "BREV"]
BITCOUNT = ["FF1", "FL1", "CNT", "CLB"]
SHUFFLE = ["SHUF", "SHUF2", "PCKLO", "PCKHI", "EXT", "EXTS", "INS"]
VEC_CMP = ["LTS", "LTU", "LES", "LEU", "GTS", "GTU", "GES", "GEU", "EQ", "NE"]
SCALAR_CMP = ["SLTS", "SLTU", "SLETS", "SLETU"]
MINMAX = ["MIN", "MINU", "MAX", "MAXU"]
DIVS = ["DIVU", "DIV", "REMU", "REM"]

VM_ALLOWED = {}
for o in ADD_FAMILY + VEC_CMP + MINMAX + SHIFTS:
    VM_ALLOWED[o] = [VM32, VM16, VM8]
for o in LOGIC + BITMANIP + BITCOUNT + SCALAR_CMP + DIVS + ["CLIP", "CLIPU"]:
    VM_ALLOWED[o] = [VM32]
for o in SHUFFLE:
    VM_ALLOWED[o] = [VM16, VM8]
VM_ALLOWED["ABS"] = [VM32, VM16, VM8]

# ---- operand pools -----------------------------------------------------------
CORNERS = [
    0x00000000, 0x00000001, 0x00000002, 0x00000003, 0x00000005,
    0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0xFFFFFFFE, 0x80000001,
    0x0000FFFF, 0xFFFF0000, 0x00010000, 0x00008000, 0x00007FFF,
    0xAAAAAAAA, 0x55555555, 0x00FF00FF, 0xFF00FF00, 0x01010101,
    0x7F7F7F7F, 0x80808080, 0xFF7F80FE, 0x0000007F, 0x00000080,
    0xDEADBEEF, 0x12345678, 0xCAFEF00D, 0x00000010, 0xFFFFFF80,
]
LANE_BYTES = [0x00, 0x01, 0x7F, 0x80, 0xFF, 0xFE, 0x55, 0xAA, 0x02, 0x81]


def rnd32():
    m = R.random()
    if m < 0.35:
        return R.getrandbits(32)
    if m < 0.55:
        return R.choice(CORNERS)
    if m < 0.75:  # byte-lane patterned (interesting for SIMD)
        return int.from_bytes(bytes(R.choice(LANE_BYTES) for _ in range(4)), "little")
    if m < 0.90:
        return R.randrange(0, 256)
    return (-R.randrange(0, 256)) & 0xFFFFFFFF


def shift_amt32():
    m = R.random()
    if m < 0.5:
        return R.choice([0, 1, 7, 8, 15, 16, 30, 31])
    if m < 0.8:
        return R.getrandbits(5)
    return R.getrandbits(32)  # amounts >31: only [4:0] used per lane in vm32


VECTORS = []


def emit(opname, vm=VM32, bma=0, bmb=0, ive=0, cpx=0, sbr=0, csh=0,
         a=0, b=0, c=0):
    op = OPS[opname]
    cchk = 1 if opname in CMP_OPS else 0
    VECTORS.append((op, vm, bma & 31, bmb & 31, ive & 3, cpx & 1, sbr & 1,
                    csh & 3, a & 0xFFFFFFFF, b & 0xFFFFFFFF, c & 0xFFFFFFFF, cchk))


# =============================================================================
# Directed + per-family randomized groups
# =============================================================================

# ---- 1. add/sub family: 8 ops x 3 modes x 110 --------------------------------
for opname in ADD_FAMILY:
    rounding = opname in ("ADDR", "SUBR", "ADDUR", "SUBUR")
    for vm in VM_ALLOWED[opname]:
        for i in range(110):
            a, b = rnd32(), rnd32()
            if i < 20:  # carry/overflow corners
                a = R.choice(CORNERS)
                b = R.choice(CORNERS)
            # bmask: mostly zero (plain add/sub), sometimes round+shift
            if rounding or R.random() < 0.30:
                bma, bmb = R.getrandbits(5), R.getrandbits(5)
            else:
                bma, bmb = 0, 0
            cpx = sbr = csh = 0
            r = R.random()
            if opname == "SUB" and vm == VM16 and r < 0.15:
                sbr = 1  # pv.subrotmj
            elif not rounding and vm in (VM32, VM16) and 0.15 <= r < 0.30:
                cpx, csh = 1, R.getrandbits(2)  # clpx post-shift on add/sub
                bma, bmb = 0, 0
            emit(opname, vm=vm, bma=bma, bmb=bmb, cpx=cpx, sbr=sbr, csh=csh, a=a, b=b)

# ---- 2. logic: 3 ops x 140 ----------------------------------------------------
for opname in LOGIC:
    for a in CORNERS[:10]:
        for b in (0, 0xFFFFFFFF, a):
            emit(opname, a=a, b=b)
    for _ in range(110):
        emit(opname, a=rnd32(), b=rnd32())

# ---- 3. shifts: 4 ops x 3 modes x 90 -----------------------------------------
for opname in SHIFTS:
    for vm in VM_ALLOWED[opname]:
        for i in range(90):
            a = rnd32()
            if vm == VM32:
                b = shift_amt32() if i >= 12 else [0, 1, 15, 16, 31, 32, 33, 63,
                                                   255, 0xFFFFFFFF, 0x80000000, 5][i]
            else:
                # per-lane amounts live in the b lanes
                b = int.from_bytes(bytes(R.choice([0, 1, 3, 7, 8, 15, R.getrandbits(8) & 0x1F,
                                                   R.getrandbits(8)]) for _ in range(4)), "little")
            emit(opname, vm=vm, a=a, b=b)

# ---- 4. bit-manip: coverage of bmask space ------------------------------------
for opname in ["BEXT", "BEXTU"]:
    # realistic: extract (bma+1) bits starting at bit bmb: b = bmb (shift amount)
    for bma in range(0, 32, 3):
        for bmb in [0, 1, 7, 13, 27, 31]:
            emit(opname, bma=bma, bmb=bmb, a=rnd32(), b=bmb)
    for _ in range(120):  # random combos (incl. b != bmb, mask wrap bma+bmb>31)
        emit(opname, bma=R.getrandbits(5), bmb=R.getrandbits(5), a=rnd32(),
             b=R.getrandbits(5) if R.random() < 0.7 else rnd32())
for opname in ["BINS"]:
    for _ in range(230):
        bmb = R.getrandbits(5)
        emit(opname, bma=R.getrandbits(5), bmb=bmb, a=rnd32(),
             b=bmb if R.random() < 0.7 else R.getrandbits(5), c=rnd32())
for opname in ["BCLR", "BSET"]:
    for bma in range(0, 32, 2):
        for bmb in [0, 5, 19, 31]:
            emit(opname, bma=bma, bmb=bmb, a=rnd32())
    for _ in range(100):
        emit(opname, bma=R.getrandbits(5), bmb=R.getrandbits(5), a=rnd32())
# BREV: radix (bma[1:0] in 0..2) x shift amount (b, applied via left-shifter)
for radix in [0, 1, 2, 3]:
    for _ in range(60):
        emit("BREV", bma=radix, b=R.choice([0, 1, 2, 3, 7, 15, 31, R.getrandbits(5)]),
             a=rnd32())

# ---- 5. bit-count: single-bit sweeps + corners --------------------------------
for opname in BITCOUNT:
    for k in range(32):
        emit(opname, a=1 << k)
        emit(opname, a=(0xFFFFFFFF >> k) & 0xFFFFFFFF)
    for a in [0, 0xFFFFFFFF, 0x80000000, 0x00000001, 0xAAAAAAAA, 0x55555555]:
        emit(opname, a=a)
    for _ in range(80):
        emit(opname, a=rnd32())

# ---- 6. shuffle/pack/ext/ins: 7 ops x 2 modes x 90 ----------------------------
for opname in SHUFFLE:
    for vm in VM_ALLOWED[opname]:
        for _ in range(90):
            ive = R.getrandbits(2) if opname in ("EXT", "EXTS", "INS") else 0
            # EXT/EXTS: c=0 (decoder-guaranteed) so EXT is a clean zero-extend —
            # with c!=0 the shuffle datapath fills the upper bytes from c.
            c = 0 if opname in ("EXT", "EXTS") else rnd32()
            emit(opname, vm=vm, ive=ive, a=rnd32(), b=rnd32(), c=c)

# ---- 7. comparisons -----------------------------------------------------------
CMP_PAIRS = [
    (0, 0), (1, 0), (0, 1), (0x7FFFFFFF, 0x80000000), (0x80000000, 0x7FFFFFFF),
    (0xFFFFFFFF, 0), (0, 0xFFFFFFFF), (0xFFFFFFFF, 1), (0x80000000, 0x80000000),
    (0x7FFFFFFF, 0x7FFFFFFF), (5, 0xFFFFFFFB), (0xFFFFFFFB, 5),
    (0x00FF00FF, 0x00FF00FE), (0x80808080, 0x7F7F7F7F), (0x01020304, 0x01020305),
]
for opname in VEC_CMP:
    for vm in VM_ALLOWED[opname]:
        for (a, b) in CMP_PAIRS:
            emit(opname, vm=vm, a=a, b=b)
        for _ in range(60):
            if R.random() < 0.35:  # near-equal lanes: flip a random byte/bit
                a = rnd32()
                b = a ^ (R.choice([1, 0x80, 0x100, 0x8000, 0x10000, 0x800000,
                                   0x1000000, 0x80000000]) if R.random() < 0.8 else 0)
            else:
                a, b = rnd32(), rnd32()
            emit(opname, vm=vm, a=a, b=b)
for opname in SCALAR_CMP:
    for (a, b) in CMP_PAIRS:
        emit(opname, a=a, b=b)
    for _ in range(85):
        a = rnd32()
        b = a if R.random() < 0.25 else rnd32()
        emit(opname, a=a, b=b)

# ---- 8. min/max/abs/clip ------------------------------------------------------
for opname in MINMAX:
    for vm in VM_ALLOWED[opname]:
        for (a, b) in CMP_PAIRS:
            emit(opname, vm=vm, a=a, b=b)
        for _ in range(55):
            emit(opname, vm=vm, a=rnd32(), b=rnd32())
# ABS (incl. cplxconj variant on vm16)
for vm in VM_ALLOWED["ABS"]:
    for a in [0, 1, 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF, 0x80008000, 0x7FFF8000,
              0x80808080, 0xFF01FF01]:
        emit("ABS", vm=vm, a=a)
    for _ in range(55):
        emit("ABS", vm=vm, a=rnd32())
for _ in range(70):
    emit("ABS", vm=VM16, cpx=1, csh=0, a=rnd32())
# CLIP/CLIPU: realistic bounds b = 2^k - 1 (as the decoder produces) + corners + random
for opname in ["CLIP", "CLIPU"]:
    for k in range(32):
        b = (1 << k) - 1
        for a in [0, 1, 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF, b, (b + 1) & 0xFFFFFFFF,
                  (~b) & 0xFFFFFFFF, rnd32()]:
            emit(opname, a=a, b=b)
    for _ in range(80):
        emit(opname, a=rnd32(), b=rnd32())

# ---- 9. div/rem ---------------------------------------------------------------
DIV_PAIRS = [
    (0, 0), (1, 0), (0xFFFFFFFF, 0), (0x80000000, 0), (0x7FFFFFFF, 0),
    (0, 1), (0, 0xFFFFFFFF), (0x80000000, 0xFFFFFFFF),   # INT_MIN / -1 overflow
    (0x80000000, 1), (0x80000000, 0x80000000), (0x7FFFFFFF, 0x7FFFFFFF),
    (100, 7), (7, 100), (0xFFFFFF9C, 7), (100, 0xFFFFFFF9),
    (0xFFFFFF9C, 0xFFFFFFF9), (1, 1), (0xFFFFFFFF, 0xFFFFFFFF),
    (0xFFFFFFFF, 1), (1, 0xFFFFFFFF), (1024, 32), (1023, 32), (1025, 32),
    (0xDEADBEEF, 0x1000), (0x12345678, 3), (6, 3), (5, 3),
]
for opname in DIVS:
    for (a, b) in DIV_PAIRS:
        emit(opname, a=a, b=b)
    for i in range(180):
        m = R.random()
        if m < 0.25:
            a, b = R.getrandbits(32), R.getrandbits(32)
        elif m < 0.50:
            a, b = R.getrandbits(32), R.randrange(1, 64)
        elif m < 0.65:
            a, b = R.randrange(0, 64), R.getrandbits(32)
        elif m < 0.80:
            b = R.getrandbits(32)
            a = (b * R.randrange(0, 16) + R.randrange(0, 8)) & 0xFFFFFFFF
        elif m < 0.90:
            a, b = R.getrandbits(32), 1 << R.randrange(0, 32)
        else:
            a, b = (-R.randrange(1, 1 << 20)) & 0xFFFFFFFF, (-R.randrange(1, 256)) & 0xFFFFFFFF
        emit(opname, a=a, b=b)

# =============================================================================
# Global random fill to exactly 20000 (weighted; div ops kept scarce here)
# =============================================================================
NAMES = list(OPS)
WEIGHTS = [0.25 if n in DIVS else 1.0 for n in NAMES]

while len(VECTORS) < 20000:
    opname = R.choices(NAMES, WEIGHTS)[0]
    vm = R.choice(VM_ALLOWED[opname])
    bma = bmb = ive = cpx = sbr = csh = 0
    if opname in ADD_FAMILY and R.random() < 0.4:
        bma, bmb = R.getrandbits(5), R.getrandbits(5)
    if opname in BITMANIP:
        bma, bmb = R.getrandbits(5), R.getrandbits(5)
    if opname in ("EXT", "EXTS", "INS"):
        ive = R.getrandbits(2)
    # ABS only ever sees b=0 (as the CPU decoder guarantees); with b!=0 the RTL's
    # shared comparator/adder gives (a >s b) ? a : (b - a), which is not "abs".
    b = 0 if opname == "ABS" else rnd32()
    # EXT/EXTS: c=0 (decoder-guaranteed), see the shuffle section above.
    c = 0 if opname in ("EXT", "EXTS") else rnd32()
    emit(opname, vm=vm, bma=bma, bmb=bmb, ive=ive, cpx=cpx, sbr=sbr, csh=csh,
         a=rnd32(), b=b, c=c)

assert len(VECTORS) == 20000, len(VECTORS)
n_div = sum(1 for v in VECTORS if (v[0] >> 2) == 0b01100)
with OUT.open("w") as f:
    f.write("# op vm bma bmb ive cpx sbr csh a b c cchk\n")
    for (op, vm, bma, bmb, ive, cpx, sbr, csh, a, b, c, cchk) in VECTORS:
        f.write(f"{op:02x} {vm:x} {bma:02x} {bmb:02x} {ive:x} {cpx:x} {sbr:x} "
                f"{csh:x} {a:08x} {b:08x} {c:08x} {cchk:x}\n")
print(f"wrote {OUT}: {len(VECTORS)} vectors ({n_div} div/rem)")
