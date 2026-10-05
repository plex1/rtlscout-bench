"""Weight-stationary systolic matrix-multiply tile (a Google-TPU-style MXU) —
SpireHDL port of the logikbench `tpu` (see the verilog sibling benchmark).

Computes C = A * B on an N x N grid of processing elements. B (the "weight")
is loaded into the array and held; A (the "activation") streams through; C
drains out the bottom:

  1. Weight load: w_valid for N cycles, one weight row per cycle, BOTTOM
     matrix row first (the weights shift south); after N cycles PE(i,j)
     holds B[i][j].
  2. Compute: a_valid streams A one row per cycle. Result row C[m][*]
     appears on c_data with c_valid, 2*N-1 compute cycles after its A row.

The array advances only while w_valid is low (en = ~w_valid). Row i of A is
delayed i cycles entering the array (input skew) and column j of the result
is delayed N-1-j cycles (output de-skew), so a full result row leaves
together and the 2*N-1 latency is exact. Zeros are injected while a_valid is
low so idle cycles add nothing to the accumulators.

Structure mirrors the original register-for-register — the PE mesh, the
per-row/column skew shift chains, and the valid pipeline are generated with
Python loops instead of Verilog generate blocks. Each PE holds one stationary
signed weight, one activation hop register (west→east) and one partial-sum
register (north→south accumulate); the DW x DW signed multiply plus ACCW-bit
add is the only arithmetic.

Reset: async active-high `rst` (spire with_reset=True), all registers reset
to 0 (the original resets synchronously; under the benchmark tb — reset only
at t=0 — the behaviors are identical).
"""
from __future__ import annotations

from spire import Component, IORecord, Input, Output
from spire.expr import Const, Register, SInt, UInt, cat, fit_type, mux, reinterpret

N = 8       # array dimension (N x N)
DW = 8      # operand width (signed)
ACCW = 32   # result / accumulator width (signed)
LAT = 2 * N - 1


def _u(v: int, w: int):
    return Const(v, UInt(w))


class TpuComponent(Component):
    """N x N weight-stationary systolic matmul tile (logikbench tpu)."""

    def __init__(self) -> None:
        self.io = IORecord(
            w_valid=Input(UInt(1)),
            w_data=Input(UInt(N * DW)),
            a_valid=Input(UInt(1)),
            a_data=Input(UInt(N * DW)),
            c_valid=Output(UInt(1)),
            c_data=Output(UInt(N * ACCW)),
        )
        self.elaborate()

    def elaborate(self) -> None:
        io = self.io
        ld = io.w_valid
        en = ~io.w_valid

        def sreg(w: int, name: str) -> Register:
            return Register(SInt(w), name=name)

        def s8(bits) -> "Expr":
            return reinterpret(bits, SInt(DW))

        # ---- input skew: row i of the activation is delayed i cycles --------
        a_left = []
        for i in range(N):
            din = mux(io.a_valid, s8(io.a_data[i * DW:(i + 1) * DW]),
                      Const(0, SInt(DW)))
            sig = din
            for k in range(i):
                r = sreg(DW, f"skew_r{i}_{k}")
                r <<= mux(en, sig, r)
                sig = r
            a_left.append(sig)

        # ---- the PE mesh ----------------------------------------------------
        # w:    stationary weights, load-shifted south while ld
        # a:    activation hop registers, west -> east
        # psum: partial sums, north -> south accumulate
        w = [[sreg(DW, f"pe_w_{i}_{j}") for j in range(N)] for i in range(N)]
        a = [[sreg(DW, f"pe_a_{i}_{j}") for j in range(N)] for i in range(N)]
        psum = [[sreg(ACCW, f"pe_psum_{i}_{j}") for j in range(N)] for i in range(N)]

        for i in range(N):
            for j in range(N):
                w_north = s8(io.w_data[j * DW:(j + 1) * DW]) if i == 0 else w[i - 1][j]
                a_west = a_left[i] if j == 0 else a[i][j - 1]
                psum_north = Const(0, SInt(ACCW)) if i == 0 else psum[i - 1][j]

                w[i][j] <<= mux(ld, w_north, w[i][j])
                a[i][j] <<= mux(en, a_west, a[i][j])
                prod = a_west * w[i][j]                       # DW x DW signed
                # Accumulate as an UNSIGNED 32-bit add of the sign-extended
                # product (bit-identical to the original's
                # `psum + {{16{prod[15]}}, prod}`, whose concat operand makes
                # the add unsigned). The unsigned add keeps yosys's alumacc
                # from fusing the accumulate into the $macc — the fused
                # multiply-accumulate techmaps ~40% larger than multiplier +
                # carry-chain adder.
                sign = prod[2 * DW - 1:2 * DW]
                prod_ext_u = cat(fit_type(prod, UInt(2 * DW)),
                                 *([sign] * (ACCW - 2 * DW)))
                sum_u = fit_type(psum_north, UInt(ACCW)) + prod_ext_u
                psum[i][j] <<= mux(en, reinterpret(fit_type(sum_u, UInt(ACCW)),
                                                   SInt(ACCW)), psum[i][j])

        # ---- output de-skew: column j delayed N-1-j cycles -------------------
        c_cols = []
        for j in range(N):
            sig = psum[N - 1][j]
            for k in range(N - 1 - j):
                r = sreg(ACCW, f"deskew_r{j}_{k}")
                r <<= mux(en, sig, r)
                sig = r
            c_cols.append(fit_type(sig, UInt(ACCW)))

        io.c_data <<= cat(*c_cols)                            # col 0 = LSBs

        # ---- valid pipeline: c_valid = a_valid delayed LAT compute cycles ----
        v = io.a_valid
        for k in range(LAT):
            r = Register(UInt(1), name=f"vpipe_{k}")
            r <<= mux(en, v, r)
            v = r
        io.c_valid <<= v


# --- entry point: emit the reference Verilog --------------------------------
# 8x8 weight-stationary systolic matmul tile; ports clk, rst (async active-
# high), w_valid/w_data, a_valid/a_data -> c_valid/c_data (see description.txt).
_net = TpuComponent().to_netlist("tpu", with_clock=True, with_reset=True)
_net.to_verilog_file("design.v")
