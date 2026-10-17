#!/usr/bin/env python3
"""Independent cross-check of vectors.dat against Python models.

Covers the op subsets whose semantics are unambiguous (scalar mode, no bmask,
no flags): logic, plain add/sub, scalar shifts/ror, scalar comparisons,
min/max, abs, bit-counts, bclr/bset, and RISC-V M div/rem semantics.
Any mismatch means the oracle harness (field order / protocol / sampling)
or our understanding of the design is wrong — investigate before shipping.
"""
from pathlib import Path

M32 = 0xFFFFFFFF


def s32(x):
    return x - (1 << 32) if x & 0x80000000 else x


checked = 0
skipped = 0
errors = []

for ln, line in enumerate((Path(__file__).parent / "vectors.dat").read_text().splitlines(), 1):
    if not line or line.startswith("#"):
        continue
    f = line.split()
    op, vm, bma, bmb, ive, cpx, sbr, csh = (int(x, 16) for x in f[:8])
    a, b, c, cchk, res, cmp_ = (int(x, 16) for x in f[8:])

    exp = None      # expected result_o, or None if not modeled
    expc = None     # expected comparison_result_o (only when cchk)

    plain = (vm == 0 and bma == 0 and bmb == 0 and cpx == 0 and sbr == 0)

    if op == 0b0010101:  # AND
        exp = a & b
    elif op == 0b0101110:  # OR
        exp = a | b
    elif op == 0b0101111:  # XOR
        exp = a ^ b
    elif op in (0b0011000, 0b0011010) and plain:  # ADD/ADDU, no round/shift
        exp = (a + b) & M32
    elif op in (0b0011001, 0b0011011) and plain:  # SUB/SUBU
        exp = (a - b) & M32
    elif op == 0b0100111 and vm == 0:  # SLL
        exp = (a << (b & 31)) & M32
    elif op == 0b0100101 and vm == 0:  # SRL
        exp = (a >> (b & 31)) & M32
    elif op == 0b0100100 and vm == 0:  # SRA
        exp = (s32(a) >> (b & 31)) & M32
    elif op == 0b0100110 and vm == 0:  # ROR
        sh = b & 31
        exp = ((a >> sh) | (a << (32 - sh))) & M32 if sh else a
    elif op in (0b0000010, 0b0000011, 0b0000110, 0b0000111) and vm == 0:
        # SLTS/SLTU/SLETS/SLETU -> result = {31'b0, cmp}
        signed = (op & 1) == 0
        av, bv = (s32(a), s32(b)) if signed else (a, b)
        lt = av < bv
        le = av <= bv
        expc = int(lt if op in (0b0000010, 0b0000011) else le)
        exp = expc
    elif op in (0b0000000, 0b0000001, 0b0000100, 0b0000101, 0b0001000,
                0b0001001, 0b0001010, 0b0001011, 0b0001100, 0b0001101) and vm == 0:
        signed = op in (0b0000000, 0b0000100, 0b0001000, 0b0001010)
        av, bv = (s32(a), s32(b)) if signed or op in (0b0001100, 0b0001101) else (a, b)
        if op in (0b0000000, 0b0000001):   r = av < bv     # LT
        elif op in (0b0000100, 0b0000101): r = av <= bv    # LE
        elif op in (0b0001000, 0b0001001): r = av > bv     # GT
        elif op in (0b0001010, 0b0001011): r = av >= bv    # GE
        elif op == 0b0001100:              r = a == b      # EQ
        else:                              r = a != b      # NE
        expc = int(r)
        exp = 0xFFFFFFFF if r else 0
    elif op in (0b0010000, 0b0010001, 0b0010010, 0b0010011) and vm == 0:  # MIN/MINU/MAX/MAXU
        signed = op in (0b0010000, 0b0010010)
        av, bv = (s32(a), s32(b)) if signed else (a, b)
        take_a = av < bv if op in (0b0010000, 0b0010001) else av > bv
        exp = a if take_a else b
    elif op == 0b0010100 and plain and cpx == 0:  # ABS (scalar)
        exp = (-s32(a)) & M32 if s32(a) < 0 else a
    elif op == 0b0110100:  # CNT
        exp = bin(a).count("1")
    elif op == 0b0110110:  # FF1: index of least-significant 1, or 32
        exp = 32 if a == 0 else (a & -a).bit_length() - 1
    elif op == 0b0110111:  # FL1: index of most-significant 1, or 32
        exp = 32 if a == 0 else a.bit_length() - 1
    elif op == 0b0110101:  # CLB: leading redundant sign bits count - style
        if a == 0:
            exp = 0
        elif a == M32:
            exp = 31
        else:
            # count of leading bits equal to the sign bit, minus 1
            v = a if not (a & 0x80000000) else (~a) & M32
            exp = 31 - v.bit_length()
    elif op == 0b0101011:  # BCLR
        width_mask = (~(M32 << (bma + 1))) & M32 if bma < 31 else M32
        mask = (width_mask << bmb) & M32
        exp = a & ~mask & M32
    elif op == 0b0101100:  # BSET
        width_mask = (~(M32 << (bma + 1))) & M32 if bma < 31 else M32
        mask = (width_mask << bmb) & M32
        exp = (a | mask) & M32
    elif op in (0b0110000, 0b0110001, 0b0110010, 0b0110011):  # DIVU/DIV/REMU/REM
        # RISC-V M semantics. NOTE: at the ALU port level the DIVIDEND is
        # operand_b_i and the DIVISOR is operand_a_i (the EX stage feeds the
        # serial divider with swapped operands).
        dvd, dvs = b, a
        if op == 0b0110000:  # DIVU
            exp = M32 if dvs == 0 else dvd // dvs
        elif op == 0b0110001:  # DIV
            if dvs == 0:
                exp = M32
            elif dvd == 0x80000000 and dvs == M32:
                exp = 0x80000000
            else:
                q = abs(s32(dvd)) // abs(s32(dvs))
                exp = (-q if (s32(dvd) < 0) != (s32(dvs) < 0) else q) & M32
        elif op == 0b0110010:  # REMU
            exp = dvd if dvs == 0 else dvd % dvs
        else:  # REM
            if dvs == 0:
                exp = dvd
            elif dvd == 0x80000000 and dvs == M32:
                exp = 0
            else:
                r = abs(s32(dvd)) % abs(s32(dvs))
                exp = (-r if s32(dvd) < 0 else r) & M32
    else:
        skipped += 1
        continue

    checked += 1
    if exp is not None and res != (exp & M32):
        errors.append(f"line {ln}: op={op:02x} a={a:08x} b={b:08x} "
                      f"expected {exp & M32:08x} got {res:08x}")
    if cchk and expc is not None and cmp_ != expc:
        errors.append(f"line {ln}: op={op:02x} cmp expected {expc} got {cmp_}")

print(f"checked={checked} skipped={skipped} errors={len(errors)}")
for e in errors[:20]:
    print(" ", e)
if errors:
    raise SystemExit(1)
print("SANITY OK")
