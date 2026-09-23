// End-to-end test with REAL Groth16 proofs: the small C_twin instance
// (circuits/test/telemetry_attest_test.circom, 2 sensors x 4 samples) proved
// with snarkjs and verified by its exported Solidity verifier.
// Build the artifacts first:  (cd circuits && npm install && ./build_test.sh)
// The suite is skipped when they are missing.
const { expect } = require("chai");
const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");
const { fieldId } = require("./helpers");

const CIRC = path.join(__dirname, "..", "circuits");
const WASM = path.join(CIRC, "build/test/telemetry_attest_test_js/telemetry_attest_test.wasm");
const ZKEY = path.join(CIRC, "build/test/telemetry_attest_test_final.zkey");
const VERIFIER = path.join(__dirname, "..", "contracts/generated/TwinVerifierTest.sol");
const READY = [WASM, ZKEY, VERIFIER, path.join(CIRC, "node_modules/snarkjs")].every(fs.existsSync);

const LO = [0n, 0n];
const HI = [60n, 300n];
const SAMPLES = [[10n, 20n, 30n, 40n], [100n, 200n, 150n, 50n]];

describe("Groth16 end-to-end (real C_twin proofs)", function () {
  this.timeout(300000);
  let snarkjs, boundsHash, ctx;

  before(async function () {
    if (!READY) this.skip();
    snarkjs = require(path.join(CIRC, "node_modules/snarkjs"));
    ({ boundsHash } = require(path.join(CIRC, "bounds_hash.js")));
  });

  after(async () => { if (globalThis.curve_bn128) await globalThis.curve_bn128.terminate(); });

  async function prove(input) {
    const { proof, publicSignals } = await snarkjs.groth16.fullProve(
      Object.fromEntries(Object.entries(input).map(([k, v]) => [k, JSON.parse(JSON.stringify(v, (_, x) =>
        typeof x === "bigint" ? x.toString() : x))])), WASM, ZKEY);
    const cd = await snarkjs.groth16.exportSolidityCallData(proof, publicSignals);
    const [a, b, c, pub] = JSON.parse(`[${cd}]`);
    return { a, b, c, pub: pub.map(BigInt) };
  }

  function witnessInput({ prevStateRoot = 0n, healthScore, cycleCount, machineId, submitter,
                          samples = SAMPLES, lo = LO, hi = HI }) {
    return { prevStateRoot, healthScore, cycleCount, machineId: BigInt(machineId), submitter: BigInt(submitter),
             samples, lo, hi, errorFlags: 0n };
  }

  beforeEach(async () => {
    const [deployer, gov, operator, stranger, requester] = await ethers.getSigners();
    const vwc = await ethers.deployContract("VWCToken", [ethers.parseEther("1000000"), deployer.address]);
    const verifier = await ethers.deployContract("TwinVerifierTest");
    const twins = await ethers.deployContract("FlexFactoryTwinRegistry",
      [await verifier.getAddress(), await vwc.getAddress(), gov.address]);
    const tasks = await ethers.deployContract("TaskRegistry", [await verifier.getAddress(), await vwc.getAddress()]);
    const bh = await boundsHash(LO, HI);
    const machine = fieldId("press-07");
    const other = fieldId("press-08");
    await twins.connect(gov).registerMachine(machine, operator.address, bh);
    await twins.connect(gov).registerMachine(other, operator.address, bh);
    await vwc.transfer(requester.address, ethers.parseEther("100"));
    ctx = { gov, operator, stranger, requester, vwc, verifier, twins, tasks, bh, machine, other };
  });

  it("the registrar's bounds hash equals the circuit's (circomlibjs Poseidon)", async () => {
    const p = await prove(witnessInput({ healthScore: 90n, cycleCount: 1n, machineId: ctx.machine, submitter: ctx.operator.address }));
    expect(p.pub[2]).to.equal(ctx.bh);
    expect(p.pub.slice(3)).to.deep.equal([0n, 90n, 1n, BigInt(ctx.machine), BigInt(ctx.operator.address)]);
  });

  it("accepts a real proof, chains the next one, and reports gas", async () => {
    const { operator, twins, machine } = ctx;
    const p1 = await prove(witnessInput({ healthScore: 35n, cycleCount: 100n, machineId: machine, submitter: operator.address }));
    const tx = await twins.connect(operator).updateTwinState(machine, ethers.toBeHex(p1.pub[0], 32),
      ethers.toBeHex(p1.pub[1], 32), 35, 100, p1.a, p1.b, p1.c);
    await expect(tx).to.emit(twins, "TwinUpdated").and.to.emit(twins, "MaintenanceTriggered");
    const gas = (await tx.wait()).gasUsed;
    console.log(`      updateTwinState with a real Groth16 verifier: ${gas} gas`);
    const p2 = await prove(witnessInput({ prevStateRoot: p1.pub[0], healthScore: 91n, cycleCount: 160n,
                                          machineId: machine, submitter: operator.address }));
    const tx2 = await twins.connect(operator).updateTwinState(machine, ethers.toBeHex(p2.pub[0], 32),
      ethers.toBeHex(p2.pub[1], 32), 91, 160, p2.a, p2.b, p2.c);
    await expect(tx2).to.emit(twins, "TwinUpdated");
    console.log(`      subsequent update of the same machine:       ${(await tx2.wait()).gasUsed} gas`);
    expect(await twins.goodProofs(operator.address)).to.equal(2);
  });

  it("rejects replay on another machine and an edited health score (recorded as invalid)", async () => {
    const { operator, twins, machine, other } = ctx;
    const p = await prove(witnessInput({ healthScore: 20n, cycleCount: 7n, machineId: machine, submitter: operator.address }));
    const root = ethers.toBeHex(p.pub[0], 32), hin = ethers.toBeHex(p.pub[1], 32);
    await expect(twins.connect(operator).updateTwinState(other, root, hin, 20, 7, p.a, p.b, p.c))
      .to.emit(twins, "InvalidProof");
    await expect(twins.connect(operator).updateTwinState(machine, root, hin, 95, 7, p.a, p.b, p.c))
      .to.emit(twins, "InvalidProof");
    expect(await twins.badProofs(operator.address)).to.equal(2);
    await expect(twins.connect(operator).updateTwinState(machine, root, hin, 20, 7, p.a, p.b, p.c))
      .to.emit(twins, "TwinUpdated");
  });

  it("a proof under bounds other than the registered ones fails verification", async () => {
    const { operator, twins, machine } = ctx;
    const p = await prove(witnessInput({ healthScore: 80n, cycleCount: 3n, machineId: machine, submitter: operator.address,
                                         hi: [1000n, 1000n] }));           // looser bounds
    await expect(twins.connect(operator).updateTwinState(machine, ethers.toBeHex(p.pub[0], 32),
      ethers.toBeHex(p.pub[1], 32), 80, 3, p.a, p.b, p.c)).to.emit(twins, "InvalidProof");
  });

  // (the witness calculator prints "Error in template TelemetryAttest" for these: expected)
  it("no proof exists for out-of-bounds readings or health > 100", async () => {
    const { operator, machine } = ctx;
    const tampered = [[10n, 20n, 30n, 61n], [100n, 200n, 150n, 50n]];      // 61 > hi[0] = 60
    await expect(prove(witnessInput({ healthScore: 80n, cycleCount: 3n, machineId: machine, submitter: operator.address,
                                      samples: tampered }))).to.be.rejected;
    await expect(prove(witnessInput({ healthScore: 101n, cycleCount: 3n, machineId: machine, submitter: operator.address })))
      .to.be.rejected;
  });

  it("TaskRegistry: a real proof pays its prover and fails for a copier", async () => {
    const { tasks, vwc, requester, operator: worker, stranger } = ctx;
    // requester learns H_in / boundsHash of the data it publishes (dry run)
    const dry = await prove(witnessInput({ healthScore: 70n, cycleCount: 1n, machineId: 1n, submitter: 1n }));
    await vwc.connect(requester).approve(await tasks.getAddress(), ethers.MaxUint256);
    const deadline = (await ethers.provider.getBlock("latest")).timestamp + 3600;
    const tx = await tasks.connect(requester).createTask(ethers.toBeHex(dry.pub[1], 32), dry.pub[2], deadline, ethers.parseEther("10"));
    const id = (await tx.wait()).logs.map((l) => tasks.interface.parseLog(l)).find((e) => e && e.name === "TaskCreated").args.id;
    const p = await prove(witnessInput({ healthScore: 70n, cycleCount: 1n, machineId: await tasks.circuitTaskId(id),
                                         submitter: worker.address }));
    const out = ethers.toBeHex(p.pub[0], 32);
    await expect(tasks.connect(stranger).submitResult(id, out, 70, 1, "bafy", p.a, p.b, p.c)).to.emit(tasks, "TaskRejected");
    await expect(tasks.connect(worker).submitResult(id, out, 70, 1, "bafy", p.a, p.b, p.c)).to.emit(tasks, "TaskVerified");
    expect(await vwc.balanceOf(worker.address)).to.equal(ethers.parseEther("10"));
  });
});
