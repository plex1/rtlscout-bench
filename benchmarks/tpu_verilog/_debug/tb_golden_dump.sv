// Golden-trace generator: drives stimuli.txt into the ORIGINAL tpu
// (orig/tpu.v + tpu_array.v + tpu_pe.v) and dumps (c_valid, c_data) once per
// stimulus line -> golden.txt. vectors.dat = `paste -d' ' stimuli.txt golden.txt`.
// Alignment (same in the benchmark tb.sv): 4 reset cycles, then per line:
// apply inputs post-negedge, wait one negedge, record outputs.
module tb;
  localparam N = 8, DW = 8, ACCW = 32;
  logic clk, rst;
  logic w_valid, a_valid, c_valid;
  logic [N*DW-1:0] w_data, a_data;
  logic [N*ACCW-1:0] c_data;

  tpu #(.N(N), .DW(DW), .ACCW(ACCW)) dut
    (.clk(clk), .rst(rst), .w_valid(w_valid), .w_data(w_data),
     .a_valid(a_valid), .a_data(a_data), .c_valid(c_valid), .c_data(c_data));

  initial clk = 0;
  always #5 clk = ~clk;

  integer fin, fout, rc;
  string line_buf;
  logic s_wv, s_av;
  logic [63:0] s_wd, s_ad;

  initial begin
    w_valid = 0; a_valid = 0; w_data = 0; a_data = 0;
    rst = 1;
    repeat (4) @(negedge clk);
    rst = 0;
    fin = $fopen("stimuli.txt", "r");
    fout = $fopen("golden.txt", "w");
    if (fin == 0 || fout == 0) begin $display("ERROR: files"); $fatal(1); end
    while (!$feof(fin)) begin
      void'($fgets(line_buf, fin));
      if (line_buf.len() == 0) continue;
      rc = $sscanf(line_buf, "%h %h %h %h", s_wv, s_wd, s_av, s_ad);
      if (rc != 4) continue;
      w_valid = s_wv; w_data = s_wd; a_valid = s_av; a_data = s_ad;
      @(negedge clk);
      $fwrite(fout, "%h %h\n", c_valid, c_data);
    end
    $fclose(fin); $fclose(fout);
    $display("GOLDEN_DONE");
    $finish;
  end
endmodule
