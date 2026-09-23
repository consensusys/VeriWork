const { expect } = require("chai");
const { ethers } = require("hardhat");
const { deployCore } = require("./helpers");

describe("PoAWStaking", () => {
  async function asRollup(ctx) {
    const rollupAddr = await ctx.rollup.getAddress();
    await ethers.provider.send("hardhat_impersonateAccount", [rollupAddr]);
    await ethers.provider.send("hardhat_setBalance", [rollupAddr, "0x1000000000000000000"]);
    return ethers.getSigner(rollupAddr);
  }

  it("bonds, delegates and reports sigma = own + delegated", async () => {
    const { vwc, staking, w1, alice } = await deployCore();
    await vwc.connect(w1).approve(await staking.getAddress(), ethers.parseEther("1000"));
    await staking.connect(w1).bond(ethers.parseEther("1000"));
    await vwc.connect(alice).approve(await staking.getAddress(), ethers.parseEther("500"));
    await staking.connect(alice).delegate(w1.address, ethers.parseEther("500"));
    expect(await staking.stakeOf(w1.address)).to.equal(ethers.parseEther("1500"));
    expect(await staking.isEligible(w1.address)).to.equal(true);
    expect(await staking.delegationOf(w1.address, alice.address)).to.equal(ethers.parseEther("500"));
  });

  it("applies sigma <- sigma(1 - lambda*eps) pro-rata, including each delegator's position (Eq. 1)", async () => {
    const ctx = await deployCore();
    const { vwc, staking, w1, alice, w2 } = ctx;
    await vwc.connect(w1).approve(await staking.getAddress(), ethers.parseEther("1000"));
    await staking.connect(w1).bond(ethers.parseEther("1000"));
    await vwc.connect(alice).approve(await staking.getAddress(), ethers.parseEther("500"));
    await staking.connect(alice).delegate(w1.address, ethers.parseEther("500"));
    await staking.connect(await asRollup(ctx)).slash(w1.address, ethers.parseEther("0.2"));   // eps 0.2, lambda 0.5
    expect(await staking.stakeOf(w1.address)).to.equal(ethers.parseEther("1350"));
    const n = await staking.nodes(w1.address);
    expect(n.own).to.equal(ethers.parseEther("900"));
    expect(n.delegated).to.equal(ethers.parseEther("450"));
    expect(await staking.delegationOf(w1.address, alice.address)).to.equal(ethers.parseEther("450"));
    // a later delegator is not diluted by the earlier slash
    await vwc.connect(w2).approve(await staking.getAddress(), ethers.parseEther("450"));
    await staking.connect(w2).delegate(w1.address, ethers.parseEther("450"));
    expect(await staking.delegationOf(w1.address, w2.address)).to.equal(ethers.parseEther("450"));
    expect(await staking.delegationOf(w1.address, alice.address)).to.equal(ethers.parseEther("450"));
  });

  it("enforces the per-service restaking cap and re-caps after a slash", async () => {
    const ctx = await deployCore();
    const { vwc, staking, w1 } = ctx;
    await vwc.connect(w1).approve(await staking.getAddress(), ethers.parseEther("1000"));
    await staking.connect(w1).bond(ethers.parseEther("1000"));
    const svc = ethers.keccak256(ethers.toUtf8Bytes("bridge:polygon"));
    await staking.connect(w1).restake(svc, ethers.parseEther("250"));
    await expect(staking.connect(w1).restake(svc, 1n)).to.be.revertedWith("restake cap");
    await staking.connect(await asRollup(ctx)).slash(w1.address, ethers.parseEther("0.4"));   // own -> 800
    expect(await staking.restaked(w1.address, svc)).to.equal(ethers.parseEther("200"));      // 25 % of 800
  });

  it("rejects slashing from non-rollup callers", async () => {
    const { staking, w1, alice } = await deployCore();
    await expect(staking.connect(alice).slash(w1.address, 1n)).to.be.revertedWith("not rollup");
  });
});
