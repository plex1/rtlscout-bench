#!/usr/bin/env python3
"""Per-cycle stimulus for the tpu benchmark (N=8, DW=8 weight-stationary
systolic tile). Emits stimuli.txt, one line per cycle:
    w_valid w_data(16 hex) a_valid a_data(16 hex)

Protocol (from tpu.v): w_valid for N cycles loads B (bottom row first);
a_valid streams A rows; C rows appear 2N-1 compute cycles later; the array
only advances when w_valid is low.

Covered: directed tiles (zero, identity, all-max/min saturating products),
random tiles, back-to-back A streams sharing loaded weights, a_valid gaps
mid-stream, weight reload between (and once DURING) drains, and a long
random soak. Deterministic (seed 7).
"""
import random

rng = random.Random(7)
N = 8
lines = []


def cyc(wv, wrow, av, arow):
    wd = 0
    ad = 0
    for j in range(N):
        wd |= (wrow[j] & 0xFF) << (8 * j)
        ad |= (arow[j] & 0xFF) << (8 * j)
    lines.append(f"{wv:x} {wd:016x} {av:x} {ad:016x}")


def idle(n):
    for _ in range(n):
        cyc(0, [0] * N, 0, [0] * N)


def load_weights(B):
    # bottom matrix row first: cycle k carries B[N-1-k][*]
    for k in range(N):
        cyc(1, B[N - 1 - k], 0, [0] * N)


def stream(A, gap_p=0.0):
    for m in range(len(A)):
        while rng.random() < gap_p:
            idle(1)
        cyc(0, [0] * N, 1, A[m])


def rnd_tile(lo=-128, hi=127):
    return [[rng.randint(lo, hi) for _ in range(N)] for _ in range(N)]


Z = [[0] * N for _ in range(N)]
I = [[127 if i == j else 0 for j in range(N)] for i in range(N)]
MX = [[127] * N for _ in range(N)]
MN = [[-128] * N for _ in range(N)]

idle(4)
# T1: zeros through zeros
load_weights(Z); stream(Z); idle(2 * N + 2)
# T2: identity-ish weights, random A
load_weights(I); stream(rnd_tile()); idle(2 * N + 2)
# T3: extreme products (max*max, min*min, min*max)
load_weights(MX); stream(MX); idle(1); stream(MN); idle(2 * N + 2)
load_weights(MN); stream(MN); idle(2 * N + 2)
# T4: random weights, three back-to-back A tiles (no reload)
B = rnd_tile(); load_weights(B)
stream(rnd_tile()); stream(rnd_tile()); stream(rnd_tile()); idle(2 * N + 2)
# T5: a_valid gaps mid-stream
load_weights(rnd_tile()); stream(rnd_tile(), gap_p=0.35); idle(2 * N + 4)
# T6: weight reload DURING the drain of the previous tile (stalls the array)
load_weights(rnd_tile()); stream(rnd_tile()); idle(3)
load_weights(rnd_tile())          # previous C rows still in flight: array stalls
stream(rnd_tile()); idle(2 * N + 2)
# T7: soak — random reloads/streams/gaps
for _ in range(12):
    load_weights(rnd_tile())
    for _ in range(rng.randint(1, 3)):
        stream(rnd_tile(), gap_p=rng.choice([0.0, 0.2]))
        idle(rng.randint(0, 6))
    idle(rng.randint(0, 2 * N + 2))
idle(2 * N + 8)

# ---- 2026-08-26 extension: ~10x coverage for agent campaigns ----
# T8: a_valid asserted DURING weight load (protocol corner: array must hold)
B = rnd_tile(); A1 = rnd_tile()
for k in range(N):
    cyc(1, B[N - 1 - k], 1, A1[k])       # both valids high
stream(rnd_tile()); idle(2 * N + 2)

# T9: sign checkerboards and alternating extremes (multiplier sign paths)
CB1 = [[127 if (i + j) % 2 == 0 else -128 for j in range(N)] for i in range(N)]
CB2 = [[-128 if (i + j) % 2 == 0 else 127 for j in range(N)] for i in range(N)]
for W, A in ((CB1, CB2), (CB2, CB1), (CB1, CB1)):
    load_weights(W); stream(A); idle(2 * N + 2)

# T10: walking-one / one-hot rows (isolates single PE columns)
for v in (1, -1, 127, -128):
    W = [[v if i == j else 0 for j in range(N)] for i in range(N)]
    load_weights(W)
    A = [[v if j == (m % N) else 0 for j in range(N)] for m in range(N)]
    stream(A); idle(2 * N + 2)

# T11: long protocol soak — reloads, gaps, mid-drain reloads
for _ in range(60):
    load_weights(rnd_tile())
    for _ in range(rng.randint(1, 4)):
        stream(rnd_tile(), gap_p=rng.choice([0.0, 0.15, 0.35]))
        idle(rng.randint(0, 5))
    if rng.random() < 0.3:
        idle(3); load_weights(rnd_tile()); stream(rnd_tile())
    idle(rng.randint(0, 2 * N + 2))

# T12: dense throughput — many tiles per weight load, no gaps
for _ in range(40):
    load_weights(rnd_tile())
    for _ in range(4):
        stream(rnd_tile())
    idle(2 * N + 2)
idle(2 * N + 8)

with open("stimuli.txt", "w") as f:
    f.write("\n".join(lines) + "\n")
print(f"{len(lines)} cycles")
