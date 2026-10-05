//#############################################################################
// Copyright: Zero ASIC. All rights Reserved.
// Author: Andreas Olofsson
// License:  MIT (see LICENSE file in LogikBench repository)
//#############################################################################
//
// Processing element (PE) for the weight-stationary systolic array (TPU MXU).
//
// Each PE holds one stationary signed weight 'w'. Two independent datapaths
// share the cell:
//
//   Weight load (ld): the weights form a per-column shift register that loads
//   from the top. On each ld cycle the PE captures the weight from the PE above
//   (w_north) and presents its stored weight downward (w_south). After N load
//   cycles every PE in a column holds its final stationary weight.
//
//   Compute (en): the activation flows west to east (registered, one hop per
//   cycle) and the partial sum flows north to south, accumulating the local
//   product a_west * w. This is the only arithmetic cell in the array: one
//   signed DW x DW multiply plus an ACCW-bit add.
//
//#############################################################################

module tpu_pe
  #(parameter DW = 8,	 // operand width (signed)
    parameter ACCW = 32) // accumulator / partial-sum width (signed)
   (
    input		  clk,
    input		  rst,	      // synchronous, active high
    input		  ld,	      // weight-load shift enable
    input		  en,	      // compute enable
    input [DW-1:0]	  w_north,    // weight in from PE above
    input [DW-1:0]	  a_west,     // activation in from west
    input [ACCW-1:0]	  psum_north, // partial sum in from above
    output [DW-1:0]	  w_south,    // stored weight out to PE below
    output reg [DW-1:0]	  a_east,     // activation out to east
    output reg [ACCW-1:0] psum_south  // partial sum out to below
    );

   // stationary weight register; presented downward for the load shift chain
   reg [DW-1:0] w;
   assign w_south = w;

   // local product: the ONLY signed operation. Operands are interpreted as
   // signed here via $signed; the 2*DW-bit result holds the two's-complement
   // product, which is then sign-extended to the accumulator width below.
   wire [2*DW-1:0] prod = $signed(a_west) * $signed(w);

   always @(posedge clk) begin
      if (rst) begin
         w          <= {DW{1'b0}};
         a_east     <= {DW{1'b0}};
         psum_south <= {ACCW{1'b0}};
      end
      else begin
         if (ld)
           w <= w_north;
         if (en) begin
            a_east     <= a_west;
            psum_south <= psum_north +
                          {{(ACCW-2*DW){prod[2*DW-1]}}, prod};
         end
      end
   end

endmodule
//#############################################################################
// Copyright: Zero ASIC. All rights Reserved.
// Author: Andreas Olofsson
// License:  MIT (see LICENSE file in LogikBench repository)
//#############################################################################
//
// N x N weight-stationary systolic array (the TPU MXU core).
//
// One tpu_pe per grid element, wired as a 2D mesh (per CLAUDE.md: replicated
// hardware via generate, never a procedural unroll):
//
//   - weights load from the top: w_top[j] feeds column j; each column is a
//     vertical shift register (w_south of PE(i,j) -> w_north of PE(i+1,j)).
//   - activations flow west to east: a_left[i] enters row i; a_east of PE(i,j)
//     feeds a_west of PE(i,j+1).
//   - partial sums flow north to south and accumulate; the top row's psum_north
//     is tied to zero (fresh accumulation), and c_bot[j] is the column-j result
//     leaving the bottom row.
//
// With weight B[i][j] held at PE(i,j) and activation A[m][i] entering row i,
// column j computes C[m][j] = sum_i A[m][i] * B[i][j]. Input/output skew (which
// time-aligns rows and columns) lives in the tpu top, not here.
//
//#############################################################################

module tpu_array
  #(parameter N = 8,	 // array dimension (N x N PEs)
    parameter DW = 8,	 // operand width (signed)
    parameter ACCW = 32) // accumulator width (signed)
   (
    input		clk,
    input		rst,	// synchronous, active high
    input		ld,	// weight-load shift enable
    input		en,	// compute enable
    input [N*DW-1:0]	w_top,	// weight inputs, one per column (load)
    input [N*DW-1:0]	a_left,	// activation inputs, one per row
    output [N*ACCW-1:0]	c_bot	// column results, one per column
    );

   // mesh nets: a_h carries activations east, psum_v carries sums south,
   // w_v carries weights south during load.
   wire [DW-1:0]   a_h    [0:N-1][0:N];
   wire [ACCW-1:0] psum_v [0:N][0:N-1];
   wire [DW-1:0]   w_v    [0:N][0:N-1];

   genvar	   i, j;
   generate
      // west edge: activation inputs into column 0 of each row
      for (i = 0; i < N; i = i + 1) begin : g_west
         assign a_h[i][0] = a_left[i*DW +: DW];
      end
      // top edge: zero partial sums, weight inputs; bottom edge: results out
      for (j = 0; j < N; j = j + 1) begin : g_edge
         assign psum_v[0][j] = {ACCW{1'b0}};
         assign w_v[0][j]    = w_top[j*DW +: DW];
         assign c_bot[j*ACCW +: ACCW] = psum_v[N][j];
      end
      // the PE mesh
      for (i = 0; i < N; i = i + 1) begin : g_pe_row
         for (j = 0; j < N; j = j + 1) begin : g_pe_col
            tpu_pe #(.DW(DW), .ACCW(ACCW)) u_pe
              (.clk        (clk),
               .rst        (rst),
               .ld         (ld),
               .en         (en),
               .w_north    (w_v[i][j]),
               .a_west     (a_h[i][j]),
               .psum_north (psum_v[i][j]),
               .w_south    (w_v[i+1][j]),
               .a_east     (a_h[i][j+1]),
               .psum_south (psum_v[i+1][j]));
         end
      end
   endgenerate

endmodule
//#############################################################################
// Copyright: Zero ASIC. All rights Reserved.
// Author: Andreas Olofsson
// License:  MIT (see LICENSE file in LogikBench repository)
//#############################################################################
//
// Weight-stationary systolic matrix-multiply tile (a Google-TPU-style MXU).
//
// Computes one tile product C = A * B, where A and B are N x N signed operands
// (DW bits) and C is N x N signed (ACCW bits). B is the "weight" matrix, loaded
// into and held by the array; A is the "activation" matrix, streamed through; C
// drains out the bottom. Built from a tpu_array of N x N tpu_pe cells.
//
// Operation has two phases the host drives via valids:
//
//   1. Weight load: assert w_valid for N cycles. w_data carries one weight row
//      per cycle, N signed weights packed { col N-1 ... col 0 }. Because the
//      array loads top-down (shift south), push the BOTTOM matrix row first:
//      cycle k carries B[N-1-k][*]. After N cycles PE(i,j) holds B[i][j].
//
//   2. Compute: assert a_valid while streaming A one row per cycle, a_data
//      packed { col N-1 ... col 0 } = A[m][N-1..0]. Row m of the result, C[m][*]
//      (packed the same way), appears on c_data with c_valid high, 2*N-1 cycles
//      after its A row was accepted.
//
// Time alignment: row i of A is delayed i cycles entering the array (input
// skew) so partial sums chain correctly down each column; column j of the
// result is delayed N-1-j cycles (output de-skew) so a full result row leaves
// together. Both skew networks and the valid pipeline advance only with the
// array (compute enable), so the 2*N-1 latency is exact. Per CLAUDE.md the skew
// chains are generated per-row/column shift registers, not a procedural unroll.
//
//#############################################################################

module tpu
  #(parameter N = 8,	 // array dimension (N x N)
    parameter DW = 8,	 // operand width (signed)
    parameter ACCW = 32) // result / accumulator width (signed)
   (
    input		clk,
    input		rst,	 // synchronous, active high
    input		w_valid, // weight-row valid (load phase)
    input [N*DW-1:0]	w_data,	 // weight row, packed { col N-1 ... col 0 }
    input		a_valid, // activation-row valid (compute phase)
    input [N*DW-1:0]	a_data,	 // activation row, packed { col N-1 ... col 0 }
    output		c_valid, // result-row valid
    output [N*ACCW-1:0]	c_data	 // result row, packed { col N-1 ... col 0 }
    );

   // total injection-to-output latency, in compute cycles
   localparam LAT = 2*N - 1;

   // load weights while w_valid; otherwise the array computes (and drains)
   wire	      ld = w_valid;
   wire	      en = ~w_valid;

   //##########################################################################
   // Input skew: row i of the activation is delayed i cycles. Zero is injected
   // when a_valid is low so idle/drain cycles add nothing to the accumulators.
   //##########################################################################
   wire [N*DW-1:0] a_left;
   genvar	   i;
   generate
      for (i = 0; i < N; i = i + 1) begin : g_skew
         wire [DW-1:0] din =
		       a_valid ? a_data[i*DW +: DW] : {DW{1'b0}};
         if (i == 0) begin : g_skew0
            assign a_left[i*DW +: DW] = din;
         end
         else begin : g_skewn
            reg [DW-1:0] sr [0:i-1];
            integer k;
            always @(posedge clk) begin
               if (rst) begin
                  for (k = 0; k < i; k = k + 1)
                    sr[k] <= {DW{1'b0}};
               end
               else if (en) begin
                  sr[0] <= din;
                  for (k = 1; k < i; k = k + 1)
                    sr[k] <= sr[k-1];
               end
            end
            assign a_left[i*DW +: DW] = sr[i-1];
         end
      end
   endgenerate

   //##########################################################################
   // The systolic array.
   //##########################################################################
   wire [N*ACCW-1:0] c_bot;
   tpu_array #(.N(N), .DW(DW), .ACCW(ACCW)) u_array
     (.clk(clk), .rst(rst), .ld(ld), .en(en),
      .w_top(w_data), .a_left(a_left), .c_bot(c_bot));

   //##########################################################################
   // Output de-skew: column j is delayed N-1-j cycles so all columns of a
   // result row line up. Column N-1 (longest path) needs no delay.
   //##########################################################################
   genvar j;
   generate
      for (j = 0; j < N; j = j + 1) begin : g_deskew
         localparam D = N - 1 - j;
         wire [ACCW-1:0] cin = c_bot[j*ACCW +: ACCW];
         if (D == 0) begin : g_deskew0
            assign c_data[j*ACCW +: ACCW] = cin;
         end
         else begin : g_deskewn
            reg [ACCW-1:0] dr [0:D-1];
            integer k;
            always @(posedge clk) begin
               if (rst) begin
                  for (k = 0; k < D; k = k + 1)
                    dr[k] <= {ACCW{1'b0}};
               end
               else if (en) begin
                  dr[0] <= cin;
                  for (k = 1; k < D; k = k + 1)
                    dr[k] <= dr[k-1];
               end
            end
            assign c_data[j*ACCW +: ACCW] = dr[D-1];
         end
      end
   endgenerate

   //##########################################################################
   // Valid pipeline: c_valid is a_valid delayed LAT compute cycles.
   //##########################################################################
   reg [LAT-1:0] vpipe;
   always @(posedge clk) begin
      if (rst)
        vpipe <= {LAT{1'b0}};
      else if (en)
        vpipe <= {vpipe[LAT-2:0], a_valid};
   end
   assign c_valid = vpipe[LAT-1];

endmodule
