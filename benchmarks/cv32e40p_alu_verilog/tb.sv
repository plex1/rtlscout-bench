`timescale 1ns/10ps
// Equivalence testbench for cv32e40p_alu, the CV32E40P (RI5CY) execution-stage ALU.
//
// The design under test runs side by side with the upstream ALU, embedded below as cv32e40p_alu__ref
// (the flattened upstream sources of starting_point.sv with every module renamed). Both receive the same
// stimulus, and at every falling clock edge after reset the design's ready_o must equal the reference's:
// an operation is accepted or completed in exactly the cycles the upstream ALU accepts or completes it.
// When the reference signals ready, result_o must equal the reference's, and comparison_result_o as well
// for the 14 comparison opcodes (for every other opcode that output is a don't-care).
//
// Three stimulus phases, all deterministic:
//   1. vectors.dat: 20,000 directed and seeded-random vectors with expected outputs captured from the
//      pristine upstream RTL (fields: op vm bma bmb ive cpx sbr csh a b c cchk exp_result exp_cmp, hex).
//      The reference is also checked against these expected values, which guards the harness itself.
//   2. a divider sweep: every divisor bit length x every dividend bit length x the four div/rem opcodes,
//      twice, with random low bits and random signs for the signed opcodes (8,192 operations).
//   3. N_RANDOM operations from a fixed-seed xorshift generator, within the driven envelope of the task
//      description (opcode set, vector modes per opcode family, modifier flags per opcode).
//
// Protocol (the EX stage's): rst_n low for the first 5 cycles. For each operation all inputs are driven
// at a falling edge with enable_i=1 and ex_ready_i=1 and held; ready_o is polled at the following falling
// edges (cap 64 -> error); the outputs are checked at the first poll where the reference is ready; then
// enable_i is deasserted for one cycle before the next operation. ready_o is compared at every poll and
// in the idle cycle between operations.
module tb;
  logic clk;
  initial clk = 1'b0;
  always #5 clk = ~clk;

  localparam int N_RANDOM = 1_000_000;   // phase 3 length
  localparam int MAX_SHOWN = 10;         // error lines printed; every error is counted

  logic        rst_n, enable_i, ex_ready_i;
  logic [6:0]  op;
  logic [31:0] a, b, c;
  logic [1:0]  vm, ive, csh;
  logic [4:0]  bma, bmb;
  logic        cpx, sbr;
  logic [31:0] result_o, result_ref;
  logic        cmp_o, cmp_ref, ready_o, ready_ref;

  cv32e40p_alu dut (
      .clk(clk), .rst_n(rst_n), .enable_i(enable_i), .operator_i(op),
      .operand_a_i(a), .operand_b_i(b), .operand_c_i(c), .vector_mode_i(vm),
      .bmask_a_i(bma), .bmask_b_i(bmb), .imm_vec_ext_i(ive), .is_clpx_i(cpx), .is_subrot_i(sbr), .clpx_shift_i(csh),
      .result_o(result_o), .comparison_result_o(cmp_o), .ready_o(ready_o), .ex_ready_i(ex_ready_i)
  );
  cv32e40p_alu__ref refm (
      .clk(clk), .rst_n(rst_n), .enable_i(enable_i), .operator_i(op),
      .operand_a_i(a), .operand_b_i(b), .operand_c_i(c), .vector_mode_i(vm),
      .bmask_a_i(bma), .bmask_b_i(bmb), .imm_vec_ext_i(ive), .is_clpx_i(cpx), .is_subrot_i(sbr), .clpx_shift_i(csh),
      .result_o(result_ref), .comparison_result_o(cmp_ref), .ready_o(ready_ref), .ex_ready_i(ex_ready_i)
  );

  // ---- the 57 opcodes (cv32e40p_pkg.sv) and their families ------------------------------------------
  localparam logic [6:0] OPS [0:56] = '{
      7'h18, 7'h19, 7'h1a, 7'h1b, 7'h1c, 7'h1d, 7'h1e, 7'h1f,   // ADD SUB ADDU SUBU ADDR SUBR ADDUR SUBUR
      7'h15, 7'h2e, 7'h2f,                                       // AND OR XOR
      7'h24, 7'h25, 7'h26, 7'h27,                                // SRA SRL ROR SLL
      7'h28, 7'h29, 7'h2a, 7'h2b, 7'h2c, 7'h49,                  // BEXT BEXTU BINS BCLR BSET BREV
      7'h36, 7'h37, 7'h34, 7'h35,                                // FF1 FL1 CNT CLB
      7'h3e, 7'h3f, 7'h2d,                                       // EXTS EXT INS
      7'h00, 7'h01, 7'h04, 7'h05, 7'h08, 7'h09, 7'h0a, 7'h0b, 7'h0c, 7'h0d,   // LTS LTU LES LEU GTS GTU GES GEU EQ NE
      7'h02, 7'h03, 7'h06, 7'h07,                                // SLTS SLTU SLETS SLETU
      7'h10, 7'h11, 7'h12, 7'h13, 7'h14, 7'h16, 7'h17,           // MIN MINU MAX MAXU ABS CLIP CLIPU
      7'h3a, 7'h3b, 7'h38, 7'h39,                                // SHUF SHUF2 PCKLO PCKHI
      7'h30, 7'h31, 7'h32, 7'h33};                               // DIVU DIV REMU REM
  localparam logic [6:0] OP_SUB = 7'h19, OP_ABS = 7'h14, OP_EXTS = 7'h3e, OP_EXT = 7'h3f, OP_INS = 7'h2d;

  function automatic bit is_cmp(input logic [6:0] o);      return o < 7'h0e;                 endfunction
  function automatic bit is_div(input logic [6:0] o);      return o[6:2] == 5'b01100;        endfunction  // DIVU DIV REMU REM
  function automatic bit is_sdiv(input logic [6:0] o);     return o == 7'h31 || o == 7'h33;  endfunction  // DIV REM
  function automatic bit is_addfam(input logic [6:0] o);   return o[6:3] == 4'b0011;         endfunction  // 18..1f
  function automatic bit is_rounding(input logic [6:0] o); return is_addfam(o) && o[2];      endfunction  // ADDR SUBR ADDUR SUBUR
  function automatic bit is_shift(input logic [6:0] o);    return o[6:2] == 5'b01001;        endfunction  // 24..27
  function automatic bit is_bitmanip(input logic [6:0] o); return (o >= 7'h28 && o <= 7'h2c) || o == 7'h49; endfunction
  function automatic bit is_veccmp(input logic [6:0] o);   return is_cmp(o) && !o[1];        endfunction  // not the scalar set-less ops
  function automatic bit is_minmax(input logic [6:0] o);   return o[6:2] == 5'b00100;        endfunction  // 10..13
  function automatic bit is_shuffle(input logic [6:0] o);  return (o >= 7'h38 && o <= 7'h3b) || o == OP_EXTS || o == OP_EXT || o == OP_INS; endfunction
  // vector modes an opcode is driven in: 3 = {32,16,8}, 2 = {16,8}, 1 = {32}
  function automatic int vm_choices(input logic [6:0] o);
    if (is_addfam(o) || is_veccmp(o) || is_minmax(o) || is_shift(o) || o == OP_ABS) return 3;
    if (is_shuffle(o)) return 2;
    return 1;
  endfunction

  // ---- deterministic pseudo-random source (xorshift32, fixed seed) ----------------------------------
  logic [31:0] rs;
  function automatic logic [31:0] xs_next(input logic [31:0] s);
    logic [31:0] x = s;
    x = x ^ (x << 13); x = x ^ (x >> 17); x = x ^ (x << 5);
    return x;
  endfunction
  task automatic draw(output logic [31:0] v);   // advance the generator, return the new state
    rs = xs_next(rs); v = rs;
  endtask
  localparam logic [31:0] CORNERS [0:29] = '{
      32'h00000000, 32'h00000001, 32'h00000002, 32'h00000003, 32'h00000005,
      32'h7FFFFFFF, 32'h80000000, 32'hFFFFFFFF, 32'hFFFFFFFE, 32'h80000001,
      32'h0000FFFF, 32'hFFFF0000, 32'h00010000, 32'h00008000, 32'h00007FFF,
      32'hAAAAAAAA, 32'h55555555, 32'h00FF00FF, 32'hFF00FF00, 32'h01010101,
      32'h7F7F7F7F, 32'h80808080, 32'hFF7F80FE, 32'h0000007F, 32'h00000080,
      32'hDEADBEEF, 32'h12345678, 32'hCAFEF00D, 32'h00000010, 32'hFFFFFF80};
  localparam logic [7:0] LANE_BYTES [0:9] = '{8'h00, 8'h01, 8'h7F, 8'h80, 8'hFF, 8'hFE, 8'h55, 8'hAA, 8'h02, 8'h81};
  // operand distribution of the stimulus generator: raw, corner, byte-lane patterned, small, small negative
  function automatic logic [31:0] rnd32();
    logic [31:0] m, v, k;
    draw(m); m = m % 100;
    draw(v);
    if (m < 35) return v;
    if (m < 55) return CORNERS[v % 30];
    if (m < 75) begin
      for (int i = 0; i < 4; i++) begin draw(k); v[i*8 +: 8] = LANE_BYTES[k % 10]; end
      return v;
    end
    if (m < 90) return v % 256;
    return -(v % 256);
  endfunction
  function automatic logic [31:0] shift_amt32();
    logic [31:0] m, v;
    draw(m); m = m % 10;
    draw(v);
    case (m)
      0: return 32'd0;  1: return 32'd1;  2: return 32'd7;  3: return 32'd8;  4: return 32'd15;  5: return 32'd16;
      6: return 32'd30; 7: return 32'd31; 8: return v % 32;
      default: return v;                          // amounts beyond 31: only the low bits count per lane
    endcase
  endfunction
  // a value of exactly `bits` significant bits (1..32), random below the leading one
  function automatic logic [31:0] with_bits(input int bits);
    logic [31:0] lead = 32'h1 << (bits - 1);
    logic [31:0] low;
    draw(low);
    return lead | (low & (lead - 1));
  endfunction

  // ---- bookkeeping --------------------------------------------------------------------------------
  int total_checks, total_errors, shown, total_cycles, div_cycles;
  bit aborted;

  task automatic note_error(input string kind, input string src, input int idx, input string detail);
    total_errors++;
    if (shown < MAX_SHOWN) begin
      shown++;
      $display("%s %s#%0d op=%02h vm=%0h bma=%02h bmb=%02h ive=%0h cpx=%0h sbr=%0h csh=%0h a=%08h b=%08h c=%08h : %s",
               kind, src, idx, op, vm, bma, bmb, ive, cpx, sbr, csh, a, b, c, detail);
    end
    if (total_errors >= 1000 && !aborted) begin
      aborted = 1'b1;
      $display("TB_ABORT: 1000 errors, the remaining stimulus is skipped");
    end
  endtask

  // apply the operation held in the input signals; compare the handshake every cycle and the outputs when
  // the reference is ready. have_exp: also check the reference against a recorded expectation (phase 1).
  task automatic apply_op(input string src, input int idx, input bit have_exp, input logic [31:0] exp_res, input logic exp_c);
    int cycles; bit lat_bad; string d;
    @(negedge clk);
    if (ready_o !== ready_ref) begin
      d = $sformatf("ready_o=%b in the idle cycle, reference %b", ready_o, ready_ref);
      note_error("TB_EQUIV_ERROR", src, idx, d);
    end
    enable_i = 1'b1;
    cycles = 0; lat_bad = 1'b0;
    do begin
      @(negedge clk);
      cycles++;
      if (!lat_bad && ready_o !== ready_ref) begin
        lat_bad = 1'b1;
        d = $sformatf("ready_o=%b at poll cycle %0d, reference %b (handshake timing differs)", ready_o, cycles, ready_ref);
        note_error("TB_EQUIV_ERROR", src, idx, d);
      end
    end while (ready_ref !== 1'b1 && cycles < 64);
    total_cycles += cycles;
    if (is_div(op)) div_cycles += cycles;
    total_checks++;
    if (ready_ref !== 1'b1) begin
      note_error("TB_HARNESS_ERROR", src, idx, "the reference did not assert ready_o within 64 cycles");
    end else begin
      if (result_o !== result_ref || (is_cmp(op) && cmp_o !== cmp_ref)) begin
        d = $sformatf("expected result=%08h cmp=%s got result=%08h cmp=%b", result_ref,
                      is_cmp(op) ? $sformatf("%b", cmp_ref) : "x", result_o, cmp_o);
        note_error("TB_ERROR", src, idx, d);
      end
      if (have_exp && (result_ref !== exp_res || (is_cmp(op) && cmp_ref !== exp_c))) begin
        d = $sformatf("reference result=%08h cmp=%b differs from the recorded expectation %08h cmp=%b", result_ref, cmp_ref, exp_res, exp_c);
        note_error("TB_HARNESS_ERROR", src, idx, d);
      end
    end
    enable_i = 1'b0;
  endtask

  // one random operation within the driven envelope
  task automatic random_op();
    logic [31:0] r0, r1, m;
    logic [6:0] o;
    int nvm;
    draw(r0); r0 = r0 % 57; o = OPS[r0]; nvm = vm_choices(o);
    op = o;
    draw(r1); r1 = r1 % 3;
    vm = (nvm == 1 || (nvm == 3 && r1 == 0)) ? 2'b00 : (r1 == 1) ? 2'b10 : 2'b11;
    bma = '0; bmb = '0; ive = '0; cpx = 1'b0; sbr = 1'b0; csh = '0;
    a = rnd32(); b = rnd32(); c = rnd32();
    if (is_addfam(o)) begin
      draw(m); m = m % 100;
      if (is_rounding(o) || m < 40) begin draw(r0); bma = r0[4:0]; draw(r1); bmb = r1[4:0]; end
      if (o == OP_SUB && vm == 2'b10 && m >= 40 && m < 55) sbr = 1'b1;                       // pv.subrotmj
      else if (!is_rounding(o) && vm != 2'b11 && m >= 55 && m < 70) begin cpx = 1'b1; draw(r0); csh = r0[1:0]; bma = '0; bmb = '0; end
    end
    if (is_bitmanip(o)) begin
      draw(r0); bma = r0[4:0]; draw(r1); bmb = r1[4:0];
      draw(m); m = m % 10;
      if (o != 7'h2b && o != 7'h2c && o != 7'h49 && m < 7) b = {27'd0, bmb};                 // BEXT/BEXTU/BINS: b = bmb as the decoder does
      if (o == 7'h49) begin draw(r0); r0 = r0 % 32; b = (m < 5) ? r0 : {27'd0, bmb}; end              // BREV: left shift by b[4:0]
    end
    if (is_shift(o) && vm == 2'b00) b = shift_amt32();
    if (o == OP_EXT || o == OP_EXTS || o == OP_INS) begin draw(r0); ive = r0[1:0]; end
    if (o == OP_EXT || o == OP_EXTS) c = '0;
    if (o == OP_ABS) begin b = '0; draw(m); m = m % 5; if (vm == 2'b10 && m == 0) cpx = 1'b1; end   // complex conjugate on 16-bit lanes
    if (o == 7'h16 || o == 7'h17) begin draw(m); draw(r0); r0 = r0 % 32; if (m[0] == 1'b0) b = (32'h1 << r0) - 1; end   // CLIP bounds 2^k-1
    if (is_div(o)) begin
      draw(m); m = m % 10;
      if (m < 4) begin draw(r0); r0 = 1 + r0 % 32; a = with_bits(int'(r0)); end                   // divisor of any bit length
      else if (m < 6) begin draw(a); a = a % 64; end                                              // small divisor, long division
      else if (m < 7) begin draw(r0); draw(r1); b = a * (r0 % 16) + r1 % 8; end                   // near-exact multiple
      if (is_sdiv(o)) begin draw(r0); draw(r1); if (r0[0]) a = -a; if (r1[0]) b = -b; end
    end
  endtask

  // ---- stimulus ----------------------------------------------------------------------------------
  int fd, n, vidx, k, kb, mb, pass;
  logic [31:0] r0, r1;
  logic [3:0]  cchk, exp_cmp;
  logic [31:0] exp_result;
  string line;

  initial begin
    total_checks = 0; total_errors = 0; shown = 0; total_cycles = 0; div_cycles = 0; aborted = 1'b0; rs = 32'h2545F491;
    rst_n = 1'b0; enable_i = 1'b0; ex_ready_i = 1'b1;
    op = '0; a = '0; b = '0; c = '0; vm = '0; bma = '0; bmb = '0; ive = '0; cpx = 1'b0; sbr = 1'b0; csh = '0;
    repeat (5) @(negedge clk);
    rst_n = 1'b1;
    @(negedge clk);

    // 1. the vector file
    fd = $fopen("vectors.dat", "r");
    if (fd == 0) $fatal(1, "cannot open vectors.dat");
    vidx = 0;
    while (!$feof(fd) && !aborted) begin
      if ($fgets(line, fd) == 0) break;
      if (line.len() == 0 || line[0] == "#" || line[0] == "\n") continue;
      n = $sscanf(line, "%h %h %h %h %h %h %h %h %h %h %h %h %h %h",
                  op, vm, bma, bmb, ive, cpx, sbr, csh, a, b, c, cchk, exp_result, exp_cmp);
      if (n != 14) continue;
      apply_op("file", vidx, 1'b1, exp_result, exp_cmp[0]);
      vidx++;
    end
    $fclose(fd);

    // 2. the divider sweep: divisor bits x dividend bits x opcode, two passes
    vidx = 0;
    for (pass = 0; pass < 2 && !aborted; pass++)
      for (k = 53; k < 57 && !aborted; k++)
        for (kb = 1; kb <= 32 && !aborted; kb++)
          for (mb = 1; mb <= 32 && !aborted; mb++) begin
            op = OPS[k]; vm = '0; bma = '0; bmb = '0; ive = '0; cpx = 1'b0; sbr = 1'b0; csh = '0; c = '0;
            a = with_bits(kb);                        // the divisor (operand_a_i)
            b = with_bits(mb);                        // the dividend (operand_b_i)
            if (is_sdiv(op)) begin draw(r0); draw(r1); if (r0[0]) a = -a; if (r1[0]) b = -b; end
            apply_op("sweep", vidx, 1'b0, '0, 1'b0);
            vidx++;
          end

    // 3. random operations within the envelope
    for (vidx = 0; vidx < N_RANDOM && !aborted; vidx++) begin
      random_op();
      apply_op("random", vidx, 1'b0, '0, 1'b0);
    end

    $display("TB_CYCLES total=%0d", total_cycles);
    $display("TB_DIV_CYCLES total=%0d", div_cycles);
    $display("TB_SUMMARY total=%0d errors=%0d", total_checks, total_errors);
    if (total_errors != 0) $fatal(1, "FAIL");
    $display("PASS");
    $finish;
  end
endmodule

// ---- embedded reference: the upstream CV32E40P ALU of starting_point.sv (flattened, see its header and
// _debug/ in the benchmark source), every module renamed with the suffix __ref. Its lint findings are
// upstream's and silenced here so that the lint report shows the design's only ----
/* verilator lint_off WIDTHTRUNC */
/* verilator lint_off CASEINCOMPLETE */
/* verilator lint_off UNUSEDPARAM */

// Copyright 2018 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License.  You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

////////////////////////////////////////////////////////////////////////////////
// Engineer:       Matthias Baer - baermatt@student.ethz.ch                   //
//                                                                            //
// Additional contributions by:                                               //
//                 Igor Loi - igor.loi@unibo.it                               //
//                 Andreas Traber - atraber@student.ethz.ch                   //
//                 Michael Gautschi - gautschi@iis.ee.ethz.ch                 //
//                 Davide Schiavone - pschiavo@iis.ee.ethz.ch                 //
//                                                                            //
// Design Name:    ALU                                                        //
// Project Name:   RI5CY                                                      //
// Language:       SystemVerilog                                              //
//                                                                            //
// Description:    Arithmetic logic unit of the pipelined processor           //
//                                                                            //
////////////////////////////////////////////////////////////////////////////////

module cv32e40p_alu__ref
(
    input logic               clk,
    input logic               rst_n,
    input logic               enable_i,
    input logic [ 6:0] operator_i,
    input logic        [31:0] operand_a_i,
    input logic        [31:0] operand_b_i,
    input logic        [31:0] operand_c_i,

    input logic [1:0] vector_mode_i,
    input logic [4:0] bmask_a_i,
    input logic [4:0] bmask_b_i,
    input logic [1:0] imm_vec_ext_i,

    input logic       is_clpx_i,
    input logic       is_subrot_i,
    input logic [1:0] clpx_shift_i,

    output logic [31:0] result_o,
    output logic        comparison_result_o,

    output logic ready_o,
    input  logic ex_ready_i
);

  // ---- constants inlined from cv32e40p_pkg.sv (mechanical flatten) ----
  localparam logic [6:0] ALU_ADD = 7'b0011000;
  localparam logic [6:0] ALU_SUB = 7'b0011001;
  localparam logic [6:0] ALU_ADDU = 7'b0011010;
  localparam logic [6:0] ALU_SUBU = 7'b0011011;
  localparam logic [6:0] ALU_ADDR = 7'b0011100;
  localparam logic [6:0] ALU_SUBR = 7'b0011101;
  localparam logic [6:0] ALU_ADDUR = 7'b0011110;
  localparam logic [6:0] ALU_SUBUR = 7'b0011111;
  localparam logic [6:0] ALU_XOR = 7'b0101111;
  localparam logic [6:0] ALU_OR = 7'b0101110;
  localparam logic [6:0] ALU_AND = 7'b0010101;
  localparam logic [6:0] ALU_SRA = 7'b0100100;
  localparam logic [6:0] ALU_SRL = 7'b0100101;
  localparam logic [6:0] ALU_ROR = 7'b0100110;
  localparam logic [6:0] ALU_SLL = 7'b0100111;
  localparam logic [6:0] ALU_BEXT = 7'b0101000;
  localparam logic [6:0] ALU_BEXTU = 7'b0101001;
  localparam logic [6:0] ALU_BINS = 7'b0101010;
  localparam logic [6:0] ALU_BCLR = 7'b0101011;
  localparam logic [6:0] ALU_BSET = 7'b0101100;
  localparam logic [6:0] ALU_BREV = 7'b1001001;
  localparam logic [6:0] ALU_FF1 = 7'b0110110;
  localparam logic [6:0] ALU_FL1 = 7'b0110111;
  localparam logic [6:0] ALU_CNT = 7'b0110100;
  localparam logic [6:0] ALU_CLB = 7'b0110101;
  localparam logic [6:0] ALU_EXTS = 7'b0111110;
  localparam logic [6:0] ALU_EXT = 7'b0111111;
  localparam logic [6:0] ALU_LTS = 7'b0000000;
  localparam logic [6:0] ALU_LTU = 7'b0000001;
  localparam logic [6:0] ALU_LES = 7'b0000100;
  localparam logic [6:0] ALU_LEU = 7'b0000101;
  localparam logic [6:0] ALU_GTS = 7'b0001000;
  localparam logic [6:0] ALU_GTU = 7'b0001001;
  localparam logic [6:0] ALU_GES = 7'b0001010;
  localparam logic [6:0] ALU_GEU = 7'b0001011;
  localparam logic [6:0] ALU_EQ = 7'b0001100;
  localparam logic [6:0] ALU_NE = 7'b0001101;
  localparam logic [6:0] ALU_SLTS = 7'b0000010;
  localparam logic [6:0] ALU_SLTU = 7'b0000011;
  localparam logic [6:0] ALU_SLETS = 7'b0000110;
  localparam logic [6:0] ALU_SLETU = 7'b0000111;
  localparam logic [6:0] ALU_ABS = 7'b0010100;
  localparam logic [6:0] ALU_CLIP = 7'b0010110;
  localparam logic [6:0] ALU_CLIPU = 7'b0010111;
  localparam logic [6:0] ALU_INS = 7'b0101101;
  localparam logic [6:0] ALU_MIN = 7'b0010000;
  localparam logic [6:0] ALU_MINU = 7'b0010001;
  localparam logic [6:0] ALU_MAX = 7'b0010010;
  localparam logic [6:0] ALU_MAXU = 7'b0010011;
  localparam logic [6:0] ALU_DIVU = 7'b0110000;
  localparam logic [6:0] ALU_DIV = 7'b0110001;
  localparam logic [6:0] ALU_REMU = 7'b0110010;
  localparam logic [6:0] ALU_REM = 7'b0110011;
  localparam logic [6:0] ALU_SHUF = 7'b0111010;
  localparam logic [6:0] ALU_SHUF2 = 7'b0111011;
  localparam logic [6:0] ALU_PCKLO = 7'b0111000;
  localparam logic [6:0] ALU_PCKHI = 7'b0111001;
  localparam logic [1:0] VEC_MODE32 = 2'b00;
  localparam logic [1:0] VEC_MODE16 = 2'b10;
  localparam logic [1:0] VEC_MODE8 = 2'b11;

  logic [31:0] operand_a_rev;
  logic [31:0] operand_a_neg;
  logic [31:0] operand_a_neg_rev;

  assign operand_a_neg = ~operand_a_i;

  // bit reverse operand_a for left shifts and bit counting
  generate
    genvar k;
    for (k = 0; k < 32; k++) begin : gen_operand_a_rev
      assign operand_a_rev[k] = operand_a_i[31-k];
    end
  endgenerate

  // bit reverse operand_a_neg for left shifts and bit counting
  generate
    genvar m;
    for (m = 0; m < 32; m++) begin : gen_operand_a_neg_rev
      assign operand_a_neg_rev[m] = operand_a_neg[31-m];
    end
  endgenerate

  logic [31:0] operand_b_neg;

  assign operand_b_neg = ~operand_b_i;


  logic [ 5:0] div_shift;
  logic        div_valid;
  logic [31:0] bmask;

  //////////////////////////////////////////////////////////////////////////////////////////
  //   ____            _   _ _   _                      _      _       _     _            //
  //  |  _ \ __ _ _ __| |_(_) |_(_) ___  _ __   ___  __| |    / \   __| | __| | ___ _ __  //
  //  | |_) / _` | '__| __| | __| |/ _ \| '_ \ / _ \/ _` |   / _ \ / _` |/ _` |/ _ \ '__| //
  //  |  __/ (_| | |  | |_| | |_| | (_) | | | |  __/ (_| |  / ___ \ (_| | (_| |  __/ |    //
  //  |_|   \__,_|_|   \__|_|\__|_|\___/|_| |_|\___|\__,_| /_/   \_\__,_|\__,_|\___|_|    //
  //                                                                                      //
  //////////////////////////////////////////////////////////////////////////////////////////

  logic        adder_op_b_negate;
  logic [31:0] adder_op_a, adder_op_b;
  logic [35:0] adder_in_a, adder_in_b;
  logic [31:0] adder_result;
  logic [36:0] adder_result_expanded;


  assign adder_op_b_negate = (operator_i == ALU_SUB) || (operator_i == ALU_SUBR) ||
                             (operator_i == ALU_SUBU) || (operator_i == ALU_SUBUR) || is_subrot_i;

  // prepare operand a
  assign adder_op_a = (operator_i == ALU_ABS) ? operand_a_neg : (is_subrot_i ? {
    operand_b_i[15:0], operand_a_i[31:16]
  } : operand_a_i);

  // prepare operand b
  assign adder_op_b = adder_op_b_negate ? (is_subrot_i ? ~{
    operand_a_i[15:0], operand_b_i[31:16]
  } : operand_b_neg) : operand_b_i;

  // prepare carry
  always_comb begin
    adder_in_a[0]     = 1'b1;
    adder_in_a[8:1]   = adder_op_a[7:0];
    adder_in_a[9]     = 1'b1;
    adder_in_a[17:10] = adder_op_a[15:8];
    adder_in_a[18]    = 1'b1;
    adder_in_a[26:19] = adder_op_a[23:16];
    adder_in_a[27]    = 1'b1;
    adder_in_a[35:28] = adder_op_a[31:24];

    adder_in_b[0]     = 1'b0;
    adder_in_b[8:1]   = adder_op_b[7:0];
    adder_in_b[9]     = 1'b0;
    adder_in_b[17:10] = adder_op_b[15:8];
    adder_in_b[18]    = 1'b0;
    adder_in_b[26:19] = adder_op_b[23:16];
    adder_in_b[27]    = 1'b0;
    adder_in_b[35:28] = adder_op_b[31:24];

    if (adder_op_b_negate || (operator_i == ALU_ABS || operator_i == ALU_CLIP)) begin
      // special case for subtractions and absolute number calculations
      adder_in_b[0] = 1'b1;

      case (vector_mode_i)
        VEC_MODE16: begin
          adder_in_b[18] = 1'b1;
        end

        VEC_MODE8: begin
          adder_in_b[9]  = 1'b1;
          adder_in_b[18] = 1'b1;
          adder_in_b[27] = 1'b1;
        end
      endcase

    end else begin
      // take care of partitioning the adder for the addition case
      case (vector_mode_i)
        VEC_MODE16: begin
          adder_in_a[18] = 1'b0;
        end

        VEC_MODE8: begin
          adder_in_a[9]  = 1'b0;
          adder_in_a[18] = 1'b0;
          adder_in_a[27] = 1'b0;
        end
      endcase
    end
  end

  // actual adder
  assign adder_result_expanded = $signed(adder_in_a) + $signed(adder_in_b);
  assign adder_result = {
    adder_result_expanded[35:28],
    adder_result_expanded[26:19],
    adder_result_expanded[17:10],
    adder_result_expanded[8:1]
  };


  // normalization stage
  logic [31:0] adder_round_value;
  logic [31:0] adder_round_result;

  assign adder_round_value  = ((operator_i == ALU_ADDR) || (operator_i == ALU_SUBR) ||
                               (operator_i == ALU_ADDUR) || (operator_i == ALU_SUBUR)) ?
                                {
    1'b0, bmask[31:1]
  } : '0;
  assign adder_round_result = adder_result + adder_round_value;


  ////////////////////////////////////////
  //  ____  _   _ ___ _____ _____       //
  // / ___|| | | |_ _|  ___|_   _|      //
  // \___ \| |_| || || |_    | |        //
  //  ___) |  _  || ||  _|   | |        //
  // |____/|_| |_|___|_|     |_|        //
  //                                    //
  ////////////////////////////////////////

  logic        shift_left;  // should we shift left
  logic        shift_use_round;
  logic        shift_arithmetic;

  logic [31:0] shift_amt_left;  // amount of shift, if to the left
  logic [31:0] shift_amt;  // amount of shift, to the right
  logic [31:0] shift_amt_int;  // amount of shift, used for the actual shifters
  logic [31:0] shift_amt_norm;  // amount of shift, used for normalization
  logic [31:0] shift_op_a;  // input of the shifter
  logic [31:0] shift_result;
  logic [31:0] shift_right_result;
  logic [31:0] shift_left_result;
  logic [15:0] clpx_shift_ex;

  // shifter is also used for preparing operand for division
  assign shift_amt = div_valid ? div_shift : operand_b_i;

  // by reversing the bits of the input, we also have to reverse the order of shift amounts
  always_comb begin
    case (vector_mode_i)
      VEC_MODE16: begin
        shift_amt_left[15:0]  = shift_amt[31:16];
        shift_amt_left[31:16] = shift_amt[15:0];
      end

      VEC_MODE8: begin
        shift_amt_left[7:0]   = shift_amt[31:24];
        shift_amt_left[15:8]  = shift_amt[23:16];
        shift_amt_left[23:16] = shift_amt[15:8];
        shift_amt_left[31:24] = shift_amt[7:0];
      end

      default: // VEC_MODE32
      begin
        shift_amt_left[31:0] = shift_amt[31:0];
      end
    endcase
  end

  // ALU_FL1 and ALU_CBL are used for the bit counting ops later
  assign shift_left = (operator_i == ALU_SLL) || (operator_i == ALU_BINS) ||
                      (operator_i == ALU_FL1) || (operator_i == ALU_CLB)  ||
                      (operator_i == ALU_DIV) || (operator_i == ALU_DIVU) ||
                      (operator_i == ALU_REM) || (operator_i == ALU_REMU) ||
                      (operator_i == ALU_BREV);

  assign shift_use_round = (operator_i == ALU_ADD)   || (operator_i == ALU_SUB)   ||
                           (operator_i == ALU_ADDR)  || (operator_i == ALU_SUBR)  ||
                           (operator_i == ALU_ADDU)  || (operator_i == ALU_SUBU)  ||
                           (operator_i == ALU_ADDUR) || (operator_i == ALU_SUBUR);

  assign shift_arithmetic = (operator_i == ALU_SRA)  || (operator_i == ALU_BEXT) ||
                            (operator_i == ALU_ADD)  || (operator_i == ALU_SUB)  ||
                            (operator_i == ALU_ADDR) || (operator_i == ALU_SUBR);

  // choose the bit reversed or the normal input for shift operand a
  assign shift_op_a    = shift_left ? operand_a_rev :
                          (shift_use_round ? adder_round_result : operand_a_i);
  assign shift_amt_int = shift_use_round ? shift_amt_norm :
                          (shift_left ? shift_amt_left : shift_amt);

  assign shift_amt_norm = is_clpx_i ? {clpx_shift_ex, clpx_shift_ex} : {4{3'b000, bmask_b_i}};

  assign clpx_shift_ex = $unsigned(clpx_shift_i);

  // right shifts, we let the synthesizer optimize this
  logic [63:0] shift_op_a_32;

  assign shift_op_a_32 = (operator_i == ALU_ROR) ? {
        shift_op_a, shift_op_a
      } : $signed(
          {{32{shift_arithmetic & shift_op_a[31]}}, shift_op_a}
      );

  always_comb begin
    case (vector_mode_i)
      VEC_MODE16: begin
        shift_right_result[31:16] = $signed(
            {shift_arithmetic & shift_op_a[31], shift_op_a[31:16]}
        ) >>> shift_amt_int[19:16];
        shift_right_result[15:0] = $signed(
            {shift_arithmetic & shift_op_a[15], shift_op_a[15:0]}
        ) >>> shift_amt_int[3:0];
      end

      VEC_MODE8: begin
        shift_right_result[31:24] = $signed(
            {shift_arithmetic & shift_op_a[31], shift_op_a[31:24]}
        ) >>> shift_amt_int[26:24];
        shift_right_result[23:16] = $signed(
            {shift_arithmetic & shift_op_a[23], shift_op_a[23:16]}
        ) >>> shift_amt_int[18:16];
        shift_right_result[15:8] = $signed(
            {shift_arithmetic & shift_op_a[15], shift_op_a[15:8]}
        ) >>> shift_amt_int[10:8];
        shift_right_result[7:0] = $signed(
            {shift_arithmetic & shift_op_a[7], shift_op_a[7:0]}
        ) >>> shift_amt_int[2:0];
      end

      default: // VEC_MODE32
      begin
        shift_right_result = shift_op_a_32 >> shift_amt_int[4:0];
      end
    endcase
    ;  // case (vec_mode_i)
  end

  // bit reverse the shift_right_result for left shifts
  genvar j;
  generate
    for (j = 0; j < 32; j++) begin : gen_shift_left_result
      assign shift_left_result[j] = shift_right_result[31-j];
    end
  endgenerate

  assign shift_result = shift_left ? shift_left_result : shift_right_result;


  //////////////////////////////////////////////////////////////////
  //   ____ ___  __  __ ____   _    ____  ___ ____   ___  _   _   //
  //  / ___/ _ \|  \/  |  _ \ / \  |  _ \|_ _/ ___| / _ \| \ | |  //
  // | |  | | | | |\/| | |_) / _ \ | |_) || |\___ \| | | |  \| |  //
  // | |__| |_| | |  | |  __/ ___ \|  _ < | | ___) | |_| | |\  |  //
  //  \____\___/|_|  |_|_| /_/   \_\_| \_\___|____/ \___/|_| \_|  //
  //                                                              //
  //////////////////////////////////////////////////////////////////

  logic [ 3:0] is_equal;
  logic [ 3:0] is_greater;  // handles both signed and unsigned forms

  // 8-bit vector comparisons, basic building blocks
  logic [ 3:0] cmp_signed;
  logic [ 3:0] is_equal_vec;
  logic [ 3:0] is_greater_vec;
  logic [31:0] operand_b_eq;
  logic        is_equal_clip;


  //second == comparator for CLIP instructions
  always_comb begin
    operand_b_eq = operand_b_neg;
    if (operator_i == ALU_CLIPU) operand_b_eq = '0;
    else operand_b_eq = operand_b_neg;
  end
  assign is_equal_clip = operand_a_i == operand_b_eq;

  always_comb begin
    cmp_signed = 4'b0;

    unique case (operator_i)
      ALU_GTS,
      ALU_GES,
      ALU_LTS,
      ALU_LES,
      ALU_SLTS,
      ALU_SLETS,
      ALU_MIN,
      ALU_MAX,
      ALU_ABS,
      ALU_CLIP,
      ALU_CLIPU: begin
        case (vector_mode_i)
          VEC_MODE8:  cmp_signed[3:0] = 4'b1111;
          VEC_MODE16: cmp_signed[3:0] = 4'b1010;
          default:    cmp_signed[3:0] = 4'b1000;
        endcase
      end

      default: ;
    endcase
  end

  // generate vector equal and greater than signals, cmp_signed decides if the
  // comparison is done signed or unsigned
  genvar i;
  generate
    for (i = 0; i < 4; i++) begin : gen_is_vec
      assign is_equal_vec[i] = (operand_a_i[8*i+7:8*i] == operand_b_i[8*i+7:i*8]);
      assign is_greater_vec[i] = $signed(
          {operand_a_i[8*i+7] & cmp_signed[i], operand_a_i[8*i+7:8*i]}
      ) > $signed(
          {operand_b_i[8*i+7] & cmp_signed[i], operand_b_i[8*i+7:i*8]}
      );
    end
  endgenerate

  // generate the real equal and greater than signals that take the vector
  // mode into account
  always_comb begin
    // 32-bit mode
    is_equal[3:0] = {4{is_equal_vec[3] & is_equal_vec[2] & is_equal_vec[1] & is_equal_vec[0]}};
    is_greater[3:0] = {4{is_greater_vec[3] | (is_equal_vec[3] & (is_greater_vec[2]
                                            | (is_equal_vec[2] & (is_greater_vec[1]
                                             | (is_equal_vec[1] & (is_greater_vec[0]))))))}};

    case (vector_mode_i)
      VEC_MODE16: begin
        is_equal[1:0]   = {2{is_equal_vec[0] & is_equal_vec[1]}};
        is_equal[3:2]   = {2{is_equal_vec[2] & is_equal_vec[3]}};
        is_greater[1:0] = {2{is_greater_vec[1] | (is_equal_vec[1] & is_greater_vec[0])}};
        is_greater[3:2] = {2{is_greater_vec[3] | (is_equal_vec[3] & is_greater_vec[2])}};
      end

      VEC_MODE8: begin
        is_equal[3:0]   = is_equal_vec[3:0];
        is_greater[3:0] = is_greater_vec[3:0];
      end

      default: ;  // see default assignment
    endcase
  end

  // generate comparison result
  logic [3:0] cmp_result;

  always_comb begin
    cmp_result = is_equal;
    unique case (operator_i)
      ALU_EQ:                                 cmp_result = is_equal;
      ALU_NE:                                 cmp_result = ~is_equal;
      ALU_GTS, ALU_GTU:                       cmp_result = is_greater;
      ALU_GES, ALU_GEU:                       cmp_result = is_greater | is_equal;
      ALU_LTS, ALU_SLTS, ALU_LTU, ALU_SLTU:   cmp_result = ~(is_greater | is_equal);
      ALU_SLETS, ALU_SLETU, ALU_LES, ALU_LEU: cmp_result = ~is_greater;
      default:                                ;
    endcase
  end

  assign comparison_result_o = cmp_result[3];


  // min/max/abs handling
  logic [31:0] result_minmax;
  logic [ 3:0] sel_minmax;
  logic        do_min;
  logic [31:0] minmax_b;

  assign minmax_b = (operator_i == ALU_ABS) ? adder_result : operand_b_i;

  assign do_min   = (operator_i == ALU_MIN)  || (operator_i == ALU_MINU) ||
                    (operator_i == ALU_CLIP) || (operator_i == ALU_CLIPU);

  assign sel_minmax[3:0] = is_greater ^ {4{do_min}};

  assign result_minmax[31:24] = (sel_minmax[3] == 1'b1) ? operand_a_i[31:24] : minmax_b[31:24];
  assign result_minmax[23:16] = (sel_minmax[2] == 1'b1) ? operand_a_i[23:16] : minmax_b[23:16];
  assign result_minmax[15:8] = (sel_minmax[1] == 1'b1) ? operand_a_i[15:8] : minmax_b[15:8];
  assign result_minmax[7:0] = (sel_minmax[0] == 1'b1) ? operand_a_i[7:0] : minmax_b[7:0];

  //////////////////////////////////////////////////
  // Clip
  //////////////////////////////////////////////////
  logic [31:0] clip_result;  // result of clip and clip

  always_comb begin
    clip_result = result_minmax;
    if (operator_i == ALU_CLIPU) begin
      if (operand_a_i[31] || is_equal_clip) begin
        clip_result = '0;
      end else begin
        clip_result = result_minmax;
      end
    end else begin
      //CLIP
      if (adder_result_expanded[36] || is_equal_clip) begin
        clip_result = operand_b_neg;
      end else begin
        clip_result = result_minmax;
      end
    end

  end

  //////////////////////////////////////////////////
  //  ____  _   _ _   _ _____ _____ _     _____   //
  // / ___|| | | | | | |  ___|  ___| |   | ____|  //
  // \___ \| |_| | | | | |_  | |_  | |   |  _|    //
  //  ___) |  _  | |_| |  _| |  _| | |___| |___   //
  // |____/|_| |_|\___/|_|   |_|   |_____|_____|  //
  //                                              //
  //////////////////////////////////////////////////

  logic [3:0][1:0] shuffle_byte_sel;  // select byte in register: 31:24, 23:16, 15:8, 7:0
  logic [3:0]      shuffle_reg_sel;  // select register: rD/rS2 or rS1
  logic [1:0]      shuffle_reg1_sel;  // select register rD or rS2 for next stage
  logic [1:0]      shuffle_reg0_sel;
  logic [3:0]      shuffle_through;

  logic [31:0] shuffle_r1, shuffle_r0;
  logic [31:0] shuffle_r1_in, shuffle_r0_in;
  logic [31:0] shuffle_result;
  logic [31:0] pack_result;


  always_comb begin
    shuffle_reg_sel  = '0;
    shuffle_reg1_sel = 2'b01;
    shuffle_reg0_sel = 2'b10;
    shuffle_through  = '1;

    unique case (operator_i)
      ALU_EXT, ALU_EXTS: begin
        if (operator_i == ALU_EXTS) shuffle_reg1_sel = 2'b11;

        if (vector_mode_i == VEC_MODE8) begin
          shuffle_reg_sel[3:1] = 3'b111;
          shuffle_reg_sel[0]   = 1'b0;
        end else begin
          shuffle_reg_sel[3:2] = 2'b11;
          shuffle_reg_sel[1:0] = 2'b00;
        end
      end

      ALU_PCKLO: begin
        shuffle_reg1_sel = 2'b00;

        if (vector_mode_i == VEC_MODE8) begin
          shuffle_through = 4'b0011;
          shuffle_reg_sel = 4'b0001;
        end else begin
          shuffle_reg_sel = 4'b0011;
        end
      end

      ALU_PCKHI: begin
        shuffle_reg1_sel = 2'b00;

        if (vector_mode_i == VEC_MODE8) begin
          shuffle_through = 4'b1100;
          shuffle_reg_sel = 4'b0100;
        end else begin
          shuffle_reg_sel = 4'b0011;
        end
      end

      ALU_SHUF2: begin
        unique case (vector_mode_i)
          VEC_MODE8: begin
            shuffle_reg_sel[3] = ~operand_b_i[26];
            shuffle_reg_sel[2] = ~operand_b_i[18];
            shuffle_reg_sel[1] = ~operand_b_i[10];
            shuffle_reg_sel[0] = ~operand_b_i[2];
          end

          VEC_MODE16: begin
            shuffle_reg_sel[3] = ~operand_b_i[17];
            shuffle_reg_sel[2] = ~operand_b_i[17];
            shuffle_reg_sel[1] = ~operand_b_i[1];
            shuffle_reg_sel[0] = ~operand_b_i[1];
          end
          default: ;
        endcase
      end

      ALU_INS: begin
        unique case (vector_mode_i)
          VEC_MODE8: begin
            shuffle_reg0_sel = 2'b00;
            unique case (imm_vec_ext_i)
              2'b00: begin
                shuffle_reg_sel[3:0] = 4'b1110;
              end
              2'b01: begin
                shuffle_reg_sel[3:0] = 4'b1101;
              end
              2'b10: begin
                shuffle_reg_sel[3:0] = 4'b1011;
              end
              2'b11: begin
                shuffle_reg_sel[3:0] = 4'b0111;
              end
            endcase
          end
          VEC_MODE16: begin
            shuffle_reg0_sel   = 2'b01;
            shuffle_reg_sel[3] = ~imm_vec_ext_i[0];
            shuffle_reg_sel[2] = ~imm_vec_ext_i[0];
            shuffle_reg_sel[1] = imm_vec_ext_i[0];
            shuffle_reg_sel[0] = imm_vec_ext_i[0];
          end
          default: ;
        endcase
      end

      default: ;
    endcase
  end

  always_comb begin
    shuffle_byte_sel = '0;

    // byte selector
    unique case (operator_i)
      ALU_EXTS, ALU_EXT: begin
        unique case (vector_mode_i)
          VEC_MODE8: begin
            shuffle_byte_sel[3] = imm_vec_ext_i[1:0];
            shuffle_byte_sel[2] = imm_vec_ext_i[1:0];
            shuffle_byte_sel[1] = imm_vec_ext_i[1:0];
            shuffle_byte_sel[0] = imm_vec_ext_i[1:0];
          end

          VEC_MODE16: begin
            shuffle_byte_sel[3] = {imm_vec_ext_i[0], 1'b1};
            shuffle_byte_sel[2] = {imm_vec_ext_i[0], 1'b1};
            shuffle_byte_sel[1] = {imm_vec_ext_i[0], 1'b1};
            shuffle_byte_sel[0] = {imm_vec_ext_i[0], 1'b0};
          end

          default: ;
        endcase
      end

      ALU_PCKLO: begin
        unique case (vector_mode_i)
          VEC_MODE8: begin
            shuffle_byte_sel[3] = 2'b00;
            shuffle_byte_sel[2] = 2'b00;
            shuffle_byte_sel[1] = 2'b00;
            shuffle_byte_sel[0] = 2'b00;
          end

          VEC_MODE16: begin
            shuffle_byte_sel[3] = 2'b01;
            shuffle_byte_sel[2] = 2'b00;
            shuffle_byte_sel[1] = 2'b01;
            shuffle_byte_sel[0] = 2'b00;
          end

          default: ;
        endcase
      end

      ALU_PCKHI: begin
        unique case (vector_mode_i)
          VEC_MODE8: begin
            shuffle_byte_sel[3] = 2'b00;
            shuffle_byte_sel[2] = 2'b00;
            shuffle_byte_sel[1] = 2'b00;
            shuffle_byte_sel[0] = 2'b00;
          end

          VEC_MODE16: begin
            shuffle_byte_sel[3] = 2'b11;
            shuffle_byte_sel[2] = 2'b10;
            shuffle_byte_sel[1] = 2'b11;
            shuffle_byte_sel[0] = 2'b10;
          end

          default: ;
        endcase
      end

      ALU_SHUF2, ALU_SHUF: begin
        unique case (vector_mode_i)
          VEC_MODE8: begin
            shuffle_byte_sel[3] = operand_b_i[25:24];
            shuffle_byte_sel[2] = operand_b_i[17:16];
            shuffle_byte_sel[1] = operand_b_i[9:8];
            shuffle_byte_sel[0] = operand_b_i[1:0];
          end

          VEC_MODE16: begin
            shuffle_byte_sel[3] = {operand_b_i[16], 1'b1};
            shuffle_byte_sel[2] = {operand_b_i[16], 1'b0};
            shuffle_byte_sel[1] = {operand_b_i[0], 1'b1};
            shuffle_byte_sel[0] = {operand_b_i[0], 1'b0};
          end
          default: ;
        endcase
      end

      ALU_INS: begin
        shuffle_byte_sel[3] = 2'b11;
        shuffle_byte_sel[2] = 2'b10;
        shuffle_byte_sel[1] = 2'b01;
        shuffle_byte_sel[0] = 2'b00;
      end

      default: ;
    endcase
  end

  assign shuffle_r0_in = shuffle_reg0_sel[1] ?
                          operand_a_i :
                          (shuffle_reg0_sel[0] ? {2{operand_a_i[15:0]}} : {4{operand_a_i[7:0]}});

  assign shuffle_r1_in = shuffle_reg1_sel[1] ? {
    {8{operand_a_i[31]}}, {8{operand_a_i[23]}}, {8{operand_a_i[15]}}, {8{operand_a_i[7]}}
  } : (shuffle_reg1_sel[0] ? operand_c_i : operand_b_i);

  assign shuffle_r0[31:24] = shuffle_byte_sel[3][1] ?
                              (shuffle_byte_sel[3][0] ? shuffle_r0_in[31:24] : shuffle_r0_in[23:16]) :
                              (shuffle_byte_sel[3][0] ? shuffle_r0_in[15: 8] : shuffle_r0_in[ 7: 0]);
  assign shuffle_r0[23:16] = shuffle_byte_sel[2][1] ?
                              (shuffle_byte_sel[2][0] ? shuffle_r0_in[31:24] : shuffle_r0_in[23:16]) :
                              (shuffle_byte_sel[2][0] ? shuffle_r0_in[15: 8] : shuffle_r0_in[ 7: 0]);
  assign shuffle_r0[15: 8] = shuffle_byte_sel[1][1] ?
                              (shuffle_byte_sel[1][0] ? shuffle_r0_in[31:24] : shuffle_r0_in[23:16]) :
                              (shuffle_byte_sel[1][0] ? shuffle_r0_in[15: 8] : shuffle_r0_in[ 7: 0]);
  assign shuffle_r0[ 7: 0] = shuffle_byte_sel[0][1] ?
                              (shuffle_byte_sel[0][0] ? shuffle_r0_in[31:24] : shuffle_r0_in[23:16]) :
                              (shuffle_byte_sel[0][0] ? shuffle_r0_in[15: 8] : shuffle_r0_in[ 7: 0]);

  assign shuffle_r1[31:24] = shuffle_byte_sel[3][1] ?
                              (shuffle_byte_sel[3][0] ? shuffle_r1_in[31:24] : shuffle_r1_in[23:16]) :
                              (shuffle_byte_sel[3][0] ? shuffle_r1_in[15: 8] : shuffle_r1_in[ 7: 0]);
  assign shuffle_r1[23:16] = shuffle_byte_sel[2][1] ?
                              (shuffle_byte_sel[2][0] ? shuffle_r1_in[31:24] : shuffle_r1_in[23:16]) :
                              (shuffle_byte_sel[2][0] ? shuffle_r1_in[15: 8] : shuffle_r1_in[ 7: 0]);
  assign shuffle_r1[15: 8] = shuffle_byte_sel[1][1] ?
                              (shuffle_byte_sel[1][0] ? shuffle_r1_in[31:24] : shuffle_r1_in[23:16]) :
                              (shuffle_byte_sel[1][0] ? shuffle_r1_in[15: 8] : shuffle_r1_in[ 7: 0]);
  assign shuffle_r1[ 7: 0] = shuffle_byte_sel[0][1] ?
                              (shuffle_byte_sel[0][0] ? shuffle_r1_in[31:24] : shuffle_r1_in[23:16]) :
                              (shuffle_byte_sel[0][0] ? shuffle_r1_in[15: 8] : shuffle_r1_in[ 7: 0]);

  assign shuffle_result[31:24] = shuffle_reg_sel[3] ? shuffle_r1[31:24] : shuffle_r0[31:24];
  assign shuffle_result[23:16] = shuffle_reg_sel[2] ? shuffle_r1[23:16] : shuffle_r0[23:16];
  assign shuffle_result[15:8] = shuffle_reg_sel[1] ? shuffle_r1[15:8] : shuffle_r0[15:8];
  assign shuffle_result[7:0] = shuffle_reg_sel[0] ? shuffle_r1[7:0] : shuffle_r0[7:0];

  assign pack_result[31:24] = shuffle_through[3] ? shuffle_result[31:24] : operand_c_i[31:24];
  assign pack_result[23:16] = shuffle_through[2] ? shuffle_result[23:16] : operand_c_i[23:16];
  assign pack_result[15:8] = shuffle_through[1] ? shuffle_result[15:8] : operand_c_i[15:8];
  assign pack_result[7:0] = shuffle_through[0] ? shuffle_result[7:0] : operand_c_i[7:0];


  /////////////////////////////////////////////////////////////////////
  //   ____  _ _      ____                  _      ___               //
  //  | __ )(_) |_   / ___|___  _   _ _ __ | |_   / _ \ _ __  ___    //
  //  |  _ \| | __| | |   / _ \| | | | '_ \| __| | | | | '_ \/ __|   //
  //  | |_) | | |_  | |__| (_) | |_| | | | | |_  | |_| | |_) \__ \_  //
  //  |____/|_|\__|  \____\___/ \__,_|_| |_|\__|  \___/| .__/|___(_) //
  //                                                   |_|           //
  /////////////////////////////////////////////////////////////////////

  logic [31:0] ff_input;  // either op_a_i or its bit reversed version
  logic [ 5:0] cnt_result;  // population count
  logic [ 5:0] clb_result;  // count leading bits
  logic [ 4:0] ff1_result;  // holds the index of the first '1'
  logic        ff_no_one;  // if no ones are found
  logic [ 4:0] fl1_result;  // holds the index of the last '1'
  logic [ 5:0] bitop_result;  // result of all bitop operations muxed together

  cv32e40p_popcnt__ref popcnt_i (
      .in_i    (operand_a_i),
      .result_o(cnt_result)
  );

  always_comb begin
    ff_input = '0;

    case (operator_i)
      ALU_FF1: ff_input = operand_a_i;

      ALU_DIVU, ALU_REMU, ALU_FL1: ff_input = operand_a_rev;

      ALU_DIV, ALU_REM, ALU_CLB: begin
        if (operand_a_i[31]) ff_input = operand_a_neg_rev;
        else ff_input = operand_a_rev;
      end
    endcase
  end

  cv32e40p_ff_one__ref ff_one_i (
      .in_i       (ff_input),
      .first_one_o(ff1_result),
      .no_ones_o  (ff_no_one)
  );

  // special case if ff1_res is 0 (no 1 found), then we keep the 0
  // this is done in the result mux
  assign fl1_result = 5'd31 - ff1_result;
  assign clb_result = ff1_result - 5'd1;

  always_comb begin
    bitop_result = '0;
    case (operator_i)
      ALU_FF1: bitop_result = ff_no_one ? 6'd32 : {1'b0, ff1_result};
      ALU_FL1: bitop_result = ff_no_one ? 6'd32 : {1'b0, fl1_result};
      ALU_CNT: bitop_result = cnt_result;
      ALU_CLB: begin
        if (ff_no_one) begin
          if (operand_a_i[31]) bitop_result = 6'd31;
          else bitop_result = '0;
        end else begin
          bitop_result = clb_result;
        end
      end
      default: ;
    endcase
  end


  ////////////////////////////////////////////////
  //  ____  _ _     __  __             _        //
  // | __ )(_) |_  |  \/  | __ _ _ __ (_)_ __   //
  // |  _ \| | __| | |\/| |/ _` | '_ \| | '_ \  //
  // | |_) | | |_  | |  | | (_| | | | | | |_) | //
  // |____/|_|\__| |_|  |_|\__,_|_| |_|_| .__/  //
  //                                    |_|     //
  ////////////////////////////////////////////////

  logic extract_is_signed;
  logic extract_sign;
  logic [31:0] bmask_first, bmask_inv;
  logic [31:0] bextins_and;
  logic [31:0] bextins_result, bclr_result, bset_result;


  // construct bit mask for insert/extract/bclr/bset
  // bmask looks like this 00..0011..1100..00
  assign bmask_first       = {32'hFFFFFFFE} << bmask_a_i;
  assign bmask             = (~bmask_first) << bmask_b_i;
  assign bmask_inv         = ~bmask;

  assign bextins_and       = (operator_i == ALU_BINS) ? operand_c_i : {32{extract_sign}};

  assign extract_is_signed = (operator_i == ALU_BEXT);
  assign extract_sign      = extract_is_signed & shift_result[bmask_a_i];

  assign bextins_result    = (bmask & shift_result) | (bextins_and & bmask_inv);

  assign bclr_result       = operand_a_i & bmask_inv;
  assign bset_result       = operand_a_i | bmask;

  /////////////////////////////////////////////////////////////////////////////////
  //  ____ _____ _______     _____  ________      ________ _____   _____ ______  //
  // |  _ \_   _|__   __|   |  __ \|  ____\ \    / /  ____|  __ \ / ____|  ____| //
  // | |_) || |    | |______| |__) | |__   \ \  / /| |__  | |__) | (___ | |__    //
  // |  _ < | |    | |______|  _  /|  __|   \ \/ / |  __| |  _  / \___ \|  __|   //
  // | |_) || |_   | |      | | \ \| |____   \  /  | |____| | \ \ ____) | |____  //
  // |____/_____|  |_|      |_|  \_\______|   \/   |______|_|  \_\_____/|______| //
  //                                                                             //
  /////////////////////////////////////////////////////////////////////////////////

  logic [31:0] radix_2_rev;
  logic [31:0] radix_4_rev;
  logic [31:0] radix_8_rev;
  logic [31:0] reverse_result;
  logic [ 1:0] radix_mux_sel;

  assign radix_mux_sel = bmask_a_i[1:0];

  generate
    // radix-2 bit reverse
    for (j = 0; j < 32; j++) begin : gen_radix_2_rev
      assign radix_2_rev[j] = shift_result[31-j];
    end
    // radix-4 bit reverse
    for (j = 0; j < 16; j++) begin : gen_radix_4_rev
      assign radix_4_rev[2*j+1:2*j] = shift_result[31-j*2:31-j*2-1];
    end
    // radix-8 bit reverse
    for (j = 0; j < 10; j++) begin : gen_radix_8_rev
      assign radix_8_rev[3*j+2:3*j] = shift_result[31-j*3:31-j*3-2];
    end
    assign radix_8_rev[31:30] = 2'b0;
  endgenerate

  always_comb begin
    reverse_result = '0;

    unique case (radix_mux_sel)
      2'b00: reverse_result = radix_2_rev;
      2'b01: reverse_result = radix_4_rev;
      2'b10: reverse_result = radix_8_rev;

      default: reverse_result = radix_2_rev;
    endcase
  end

  ////////////////////////////////////////////////////
  //  ____ _____     __     __  ____  _____ __  __  //
  // |  _ \_ _\ \   / /    / / |  _ \| ____|  \/  | //
  // | | | | | \ \ / /    / /  | |_) |  _| | |\/| | //
  // | |_| | |  \ V /    / /   |  _ <| |___| |  | | //
  // |____/___|  \_/    /_/    |_| \_\_____|_|  |_| //
  //                                                //
  ////////////////////////////////////////////////////

  logic [31:0] result_div;
  logic        div_ready;
  logic        div_signed;
  logic        div_op_a_signed;
  logic [ 5:0] div_shift_int;

  assign div_signed = operator_i[0];

  assign div_op_a_signed = operand_a_i[31] & div_signed;

  assign div_shift_int = ff_no_one ? 6'd31 : clb_result;
  assign div_shift = div_shift_int + (div_op_a_signed ? 6'd0 : 6'd1);

  assign div_valid = enable_i & ((operator_i == ALU_DIV) || (operator_i == ALU_DIVU) ||
                     (operator_i == ALU_REM) || (operator_i == ALU_REMU));

  // inputs A and B are swapped
  cv32e40p_alu_div__ref alu_div_i (
      .Clk_CI (clk),
      .Rst_RBI(rst_n),

      // input IF
      .OpA_DI      (operand_b_i),
      .OpB_DI      (shift_left_result),
      .OpBShift_DI (div_shift),
      .OpBIsZero_SI((cnt_result == 0)),

      .OpBSign_SI(div_op_a_signed),
      .OpCode_SI (operator_i[1:0]),

      .Res_DO(result_div),

      // Hand-Shake
      .InVld_SI (div_valid),
      .OutRdy_SI(ex_ready_i),
      .OutVld_SO(div_ready)
  );

  ////////////////////////////////////////////////////////
  //   ____                 _ _     __  __              //
  //  |  _ \ ___  ___ _   _| | |_  |  \/  |_   ___  __  //
  //  | |_) / _ \/ __| | | | | __| | |\/| | | | \ \/ /  //
  //  |  _ <  __/\__ \ |_| | | |_  | |  | | |_| |>  <   //
  //  |_| \_\___||___/\__,_|_|\__| |_|  |_|\__,_/_/\_\  //
  //                                                    //
  ////////////////////////////////////////////////////////

  always_comb begin
    result_o = '0;

    unique case (operator_i)
      // Standard Operations
      ALU_AND: result_o = operand_a_i & operand_b_i;
      ALU_OR:  result_o = operand_a_i | operand_b_i;
      ALU_XOR: result_o = operand_a_i ^ operand_b_i;

      // Shift Operations
      ALU_ADD, ALU_ADDR, ALU_ADDU, ALU_ADDUR,
      ALU_SUB, ALU_SUBR, ALU_SUBU, ALU_SUBUR,
      ALU_SLL,
      ALU_SRL, ALU_SRA,
      ALU_ROR:
      result_o = shift_result;

      // bit manipulation instructions
      ALU_BINS, ALU_BEXT, ALU_BEXTU: result_o = bextins_result;

      ALU_BCLR: result_o = bclr_result;
      ALU_BSET: result_o = bset_result;

      // Bit reverse instruction
      ALU_BREV: result_o = reverse_result;

      // pack and shuffle operations
      ALU_SHUF, ALU_SHUF2, ALU_PCKLO, ALU_PCKHI, ALU_EXT, ALU_EXTS, ALU_INS: result_o = pack_result;

      // Min/Max/Ins
      ALU_MIN, ALU_MINU, ALU_MAX, ALU_MAXU: result_o = result_minmax;

      //Abs/Cplxconj , ABS is used to do 0 - A for Cplxconj
      ALU_ABS: result_o = is_clpx_i ? {adder_result[31:16], operand_a_i[15:0]} : result_minmax;

      ALU_CLIP, ALU_CLIPU: result_o = clip_result;

      // Comparison Operations
      ALU_EQ, ALU_NE, ALU_GTU, ALU_GEU, ALU_LTU, ALU_LEU, ALU_GTS, ALU_GES, ALU_LTS, ALU_LES: begin
        result_o[31:24] = {8{cmp_result[3]}};
        result_o[23:16] = {8{cmp_result[2]}};
        result_o[15:8]  = {8{cmp_result[1]}};
        result_o[7:0]   = {8{cmp_result[0]}};
      end
      // Non-vector comparisons
      ALU_SLTS, ALU_SLTU, ALU_SLETS, ALU_SLETU: result_o = {31'b0, comparison_result_o};

      ALU_FF1, ALU_FL1, ALU_CLB, ALU_CNT: result_o = {26'h0, bitop_result[5:0]};

      // Division Unit Commands
      ALU_DIV, ALU_DIVU, ALU_REM, ALU_REMU: result_o = result_div;

      default: ;  // default case to suppress unique warning
    endcase
  end

  assign ready_o = div_ready;

endmodule

// Copyright 2018 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License.  You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

////////////////////////////////////////////////////////////////////////////////
// Engineer:       Andreas Traber - atraber@student.ethz.ch                   //
//                                                                            //
// Additional contributions by:                                               //
//                 Davide Schiavone - pschiavo@iis.ee.ethz.ch                 //
//                                                                            //
// Design Name:    cv32e40p_popcnt__ref                                               //
// Project Name:   RI5CY                                                      //
// Language:       SystemVerilog                                              //
//                                                                            //
// Description:    Count the number of '1's in a word                         //
//                                                                            //
////////////////////////////////////////////////////////////////////////////////

module cv32e40p_popcnt__ref (
    input  logic [31:0] in_i,
    output logic [ 5:0] result_o
);

  logic [15:0][1:0] cnt_l1;
  logic [ 7:0][2:0] cnt_l2;
  logic [ 3:0][3:0] cnt_l3;
  logic [ 1:0][4:0] cnt_l4;

  genvar l, m, n, p;
  generate
    for (l = 0; l < 16; l++) begin : gen_cnt_l1
      assign cnt_l1[l] = {1'b0, in_i[2*l]} + {1'b0, in_i[2*l+1]};
    end
  endgenerate

  generate
    for (m = 0; m < 8; m++) begin : gen_cnt_l2
      assign cnt_l2[m] = {1'b0, cnt_l1[2*m]} + {1'b0, cnt_l1[2*m+1]};
    end
  endgenerate

  generate
    for (n = 0; n < 4; n++) begin : gen_cnt_l3
      assign cnt_l3[n] = {1'b0, cnt_l2[2*n]} + {1'b0, cnt_l2[2*n+1]};
    end
  endgenerate

  generate
    for (p = 0; p < 2; p++) begin : gen_cnt_l4
      assign cnt_l4[p] = {1'b0, cnt_l3[2*p]} + {1'b0, cnt_l3[2*p+1]};
    end
  endgenerate

  assign result_o = {1'b0, cnt_l4[0]} + {1'b0, cnt_l4[1]};

endmodule

// Copyright 2018 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License.  You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

////////////////////////////////////////////////////////////////////////////////
// Engineer:       Andreas Traber - atraber@student.ethz.ch                   //
//                                                                            //
// Additional contributions by:                                               //
//                 Davide Schiavone - pschiavo@iis.ee.ethz.ch                 //
//                                                                            //
// Design Name:    cv32e40p_ff_one__ref                                               //
// Project Name:   RI5CY                                                      //
// Language:       SystemVerilog                                              //
//                                                                            //
// Description:    Find First One                                             //
//                                                                            //
////////////////////////////////////////////////////////////////////////////////

module cv32e40p_ff_one__ref #(
    parameter LEN = 32
) (
    input logic [LEN-1:0] in_i,

    output logic [$clog2(LEN)-1:0] first_one_o,
    output logic                   no_ones_o
);

  localparam NUM_LEVELS = $clog2(LEN);

  logic [          LEN-1:0][NUM_LEVELS-1:0] index_lut;
  logic [2**NUM_LEVELS-1:0]                 sel_nodes;
  logic [2**NUM_LEVELS-1:0][NUM_LEVELS-1:0] index_nodes;


  //////////////////////////////////////////////////////////////////////////////
  // generate tree structure
  //////////////////////////////////////////////////////////////////////////////

  generate
    genvar j;
    for (j = 0; j < LEN; j++) begin : gen_index_lut
      assign index_lut[j] = $unsigned(j);
    end
  endgenerate

  generate
    genvar k;
    genvar l;
    genvar level;

    assign sel_nodes[2**NUM_LEVELS-1] = 1'b0;

    for (level = 0; level < NUM_LEVELS; level++) begin : gen_tree
      //------------------------------------------------------------
      if (level < NUM_LEVELS - 1) begin : gen_non_root_level
        for (l = 0; l < 2 ** level; l++) begin : gen_node
          assign sel_nodes[2**level-1+l]   = sel_nodes[2**(level+1)-1+l*2] | sel_nodes[2**(level+1)-1+l*2+1];
          assign index_nodes[2**level-1+l] = (sel_nodes[2**(level+1)-1+l*2] == 1'b1) ?
                                           index_nodes[2**(level+1)-1+l*2] : index_nodes[2**(level+1)-1+l*2+1];
        end
      end
      //------------------------------------------------------------
      if (level == NUM_LEVELS - 1) begin : gen_root_level
        for (k = 0; k < 2 ** level; k++) begin : gen_node
          // if two successive indices are still in the vector...
          if (k * 2 < LEN - 1) begin : gen_two
            assign sel_nodes[2**level-1+k] = in_i[k*2] | in_i[k*2+1];
            assign index_nodes[2**level-1+k] = (in_i[k*2] == 1'b1) ? index_lut[k*2] : index_lut[k*2+1];
          end
          // if only the first index is still in the vector...
          if (k * 2 == LEN - 1) begin : gen_one
            assign sel_nodes[2**level-1+k]   = in_i[k*2];
            assign index_nodes[2**level-1+k] = index_lut[k*2];
          end
          // if index is out of range
          if (k * 2 > LEN - 1) begin : gen_out_of_range
            assign sel_nodes[2**level-1+k]   = 1'b0;
            assign index_nodes[2**level-1+k] = '0;
          end
        end
      end
      //------------------------------------------------------------
    end
  endgenerate

  //////////////////////////////////////////////////////////////////////////////
  // connect output
  //////////////////////////////////////////////////////////////////////////////

  assign first_one_o = index_nodes[0];
  assign no_ones_o   = ~sel_nodes[0];

endmodule

// Copyright 2018 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License.  You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

///////////////////////////////////////////////////////////////////////////////
// File       : Simple Serial Divider
// Ver        : 1.0
// Date       : 15.03.2016
///////////////////////////////////////////////////////////////////////////////
//
// Description: this is a simple serial divider for signed integers (int32).
//
///////////////////////////////////////////////////////////////////////////////
//
// Authors    : Michael Schaffner (schaffner@iis.ee.ethz.ch)
//              Andreas Traber    (atraber@iis.ee.ethz.ch)
//
///////////////////////////////////////////////////////////////////////////////

module cv32e40p_alu_div__ref #(
    parameter C_WIDTH     = 32,
    parameter C_LOG_WIDTH = 6
) (
    input  logic                   Clk_CI,
    input  logic                   Rst_RBI,
    // input IF
    input  logic [    C_WIDTH-1:0] OpA_DI,
    input  logic [    C_WIDTH-1:0] OpB_DI,
    input  logic [C_LOG_WIDTH-1:0] OpBShift_DI,
    input  logic                   OpBIsZero_SI,
    //
    input  logic                   OpBSign_SI,  // gate this to 0 in case of unsigned ops
    input  logic [            1:0] OpCode_SI,  // 0: udiv, 2: urem, 1: div, 3: rem
    // handshake
    input  logic                   InVld_SI,
    // output IF
    input  logic                   OutRdy_SI,
    output logic                   OutVld_SO,
    output logic [    C_WIDTH-1:0] Res_DO
);

  ///////////////////////////////////////////////////////////////////////////////
  // signal declarations
  ///////////////////////////////////////////////////////////////////////////////

  logic [C_WIDTH-1:0] ResReg_DP, ResReg_DN;
  logic [C_WIDTH-1:0] ResReg_DP_rev;
  logic [C_WIDTH-1:0] AReg_DP, AReg_DN;
  logic [C_WIDTH-1:0] BReg_DP, BReg_DN;

  logic RemSel_SN, RemSel_SP;
  logic CompInv_SN, CompInv_SP;
  logic ResInv_SN, ResInv_SP;

  logic [C_WIDTH-1:0] AddMux_D;
  logic [C_WIDTH-1:0] AddOut_D;
  logic [C_WIDTH-1:0] AddTmp_D;
  logic [C_WIDTH-1:0] BMux_D;
  logic [C_WIDTH-1:0] OutMux_D;

  logic [C_LOG_WIDTH-1:0] Cnt_DP, Cnt_DN;
  logic CntZero_S;

  logic ARegEn_S, BRegEn_S, ResRegEn_S, ABComp_S, PmSel_S, LoadEn_S;

  enum logic [1:0] {
    IDLE,
    DIVIDE,
    FINISH
  }
      State_SN, State_SP;


  ///////////////////////////////////////////////////////////////////////////////
  // datapath
  ///////////////////////////////////////////////////////////////////////////////

  assign PmSel_S  = LoadEn_S & ~(OpCode_SI[0] & (OpA_DI[$high(OpA_DI)] ^ OpBSign_SI));

  // muxes
  assign AddMux_D = (LoadEn_S) ? OpA_DI : BReg_DP;

  // attention: logical shift in case of negative operand B!
  assign BMux_D   = (LoadEn_S) ? OpB_DI : {CompInv_SP, (BReg_DP[$high(BReg_DP):1])};

  genvar index;
  generate
    for (index = 0; index < C_WIDTH; index++) begin : gen_bit_swapping
      assign ResReg_DP_rev[index] = ResReg_DP[C_WIDTH-1-index];
    end
  endgenerate

  assign OutMux_D = (RemSel_SP) ? AReg_DP : ResReg_DP_rev;

  // invert if necessary
  assign Res_DO = (ResInv_SP) ? -$signed(OutMux_D) : OutMux_D;

  // main comparator
  assign ABComp_S    = ((AReg_DP == BReg_DP) | ((AReg_DP > BReg_DP) ^ CompInv_SP)) & ((|AReg_DP) | OpBIsZero_SI);

  // main adder
  assign AddTmp_D = (LoadEn_S) ? 0 : AReg_DP;
  assign AddOut_D = (PmSel_S) ? AddTmp_D + AddMux_D : AddTmp_D - $signed(AddMux_D);

  ///////////////////////////////////////////////////////////////////////////////
  // counter
  ///////////////////////////////////////////////////////////////////////////////

  assign Cnt_DN = (LoadEn_S) ? OpBShift_DI : (~CntZero_S) ? Cnt_DP - 1 : Cnt_DP;

  assign CntZero_S = ~(|Cnt_DP);

  ///////////////////////////////////////////////////////////////////////////////
  // FSM
  ///////////////////////////////////////////////////////////////////////////////

  always_comb begin : p_fsm
    // default
    State_SN   = State_SP;

    OutVld_SO  = 1'b0;

    LoadEn_S   = 1'b0;

    ARegEn_S   = 1'b0;
    BRegEn_S   = 1'b0;
    ResRegEn_S = 1'b0;

    case (State_SP)
      /////////////////////////////////
      IDLE: begin
        OutVld_SO = 1'b1;

        if (InVld_SI) begin
          OutVld_SO = 1'b0;
          ARegEn_S  = 1'b1;
          BRegEn_S  = 1'b1;
          LoadEn_S  = 1'b1;
          State_SN  = DIVIDE;
        end
      end
      /////////////////////////////////
      DIVIDE: begin

        ARegEn_S   = ABComp_S;
        BRegEn_S   = 1'b1;
        ResRegEn_S = 1'b1;

        // calculation finished
        // one more divide cycle (32nd divide cycle)
        if (CntZero_S) begin
          State_SN = FINISH;
        end
      end
      /////////////////////////////////
      FINISH: begin
        OutVld_SO = 1'b1;

        if (OutRdy_SI) begin
          State_SN = IDLE;
        end
      end
      /////////////////////////////////
      default:  /* default */;
      /////////////////////////////////
    endcase
  end


  ///////////////////////////////////////////////////////////////////////////////
  // regs
  ///////////////////////////////////////////////////////////////////////////////

  // get flags
  assign RemSel_SN = (LoadEn_S) ? OpCode_SI[1] : RemSel_SP;
  assign CompInv_SN = (LoadEn_S) ? OpBSign_SI : CompInv_SP;
  assign ResInv_SN = (LoadEn_S) ? (~OpBIsZero_SI | OpCode_SI[1]) & OpCode_SI[0] & (OpA_DI[$high(
      OpA_DI
  )] ^ OpBSign_SI) : ResInv_SP;

  assign AReg_DN = (ARegEn_S) ? AddOut_D : AReg_DP;
  assign BReg_DN = (BRegEn_S) ? BMux_D : BReg_DP;
  assign ResReg_DN = (LoadEn_S) ? '0 : (ResRegEn_S) ? {
    ABComp_S, ResReg_DP[$high(ResReg_DP):1]
  } : ResReg_DP;

  always_ff @(posedge Clk_CI or negedge Rst_RBI) begin : p_regs
    if (~Rst_RBI) begin
      State_SP   <= IDLE;
      AReg_DP    <= '0;
      BReg_DP    <= '0;
      ResReg_DP  <= '0;
      Cnt_DP     <= '0;
      RemSel_SP  <= 1'b0;
      CompInv_SP <= 1'b0;
      ResInv_SP  <= 1'b0;
    end else begin
      State_SP   <= State_SN;
      AReg_DP    <= AReg_DN;
      BReg_DP    <= BReg_DN;
      ResReg_DP  <= ResReg_DN;
      Cnt_DP     <= Cnt_DN;
      RemSel_SP  <= RemSel_SN;
      CompInv_SP <= CompInv_SN;
      ResInv_SP  <= ResInv_SN;
    end
  end

  ///////////////////////////////////////////////////////////////////////////////
  // assertions
  ///////////////////////////////////////////////////////////////////////////////

`ifdef CV32E40P_ASSERT_ON
  initial begin : p_assertions
    assert (C_LOG_WIDTH == $clog2(C_WIDTH + 1))
    else $error("C_LOG_WIDTH must be $clog2(C_WIDTH+1)");
  end
`endif

endmodule  // serDiv
