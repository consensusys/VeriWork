pragma circom 2.1.6;

include "./lib/telemetry.circom";

// C_twin for one 60 s window: 8 sensors x 60 samples/min, 32-bit fixed-point
// readings.  Public signals: [twinHash, inputHash, boundsHash,
// prevStateRoot, healthScore, cycleCount, machineId, submitter].
component main {public [prevStateRoot, healthScore, cycleCount, machineId, submitter]} = TelemetryAttest(8, 60, 32);
