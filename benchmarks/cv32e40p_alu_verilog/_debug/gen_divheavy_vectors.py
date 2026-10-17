#!/usr/bin/env python3
"""Div-heavy vector set for cv32e40p_alu_divh_tl (STUDY 9.4 item 11).

The original workload under-exercises the serial divider twice over: 4.87%
div/rem share AND uniform-random divisors (wide -> early termination, mean
latency 9.57 of a possible ~34). The freed-schedule ceiling was therefore
1.417x while equivalence tuning alone reaches 1.40x -- the tracks could not
separate. This set keeps the original 973 div lines (incl. ISA corners) and
replaces 2,027 random non-div lines with HARD divides: divisor magnitude
log-uniform in 1..10 bits (latency ~ 34 - divisor_bits -> 24..33 cycles),
signed ops with random signs, dividends uniform. Result: 3,000/20,000 = 15%
div share. Goldens computed from RISC-V M semantics (the corner conventions
of the benchmark description) and validated by running the bit-exact
starting point against the new vectors.

Run: ~/pyenv_eda/bin/python internal/task_level/benchmarks/cv32e40p_alu_divh_tl/_debug/gen_divheavy_vectors.py
"""
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / "cv32e40p_alu_spire_tl" / "vectors.dat"
DST = HERE.parent / "vectors.dat"

SEED = 23
TARGET_DIV = 3000
DIV_OPS = (0x30, 0x31, 0x32, 0x33)   # DIVU DIV REMU REM

rng = random.Random(SEED)


def s32(x):
    return x - 0x100000000 if x >= 0x80000000 else x


def u32(x):
    return x & 0xFFFFFFFF


def golden(op, a, b):
    """RISC-V M semantics; dividend = b, divisor = a (benchmark convention)."""
    if op == 0x30:                                    # DIVU
        return 0xFFFFFFFF if a == 0 else b // a
    if op == 0x32:                                    # REMU
        return b if a == 0 else b % a
    sa, sb = s32(a), s32(b)
    if op == 0x31:                                    # DIV
        if a == 0:
            return 0xFFFFFFFF
        if b == 0x80000000 and a == 0xFFFFFFFF:
            return 0x80000000
        q = abs(sb) // abs(sa)
        return u32(q if (sa < 0) == (sb < 0) else -q)
    if op == 0x33:                                    # REM
        if a == 0:
            return b
        if b == 0x80000000 and a == 0xFFFFFFFF:
            return 0
        q = abs(sb) // abs(sa)
        q = q if (sa < 0) == (sb < 0) else -q
        return u32(sb - q * s32(u32(sa)))


def hard_div_line():
    op = rng.choice(DIV_OPS)
    bits = rng.randint(1, 10)                         # small divisor -> long div
    mag = rng.randint(1 << (bits - 1), (1 << bits) - 1) if bits > 1 else 1
    if op in (0x31, 0x33) and rng.random() < 0.5:
        a = u32(-mag)                                 # signed: small |divisor|
    else:
        a = mag
    b = rng.getrandbits(32)
    res = golden(op, a, b)
    return (f"{op:x} 0 00 00 0 0 0 0 {a:08x} {b:08x} 00000000 0 {res:08x} 0")


def main():
    lines = SRC.read_text().splitlines()
    header, body = lines[0], lines[1:]
    div_idx = [i for i, ln in enumerate(body) if int(ln.split()[0], 16) in DIV_OPS]
    nondiv_idx = [i for i in range(len(body)) if i not in set(div_idx)]
    need = TARGET_DIV - len(div_idx)
    assert need > 0
    for i in rng.sample(nondiv_idx, need):
        body[i] = hard_div_line()
    DST.write_text("\n".join([header] + body) + "\n")
    ndiv = sum(1 for ln in body if int(ln.split()[0], 16) in DIV_OPS)
    print(f"wrote {DST}: {len(body)} vectors, {ndiv} div/rem ({100*ndiv/len(body):.1f}%)")


if __name__ == "__main__":
    main()
