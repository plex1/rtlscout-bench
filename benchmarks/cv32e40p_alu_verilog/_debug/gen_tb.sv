// Oracle harness: replay stimuli.dat against the PRISTINE CV32E40P ALU
// (unmodified upstream sources in orig/) and dump vectors.dat with the
// sampled result_o / comparison_result_o appended.
//
// Protocol (must stay in lockstep with the benchmark tb.sv):
//   * rst_n held low for 5 cycles at start.
//   * Each vector: drive all inputs at a negedge with enable_i=1, ex_ready_i=1,
//     hold stable; poll ready_o at each following negedge (cap 64 cycles);
//     when ready_o==1, sample result_o / comparison_result_o at that negedge,
//     then deassert enable_i (prevents the serial divider from relaunching)
//     and move to the next vector.
//   * Combinational ops are ready at the first polled negedge; div/rem take
//     up to ~37 cycles.
module gen_tb;
  logic clk = 0;
  always #5 clk = ~clk;

  logic        rst_n;
  logic        enable_i;
  logic [6:0]  op;
  logic [31:0] a, b, c;
  logic [1:0]  vm;
  logic [4:0]  bma, bmb;
  logic [1:0]  ive;
  logic        cpx, sbr;
  logic [1:0]  csh;
  logic [31:0] result_o;
  logic        comparison_result_o;
  logic        ready_o;
  logic        ex_ready_i;

  cv32e40p_alu dut (
      .clk                (clk),
      .rst_n              (rst_n),
      .enable_i           (enable_i),
      .operator_i         (cv32e40p_pkg::alu_opcode_e'(op)),
      .operand_a_i        (a),
      .operand_b_i        (b),
      .operand_c_i        (c),
      .vector_mode_i      (vm),
      .bmask_a_i          (bma),
      .bmask_b_i          (bmb),
      .imm_vec_ext_i      (ive),
      .is_clpx_i          (cpx),
      .is_subrot_i        (sbr),
      .clpx_shift_i       (csh),
      .result_o           (result_o),
      .comparison_result_o(comparison_result_o),
      .ready_o            (ready_o),
      .ex_ready_i         (ex_ready_i)
  );

  int fin, fout, n, total, timeouts, cycles;
  logic [3:0] cchk;
  string line;

  initial begin
    fin  = $fopen("stimuli.dat", "r");
    fout = $fopen("vectors.dat", "w");
    if (fin == 0 || fout == 0) $fatal(1, "cannot open stimuli.dat / vectors.dat");
    $fdisplay(fout, "# op vm bma bmb ive cpx sbr csh a b c cchk exp_result exp_cmp");

    rst_n      = 1'b0;
    enable_i   = 1'b0;
    ex_ready_i = 1'b1;
    op = '0; a = '0; b = '0; c = '0; vm = '0;
    bma = '0; bmb = '0; ive = '0; cpx = '0; sbr = '0; csh = '0;
    repeat (5) @(negedge clk);
    rst_n = 1'b1;
    @(negedge clk);

    total = 0;
    timeouts = 0;
    while (!$feof(fin)) begin
      if ($fgets(line, fin) == 0) break;
      if (line.len() == 0 || line[0] == "#" || line[0] == "\n") continue;
      n = $sscanf(line, "%h %h %h %h %h %h %h %h %h %h %h %h",
                  op, vm, bma, bmb, ive, cpx, sbr, csh, a, b, c, cchk);
      if (n != 12) continue;

      @(negedge clk);
      enable_i = 1'b1;
      cycles = 0;
      do begin
        @(negedge clk);
        cycles++;
      end while (ready_o !== 1'b1 && cycles < 64);
      if (ready_o !== 1'b1) begin
        $display("ORACLE_TIMEOUT op=%h", op);
        timeouts++;
      end else begin
        $fdisplay(fout, "%02h %01h %02h %02h %01h %01h %01h %01h %08h %08h %08h %01h %08h %01h",
                  op, vm, bma, bmb, ive, cpx, sbr, csh, a, b, c, cchk[0],
                  result_o, comparison_result_o);
        total++;
      end
      enable_i = 1'b0;
    end

    $display("ORACLE_DONE total=%0d timeouts=%0d", total, timeouts);
    $fclose(fin);
    $fclose(fout);
    $finish;
  end
endmodule
