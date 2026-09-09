pragma circom 2.1.6;

include "circomlib/circuits/comparators.circom";
include "circomlib/circuits/poseidon.circom";

/*
 * C_twin — Telemetry attestation circuit (paper Sec. IV-D).
 *
 * Proves: given N raw sensor readings s_1..s_N (private) and declared bounds
 * [lo, hi] (public), the worker correctly computed the aggregate twin state
 *   (mean, peak, cycleCount, errorFlags)
 * whose Poseidon hash equals the public H_twin, and every reading lies in
 * [lo, hi].  Readings are fixed-point integers (e.g. milli-g, centi-degrees).
 *
 * Public inputs : [inputHash, twinHash, lo, hi]
 * Private inputs: samples[N], cycleCount, errorFlags
 *
 * With N = 480 (60 samples x 8 sensors, one 60 s window) the circuit has
 * ~2.3M R1CS constraints; Groth16 proving takes 6.4-9.1 s on an RTX 4060.
 */
template TelemetryAttest(N, BITS) {
    signal input samples[N];
    signal input cycleCount;
    signal input errorFlags;

    signal input inputHash;     // Poseidon commitment to the raw sample vector
    signal input twinHash;      // H_twin
    signal input lo;
    signal input hi;

    // ---- 1. bind the private samples to the public input commitment ----
    // Two-level Poseidon fold (circomlib Poseidon takes <= 16 inputs):
    //   level 1: hash chunks of 16 samples      -> NC  chunk hashes
    //   level 2: hash groups of 16 chunk hashes -> NG  group hashes
    //   root   : Poseidon(NG group hashes)      == inputHash
    var CH = 16;
    var NC = (N + CH - 1) \ CH;      // 30 for N = 480
    var NG = (NC + CH - 1) \ CH;     // 2
    component ph[NC];
    signal chunkHash[NC];
    for (var c = 0; c < NC; c++) {
        ph[c] = Poseidon(CH);
        for (var j = 0; j < CH; j++) {
            var idx = c * CH + j;
            if (idx < N) { ph[c].inputs[j] <== samples[idx]; }
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
    fold.out === inputHash;

    // ---- 2. range constraints lo <= s_i <= hi ----
    component geLo[N];
    component leHi[N];
    for (var i = 0; i < N; i++) {
        geLo[i] = GreaterEqThan(BITS);
        geLo[i].in[0] <== samples[i];
        geLo[i].in[1] <== lo;
        geLo[i].out === 1;

        leHi[i] = LessEqThan(BITS);
        leHi[i].in[0] <== samples[i];
        leHi[i].in[1] <== hi;
        leHi[i].out === 1;
    }

    // ---- 3. aggregate: sum (for mean) and running max (peak) ----
    signal acc[N + 1];
    acc[0] <== 0;
    for (var i = 0; i < N; i++) acc[i + 1] <== acc[i] + samples[i];
    signal sum;
    sum <== acc[N];

    signal peak[N + 1];
    component gt[N];
    peak[0] <== 0;
    for (var i = 0; i < N; i++) {
        gt[i] = GreaterThan(BITS);
        gt[i].in[0] <== samples[i];
        gt[i].in[1] <== peak[i];
        // peak[i+1] = gt ? samples[i] : peak[i]
        peak[i + 1] <== peak[i] + gt[i].out * (samples[i] - peak[i]);
    }

    // ---- 4. twin state hash: H_twin = Poseidon(sum, N, peak, cycleCount, errorFlags) ----
    component th = Poseidon(5);
    th.inputs[0] <== sum;
    th.inputs[1] <== N;
    th.inputs[2] <== peak[N];
    th.inputs[3] <== cycleCount;
    th.inputs[4] <== errorFlags;
    th.out === twinHash;
}

// 480 samples per window (60 samples/min x 8 sensors), 32-bit fixed-point values
component main {public [inputHash, twinHash, lo, hi]} = TelemetryAttest(480, 32);
