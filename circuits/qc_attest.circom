pragma circom 2.1.6;

include "circomlib/circuits/comparators.circom";
include "circomlib/circuits/poseidon.circom";

/*
 * C_qc — Quality-control attestation (paper Sec. IV-D).
 *
 * Proves that a manufactured batch passed dimensional inspection:
 *   for all i: lower[i] <= m[i] <= upper[i]
 * without revealing the measurement vector m.  The public batchHash commits
 * to (m, batchId) so the same proof cannot be reused for another batch.
 *
 * Public inputs : [batchHash, lower[K], upper[K]]
 * Private inputs: m[K], batchId
 */
template QCAttest(K, BITS) {
    signal input m[K];
    signal input batchId;
    signal input batchHash;
    signal input lower[K];
    signal input upper[K];

    component h = Poseidon(K + 1);
    for (var i = 0; i < K; i++) h.inputs[i] <== m[i];
    h.inputs[K] <== batchId;
    h.out === batchHash;

    component ge[K];
    component le[K];
    for (var i = 0; i < K; i++) {
        ge[i] = GreaterEqThan(BITS);
        ge[i].in[0] <== m[i];
        ge[i].in[1] <== lower[i];
        ge[i].out === 1;

        le[i] = LessEqThan(BITS);
        le[i].in[0] <== m[i];
        le[i].in[1] <== upper[i];
        le[i].out === 1;
    }
}

// 12 dimensional checks per batch, micrometre fixed-point
component main {public [batchHash, lower, upper]} = QCAttest(12, 32);
