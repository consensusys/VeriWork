pragma circom 2.1.6;

include "../lib/telemetry.circom";

// Small instance of C_twin (2 sensors x 4 samples) with the same public
// interface as the production circuit; used by the Hardhat Groth16 tests.
component main {public [prevStateRoot, healthScore, cycleCount, machineId, submitter]} = TelemetryAttest(2, 4, 32);
