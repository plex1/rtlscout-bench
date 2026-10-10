// Self-checking testbench for jpeg_idct_2d — the full 8x8 2-D IDCT
// (x-pass -> transpose -> y-pass) of ultraembedded/core_jpeg.
// Reads vectors.dat: one line per 8x8 block = 64 input coefficients (16-bit
// signed, 4 hex digits, row-major c[row][col]) followed by 64 expected outputs
// (32-bit, 8 hex digits, in ROW-MAJOR pixel order — outputs are matched via
// outport_idx_o, which labels each streamed value with its row-major position).
//
// Drive protocol: identical to the jpeg_idct_x benchmark (the upstream block
// serializer's fixed schedule): per row 8 back-to-back valid beats idx=0..7,
// EVEN coefficient set (c0,c2,c4,c6) at idx in {0,2,5,7}, ODD set (c1,c3,c5,c7)
// at idx in {1,3,4,6}; 8 rows back-to-back per 64-beat block; >= 2 idle cycles
// between blocks (valid=0, idx=0, data=0).
//
// Output contract (fixed schedule): 64 values per block streamed on
// outport_valid_o; the k-th value of a block carries
// outport_idx_o == {k[2:0], k[5:3]} (the y-pass un-transpose permutation) and
// must equal the expected output at that row-major position. Comparison is
// EXACT (integer outputs).
module tb;
  int total_checks;
  int total_errors;

  logic        clk;
  logic        rst;
  logic        img_start;
  logic        inport_valid;
  logic [15:0] inport_data0, inport_data1, inport_data2, inport_data3;
  logic [2:0]  inport_idx;
  logic        outport_valid;
  logic [31:0] outport_data;
  logic [5:0]  outport_idx;

  jpeg_idct_2d dut (
    .clk(clk), .rst(rst),
    .img_start_i(img_start), .img_end_i(1'b0),
    .inport_valid_i(inport_valid),
    .inport_data0_i(inport_data0), .inport_data1_i(inport_data1),
    .inport_data2_i(inport_data2), .inport_data3_i(inport_data3),
    .inport_idx_i(inport_idx),
    .outport_valid_o(outport_valid),
    .outport_data_o(outport_data),
    .outport_idx_o(outport_idx)
  );

  initial clk = 1'b0;
  always #5 clk = ~clk;

  // ---- load vectors.dat: flat token stream, 128 tokens per block ----------
  localparam MAX_BLOCKS = 1024;
  logic [15:0] coeffs [0:MAX_BLOCKS*64-1];
  logic [31:0] expy   [0:MAX_BLOCKS*64-1];
  int num_blocks;

  task automatic load_vectors;
    int fd, rc, n;
    logic [31:0] tok;
    logic [31:0] flat [0:MAX_BLOCKS*128-1];
    fd = $fopen("vectors.dat", "r");
    if (fd == 0) begin
      $display("ERROR: cannot open vectors.dat");
      $fatal(1);
    end
    n = 0;
    while (!$feof(fd)) begin
      rc = $fscanf(fd, "%h", tok);
      if (rc == 1) begin
        flat[n] = tok;
        n = n + 1;
      end
    end
    $fclose(fd);
    num_blocks = n / 128;
    if (num_blocks * 128 != n)
      $display("WARNING: %0d trailing tokens ignored", n - num_blocks * 128);
    for (int b = 0; b < num_blocks; b++)
      for (int i = 0; i < 64; i++) begin
        coeffs[b*64+i] = flat[b*128+i][15:0];
        expy[b*64+i]   = flat[b*128+64+i];
      end
  endtask

  // ---- output checker -------------------------------------------------------
  int out_cnt;
  bit checking;
  logic [5:0] k;

  always @(posedge clk) begin
    #1;
    if (checking && outport_valid) begin
      total_checks = total_checks + 1;
      k = out_cnt % 64;
      if (outport_idx !== {k[2:0], k[5:3]}) begin
        $display("TB_ERROR block=%0d out=%0d idx=%0d expected_idx=%0d",
                 out_cnt / 64, out_cnt % 64, outport_idx, {k[2:0], k[5:3]});
        total_errors = total_errors + 1;
      end
      if (outport_data !== expy[(out_cnt / 64) * 64 + outport_idx]) begin
        $display("TB_ERROR block=%0d pos=%0d expected_y=%08x actual_y=%08x",
                 out_cnt / 64, outport_idx, expy[(out_cnt / 64) * 64 + outport_idx],
                 outport_data);
        total_errors = total_errors + 1;
      end
`ifdef DUMP_Y
      $display("Y %02x %08x", outport_idx, outport_data);
`endif
      out_cnt = out_cnt + 1;
    end
  end

  // ---- driver ----------------------------------------------------------------
  task automatic drive_beat(input logic valid, input logic [2:0] kk,
                            input logic [15:0] d0, d1, d2, d3);
    @(negedge clk);
    inport_valid = valid;
    inport_idx   = kk;
    inport_data0 = d0;
    inport_data1 = d1;
    inport_data2 = d2;
    inport_data3 = d3;
  endtask

  initial begin
    total_checks = 0;
    total_errors = 0;
    out_cnt = 0;
    checking = 0;
    rst = 1;
    img_start = 0;
    inport_valid = 0;
    inport_idx = 0;
    inport_data0 = 0; inport_data1 = 0; inport_data2 = 0; inport_data3 = 0;

    load_vectors();

    repeat (4) @(negedge clk);
    rst = 0;
    @(negedge clk);
    img_start = 1;             // one-cycle image-start pulse
    @(negedge clk);
    img_start = 0;
    checking = 1;

    for (int b = 0; b < num_blocks; b++) begin
      for (int r = 0; r < 8; r++) begin
        for (int kb = 0; kb < 8; kb++) begin
          if (kb == 0 || kb == 2 || kb == 5 || kb == 7)
            drive_beat(1'b1, kb[2:0], coeffs[b*64+r*8+0], coeffs[b*64+r*8+2],
                                      coeffs[b*64+r*8+4], coeffs[b*64+r*8+6]);
          else
            drive_beat(1'b1, kb[2:0], coeffs[b*64+r*8+1], coeffs[b*64+r*8+3],
                                      coeffs[b*64+r*8+5], coeffs[b*64+r*8+7]);
        end
      end
      // inter-block gap: >= 2 idle cycles (serializer IDLE+SETUP), varied a bit
      repeat (2 + (b % 3)) drive_beat(1'b0, 3'd0, 16'd0, 16'd0, 16'd0, 16'd0);
    end
    drive_beat(1'b0, 3'd0, 16'd0, 16'd0, 16'd0, 16'd0);
    repeat (400) @(negedge clk);  // flush x + transpose + y pipeline

    if (out_cnt != num_blocks * 64) begin
      $display("TB_ERROR missing outputs: got %0d, expected %0d", out_cnt, num_blocks * 64);
      total_errors = total_errors + 1;
    end
    $display("TB_SUMMARY total=%0d errors=%0d", total_checks, total_errors);
    if (total_errors != 0) $fatal(1, "FAIL");
    $display("PASS");
    $finish;
  end
endmodule
