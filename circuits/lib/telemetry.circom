pragma circom 2.1.6;

include "circomlib/circuits/bitify.circom";
include "circomlib/circuits/comparators.circom";
include "circomlib/circuits/poseidon.circom";

/*
 * H_in: Poseidon commitment to the raw readings, folded as a 16-ary tree
 * because circomlib's Poseidon takes at most 16 inputs.
 *   level 1: hash chunks of 16 samples      -> NC chunk hashes
 *   level 2: hash groups of 16 chunk hashes -> NG group hashes
 *   root   : Poseidon(NG group hashes)
 * (Same construction as the v2.0 circuit, so existing H_in values stay valid.)
 */
template TwinInputHash(N) {
    signal input in[N];
    signal output out;

    var CH = 16;
    var NC = (N + CH - 1) \ CH;
    var NG = (NC + CH - 1) \ CH;

    component ph[NC];
    signal chunkHash[NC];
    for (var c = 0; c < NC; c++) {
        ph[c] = Poseidon(CH);
        for (var j = 0; j < CH; j++) {
            var idx = c * CH + j;
            if (idx < N) { ph[c].inputs[j] <== in[idx]; }
            else         { ph[c].inputs[j] <== 0; }
        }
        chunkHash[c] <== ph[c].out;
    }
    component pg[NG];
    signal groupHash[NG];
    for (var g = 0; g < NG; g++) {
        pg[g] = Poseidon(CH);
        for (var j = 0; j < CH; j++) {
            var idx = g * CH + j;
            if (idx < NC) { pg[g].inputs[j] <== chunkHash[idx]; }
            else          { pg[g].inputs[j] <== 0; }
        }
        groupHash[g] <== pg[g].out;
    }
    component fold = Poseidon(NG);
    for (var g = 0; g < NG; g++) fold.inputs[g] <== groupHash[g];
    out <== fold.out;
}

/*
 * C_twin -- telemetry attestation (paper Sec. IV-E, Algorithm 1).
 *
 * For the private readings of one telemetry window (S sensors x M samples):
 *   (1) inputHash  = H_in(samples)                                   [output]
 *   (2) boundsHash = Poseidon(lo[0..S-1], hi[0..S-1]), lo,hi < 2^BITS [output]
 *   (3) lo[s] <= samples[s][j] <= hi[s] for every sensor s and sample j
 *   (4) twinHash   = Poseidon(Poseidon(sum_0..sum_{S-1}, peak_0..peak_{S-1}),
 *                             cycleCount, errorFlags, healthScore)    [output]
 *       i.e. per-sensor sums (-> means) and peaks, the cycle count, the error
 *       flags and the health score are all committed in the new state root
 *   (5) healthScore <= 100
 * prevStateRoot, machineId and submitter have no arithmetic role: they are
 * public inputs bound to the proof, so it cannot be applied to another
 * machine, out of order, or by another sender.
 *
 * Note: the health score itself is produced by the off-circuit model on the
 * edge gateway; the proof commits to it and range-checks it, it does not
 * prove the model's output is correct.
 *
 * Public-signal order (snarkjs: outputs first, then public inputs in
 * declaration order) -- the registry contracts rebuild exactly this vector:
 *   [twinHash, inputHash, boundsHash,
 *    prevStateRoot, healthScore, cycleCount, machineId, submitter]
 */
template TelemetryAttest(S, M, BITS) {
    assert(S >= 1 && 2 * S <= 16);
    var N = S * M;

    // ---- outputs ----
    signal output twinHash;
    signal output inputHash;
    signal output boundsHash;

    // ---- public inputs (keep this order: it is the public-signal order) ----
    signal input prevStateRoot;
    signal input healthScore;
    signal input cycleCount;
    signal input machineId;
    signal input submitter;

    // ---- private inputs ----
    signal input samples[S][M];   // fixed-point readings, sensor-major
    signal input lo[S];
    signal input hi[S];
    signal input errorFlags;

    // (1) commitment to the raw readings
    component hin = TwinInputHash(N);
    for (var s = 0; s < S; s++) {
        for (var j = 0; j < M; j++) hin.in[s * M + j] <== samples[s][j];
    }
    inputHash <== hin.out;

    // (2) per-sensor bounds: BITS-bit (keeps the comparators sound) and hashed
    component loBits[S];
    component hiBits[S];
    component bh = Poseidon(2 * S);
    for (var s = 0; s < S; s++) {
        loBits[s] = Num2Bits(BITS);
        loBits[s].in <== lo[s];
        hiBits[s] = Num2Bits(BITS);
        hiBits[s].in <== hi[s];
        bh.inputs[s] <== lo[s];
        bh.inputs[S + s] <== hi[s];
    }
    boundsHash <== bh.out;

    // (3) range checks, per-sensor running sum and running max
    component geLo[S][M];
    component leHi[S][M];
    component gt[S][M];
    signal acc[S][M + 1];
    signal peak[S][M + 1];
    for (var s = 0; s < S; s++) {
        acc[s][0] <== 0;
        peak[s][0] <== 0;
        for (var j = 0; j < M; j++) {
            geLo[s][j] = GreaterEqThan(BITS);
            geLo[s][j].in[0] <== samples[s][j];
            geLo[s][j].in[1] <== lo[s];
            geLo[s][j].out === 1;

            leHi[s][j] = LessEqThan(BITS);
            leHi[s][j].in[0] <== samples[s][j];
            leHi[s][j].in[1] <== hi[s];
            leHi[s][j].out === 1;

            acc[s][j + 1] <== acc[s][j] + samples[s][j];

            gt[s][j] = GreaterThan(BITS);
            gt[s][j].in[0] <== samples[s][j];
            gt[s][j].in[1] <== peak[s][j];
            // peak' = gt ? sample : peak
            peak[s][j + 1] <== peak[s][j] + gt[s][j].out * (samples[s][j] - peak[s][j]);
        }
    }

    // (5) 0 <= healthScore <= 100
    component hBits = Num2Bits(7);
    hBits.in <== healthScore;
    component hLe = LessEqThan(7);
    hLe.in[0] <== healthScore;
    hLe.in[1] <== 100;
    hLe.out === 1;

    // (4) twin state hash
    component sh = Poseidon(2 * S);
    for (var s = 0; s < S; s++) {
        sh.inputs[s] <== acc[s][M];
        sh.inputs[S + s] <== peak[s][M];
    }
    component th = Poseidon(4);
    th.inputs[0] <== sh.out;
    th.inputs[1] <== cycleCount;
    th.inputs[2] <== errorFlags;
    th.inputs[3] <== healthScore;
    twinHash <== th.out;

    // bind the context-only public inputs explicitly (defence in depth; the
    // Groth16 setup already binds every public input)
    signal prevSq;
    signal idSq;
    signal subSq;
    prevSq <== prevStateRoot * prevStateRoot;
    idSq <== machineId * machineId;
    subSq <== submitter * submitter;
}
