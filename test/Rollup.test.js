const { expect } = require("chai");
const { ethers } = require("hardhat");
const { R, mockProof, badProof, fieldId, twinSignals, deployCore } = require("./helpers");

describe("VeriWorkRollup + SequencerElection", () => {
  const BOUNDS = 1n;

  async function bondAll(ctx, workers) {
    for (const w of workers) {
      await ctx.vwc.connect(w).approve(await ctx.staking.getAddress(), ethers.parseEther("5000"));
      await ctx.staking.connect(w).bond(ethers.parseEther("5000"));
    }
  }

  /** Submit `nValid` valid and `nInvalid` invalid twin proofs as `worker` on its own machine. */
  async function attest(ctx, worker, nValid, nInvalid) {
    const machine = fieldId(`machine-of-${worker.address}`);
    if ((await ctx.twins.operators(machine)) === ethers.ZeroAddress)
      await ctx.twins.connect(ctx.gov).registerMachine(machine, worker.address, BOUNDS);
    let t = await ctx.twins.twins(machine);
    let cycle = t.cycleCount, root = t.stateRoot;
    for (let i = 0; i < nValid + nInvalid; i++) {
      cycle += 1n;
      const newRoot = ethers.toBeHex(BigInt(ethers.keccak256(ethers.toUtf8Bytes(`${machine}${cycle}`))) % R, 32);
      const valid = i < nValid;
      const p = valid ? mockProof(twinSignals({ newRoot, inputHash: 5n, boundsHash: BOUNDS, prevRoot: BigInt(root),
                                                health: 90, cycle, machineId: machine, submitter: worker.address }))
                      : badProof();
      await ctx.twins.connect(worker).updateTwinState(machine, newRoot, ethers.toBeHex(5n, 32), 90, cycle, p.a, p.b, p.c);
      if (valid) root = newRoot;
    }
  }

  it("elects the committee from on-chain verification outcomes and accepts a valid batch", async () => {
    const ctx = await deployCore();
    const { rollup, election, staking, w1, w2, w3, genesis, gov } = ctx;
    await bondAll(ctx, [w1, w2, w3]);
    await election.connect(gov).setCommittee(2, 2);          // top-2 by S_i are eligible
    await attest(ctx, w1, 10, 0);
    await attest(ctx, w2, 9, 1);
    await attest(ctx, w3, 2, 8);                               // 80 % invalid
    await rollup.advanceEpoch();                               // bootstrap: no committee yet
    expect(await election.committeeSizeNow()).to.equal(2);
    expect(await election.isSequencer(w3.address)).to.equal(false);
    expect(await staking.stakeOf(w3.address)).to.equal(ethers.parseEther("3000"));   // 5000*(1-min(.5,.5*.8))

    const seq = (await election.isSequencer(w1.address)) ? w1 : w2;
    const stateRoot = ethers.keccak256(ethers.toUtf8Bytes("root-1"));
    const orderRoot = ethers.keccak256(ethers.toUtf8Bytes("order-1"));
    const pub = [BigInt(genesis) % R, BigInt(stateRoot) % R, BigInt(orderRoot) % R, 100n, 2n];
    const { a, b, c } = mockProof(pub);
    await expect(rollup.connect(seq).postBatch(stateRoot, orderRoot, 100, 2, false, a, b, c))
      .to.emit(rollup, "BatchPosted");
    expect(await rollup.latestRoot()).to.equal(stateRoot);
    await expect(rollup.connect(w3).postBatch(stateRoot, orderRoot, 1, 0, false, a, b, c))
      .to.be.revertedWith("not elected sequencer");
    const bp = badProof();
    await expect(rollup.connect(seq).postBatch(stateRoot, orderRoot, 1, 0, false, bp.a, bp.b, bp.c))
      .to.be.revertedWith("invalid aggregated proof");
  });

  it("slashes only from recorded outcomes: eps = invalid / submitted, settled once", async () => {
    const ctx = await deployCore();
    const { rollup, staking, election, w1, w2, gov } = ctx;
    await bondAll(ctx, [w1, w2]);
    await election.connect(gov).setCommittee(2, 1);
    await attest(ctx, w1, 5, 0);
    await attest(ctx, w2, 6, 4);                               // eps = 0.4
    const [, , eps] = await rollup.pendingEpsilon(w2.address);
    expect(eps).to.equal(ethers.parseEther("0.4"));
    await rollup.advanceEpoch();
    expect(await staking.stakeOf(w2.address)).to.equal(ethers.parseEther("4000"));   // 5000*(1-0.5*0.4)
    expect(await staking.stakeOf(w1.address)).to.equal(ethers.parseEther("5000"));   // all proofs verified
    // settling again without new outcomes changes nothing
    const seq = (await election.isSequencer(w1.address)) ? w1 : w2;
    await rollup.connect(seq).advanceEpoch();
    expect(await staking.stakeOf(w2.address)).to.equal(ethers.parseEther("4000"));
    // no entry point lets a sequencer (or anyone) supply epsilon directly
    expect(rollup.interface.getFunction("reportEpoch")).to.equal(null);
  });

  it("only sequencers advance epochs, except after the liveness timeout", async () => {
    const ctx = await deployCore();
    const { rollup, election, w1, w2, w3, alice, gov } = ctx;
    await bondAll(ctx, [w1, w2, w3]);
    await election.connect(gov).setCommittee(3, 1);
    await attest(ctx, w1, 3, 0); await attest(ctx, w2, 3, 0); await attest(ctx, w3, 3, 0);
    await rollup.advanceEpoch();
    await expect(rollup.connect(alice).advanceEpoch()).to.be.revertedWith("not sequencer");
    await ethers.provider.send("evm_increaseTime", [86400]);
    await ethers.provider.send("evm_mine", []);
    await expect(rollup.connect(alice).advanceEpoch()).to.not.be.reverted;
  });

  it("uptime is committee-reported, bounded, and never slashes", async () => {
    const ctx = await deployCore();
    const { rollup, election, staking, w1, w2, gov } = ctx;
    await bondAll(ctx, [w1, w2]);
    await election.connect(gov).setCommittee(2, 1);
    await attest(ctx, w1, 2, 0); await attest(ctx, w2, 2, 0);
    await rollup.advanceEpoch();
    const seq = (await election.isSequencer(w1.address)) ? w1 : w2;
    const nonSeq = seq === w1 ? w2 : w1;
    await rollup.connect(seq).reportUptime(nonSeq.address, ethers.parseEther("0.5"));
    expect((await election.stats(nonSeq.address)).uptimeWad).to.equal(ethers.parseEther("0.5"));
    await expect(rollup.connect(seq).reportUptime(nonSeq.address, ethers.parseEther("1.1"))).to.be.revertedWith("uptime > 1");
    await expect(rollup.connect(nonSeq).reportUptime(seq.address, 0)).to.be.revertedWith("not sequencer");
    expect(await staking.stakeOf(nonSeq.address)).to.equal(ethers.parseEther("5000"));
  });
});
