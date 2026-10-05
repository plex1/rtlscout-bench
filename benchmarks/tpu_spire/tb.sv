// Self-checking testbench for the tpu benchmark (8x8 weight-stationary
// systolic matmul tile). Reads vectors.dat, one line per clock cycle:
//   w_valid w_data(64b) a_valid a_data(64b)   c_valid c_data(256b)   (hex)
// The expected outputs are the ORIGINAL logikbench tpu's per-cycle behavior;
// c_valid and c_data are compared CYCLE-ACCURATELY every cycle (c_data only
// while c_valid is high — the original holds stale data between results).
module tb;
  localparam N = 8, DW = 8, ACCW = 32;
  int total_checks;
  int total_errors;

  logic clk, rst;
  logic w_valid, a_valid, c_valid;
  logic [N*DW-1:0] w_data, a_data;
  logic [N*ACCW-1:0] c_data;

  tpu dut
    (.clk(clk), .rst(rst), .w_valid(w_valid), .w_data(w_data),
     .a_valid(a_valid), .a_data(a_data), .c_valid(c_valid), .c_data(c_data));

  initial clk = 0;
  always #5 clk = ~clk;

  integer fd, rc, line_num;
  string line_buf;
  logic s_wv, s_av, e_cv;
  logic [63:0] s_wd, s_ad;
  logic [255:0] e_cd;

  initial begin
    total_checks = 0;
    total_errors = 0;
    w_valid = 0; a_valid = 0; w_data = 0; a_data = 0;
    rst = 1;
    repeat (4) @(negedge clk);
    rst = 0;

    fd = $fopen("vectors.dat", "r");
    if (fd == 0) begin
      $display("ERROR: cannot open vectors.dat");
      $fatal(1);
    end
    line_num = 0;
    while (!$feof(fd)) begin
      line_num = line_num + 1;
      void'($fgets(line_buf, fd));
      if (line_buf.len() == 0) continue;
      if (line_buf.substr(0, 0) == "#") continue;
      rc = $sscanf(line_buf, "%h %h %h %h %h %h", s_wv, s_wd, s_av, s_ad, e_cv, e_cd);
      if (rc != 6) continue;
      w_valid = s_wv; w_data = s_wd; a_valid = s_av; a_data = s_ad;
      @(negedge clk);
      total_checks = total_checks + 1;
      if (c_valid !== e_cv || (e_cv && c_data !== e_cd)) begin
        $display("TB_ERROR line=%0d exp(cv=%b cd=%h) got(cv=%b cd=%h)",
                 line_num, e_cv, e_cd, c_valid, c_data);
        total_errors = total_errors + 1;
        if (total_errors > 20) begin
          $display("TB_SUMMARY total=%0d errors=%0d", total_checks, total_errors);
          $fatal(1, "FAIL (too many errors, aborting)");
        end
      end
    end
    $fclose(fd);

    $display("TB_SUMMARY total=%0d errors=%0d", total_checks, total_errors);
    if (total_errors != 0) $fatal(1, "FAIL");
    $display("PASS");
    $finish;
  end
endmodule
