const { expect } = require("chai");
const { ethers } = require("hardhat");
const { R, mockProof, badProof, deployCore } = require("./helpers");

describe("VeriWorkRollup + SequencerElection", () => {
  async function bondAll(ctx, workers) {
    for (const w of workers) {
      await ctx.vwc.connect(w).approve(await ctx.staking.getAddress(), ethers.parseEther("5000"));
      await ctx.staking.connect(w).bond(ethers.parseEther("5000"));
    }
  }

  it("elects a committee from PoUW-scored, bonded nodes and accepts a valid batch", async () => {
    const ctx = await deployCore();
    const { rollup, election, staking, w1, w2, w3, genesis, gov } = ctx;
    await bondAll(ctx, [w1, w2, w3]);
    await election.connect(gov).setCommittee(2, 2);   // top-2 by S_i eligible

    // bootstrap: with no committee anyone may advance the first epoch — but
    // nobody has stats yet, so seed stats via an impersonated rollup call
    const rollupAddr = await rollup.getAddress();
    await ethers.provider.send("hardhat_impersonateAccount", [rollupAddr]);
    await ethers.provider.send("hardhat_setBalance", [rollupAddr, "0x1000000000000000000"]);
    const asRollup = await ethers.getSigner(rollupAddr);
    for (let i = 0; i < 10; i++) {
      await election.connect(asRollup).recordOutcome(w1.address, true, 1, ethers.parseEther("1"));
      await election.connect(asRollup).recordOutcome(w2.address, true, 1, ethers.parseEther("0.9"));
      await election.connect(asRollup).recordOutcome(w3.address, i < 2, 1, ethers.parseEther("1")); // 80% invalid
    }
    await rollup.connect(w1).advanceEpoch(ethers.keccak256(ethers.toUtf8Bytes("beacon-1")));
    expect(await election.committeeSizeNow()).to.equal(2);
    expect(await election.isSequencer(w3.address)).to.equal(false);   // low S_i => not in top-2

    const seq = (await election.isSequencer(w1.address)) ? w1 : w2;
    const stateRoot = ethers.keccak256(ethers.toUtf8Bytes("root-1"));
    const orderRoot = ethers.keccak256(ethers.toUtf8Bytes("order-1"));
    const pub = [BigInt(genesis) % R, BigInt(stateRoot) % R, BigInt(orderRoot) % R, 100n, 2n];
    const { a, b, c } = mockProof(pub);
    await expect(rollup.connect(seq).postBatch(stateRoot, orderRoot, 100, 2, false, a, b, c))
      .to.emit(rollup, "BatchPosted");
    expect(await rollup.latestRoot()).to.equal(stateRoot);
    expect(await rollup.batchCount()).to.equal(1);

    // non-sequencer and bad proof are rejected
    await expect(rollup.connect(w3).postBatch(stateRoot, orderRoot, 1, 0, false, a, b, c))
      .to.be.revertedWith("not elected sequencer");
    const bp = badProof();
    await expect(rollup.connect(seq).postBatch(stateRoot, orderRoot, 1, 0, false, bp.a, bp.b, bp.c))
      .to.be.revertedWith("invalid aggregated proof");
  });

  it("slashes objectively from reported epsilon", async () => {
    const ctx = await deployCore();
    const { rollup, election, staking, w1, w2, gov } = ctx;
    await bondAll(ctx, [w1, w2]);
    await election.connect(gov).setCommittee(2, 1);
    const rollupAddr = await rollup.getAddress();
    await ethers.provider.send("hardhat_impersonateAccount", [rollupAddr]);
    await ethers.provider.send("hardhat_setBalance", [rollupAddr, "0x1000000000000000000"]);
    const asRollup = await ethers.getSigner(rollupAddr);
    await election.connect(asRollup).recordOutcome(w1.address, true, 1, ethers.parseEther("1"));
    await rollup.connect(w1).advanceEpoch(ethers.keccak256(ethers.toUtf8Bytes("b")));
    const seq = (await election.isSequencer(w1.address)) ? w1 : w2;
    await rollup.connect(seq).reportEpoch(w2.address, 6, 10, 6, ethers.parseEther("1"));   // eps = 0.4
    expect(await staking.stakeOf(w2.address)).to.equal(ethers.parseEther("4000"));       // 5000 * (1 - 0.5*0.4)
  });
});
