#!/usr/bin/env python3
"""Generate input blocks for the jpeg_idct_x benchmark.

Writes vectors_in.dat: one line per 8x8 block = 64 signed 16-bit coefficients
(4-hex-digit tokens, row-major c[row][col]). The expected outputs are NOT
computed here — the original core_jpeg RTL is the oracle: simulate it with
tb.sv in DUMP mode (`+define+DUMP_Y`, see _debug/README) and merge the dumped
outputs into vectors.dat (64 extra 8-hex-digit tokens per line).

Coefficient distribution mirrors dequantized-DCT reality plus adversarial
corners: JPEG coefficients are concentrated at low frequencies and small
magnitudes, but the module must be exact on the full signed-16 range.
"""
import random

random.seed(0x1DC7)
OUT = "vectors_in.dat"


def h(v):
    return f"{v & 0xFFFF:04x}"


blocks = []

# --- directed corners -------------------------------------------------------
blocks.append([0] * 64)                                    # all zero
blocks.append([32767] + [0] * 63)                          # max DC only
blocks.append([-32768] + [0] * 63)                         # min DC only
blocks.append([32767] * 64)                                # all max
blocks.append([-32768] * 64)                               # all min
blocks.append([(-1) ** i * 32767 for i in range(64)])      # alternating extremes
for pos in range(8):                                       # impulse at each column, row 0
    b = [0] * 64
    b[pos] = 20470
    blocks.append(b)
for pos in range(8):                                       # negative impulse, distinct rows
    b = [0] * 64
    b[pos * 8 + pos] = -20470
    blocks.append(b)
b = [0] * 64                                               # +/-1 LSB sensitivity
for i in range(64):
    b[i] = 1 if i % 2 == 0 else -1
blocks.append(b)

# --- random blocks -----------------------------------------------------------
for _ in range(120):                                       # full-range uniform
    blocks.append([random.randint(-32768, 32767) for _ in range(64)])
for _ in range(120):                                       # JPEG-realistic: small, low-freq heavy
    b = []
    for row in range(8):
        for col in range(8):
            span = max(1, 2048 >> (row + col))
            b.append(random.randint(-span, span))
    blocks.append(b)
for _ in range(60):                                        # sparse (few nonzero coeffs)
    b = [0] * 64
    for _ in range(random.randint(1, 6)):
        b[random.randrange(64)] = random.randint(-4096, 4096)
    blocks.append(b)

with open(OUT, "w") as f:
    f.write("# jpeg_idct_x input blocks: 64 x 16-bit signed coefficients per line "
            "(row-major c[row][col])\n")
    for b in blocks:
        f.write(" ".join(h(v) for v in b) + "\n")
print(f"wrote {len(blocks)} blocks -> {OUT}")
