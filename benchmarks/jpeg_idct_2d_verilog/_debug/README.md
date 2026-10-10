# Vector generation (original RTL = oracle)

1. `python3 gen_vectors.py` -> `vectors_in.dat` (323 input blocks: directed
   corners incl. full-range/wrap-exercising extremes + random full-range,
   JPEG-realistic and sparse blocks).
2. Bootstrap `../vectors.dat` with 64 zero placeholders per line:
   `awk '!/^#/ {printf "%s", $0; for(i=0;i<64;i++) printf " 00000000"; print ""}' vectors_in.dat > ../vectors.dat`
3. Simulate the ORIGINAL RTL with the benchmark tb in dump mode:
   `verilator --binary --timing -Wno-WIDTH -Wno-UNUSED -Wno-DECLFILENAME +define+DUMP_Y --top-module tb -o sim ../context/starting_point.v ../tb.sv`
   and collect the `Y xxxxxxxx` lines (ignore the placeholder-mismatch errors).
4. Merge: replace the placeholder column with the dumped outputs (64 per block,
   in stream order) -> `../vectors.dat`. Re-run without DUMP_Y -> PASS.

The same vectors.dat is shared byte-identically with `jpeg_idct_2d_spire`.
