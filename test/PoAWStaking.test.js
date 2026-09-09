const { expect } = require("chai");
const { ethers } = require("hardhat");
const { deployCore } = require("./helpers");

describe("PoAWStaking", () => {
  it("bonds, delegates and reports sigma = own + delegated", async () => {
    const { vwc, staking, w1, alice } = await deployCore();
    await vwc.connect(w1).approve(await staking.getAddress(), ethers.parseEther("1000"));
    await staking.connect(w1).bond(ethers.parseEther("1000"));
    await vwc.connect(alice).approve(await staking.getAddress(), ethers.parseEther("500"));
    await staking.connect(alice).delegate(w1.address, ethers.parseEther("500"));
    expect(await staking.stakeOf(w1.address)).to.equal(ethers.parseEther("1500"));
    expect(await staking.isEligible(w1.address)).to.equal(true);
  });

  it("applies sigma <- sigma(1 - lambda*eps) pro-rata (Eq. 1)", async () => {
    const { vwc, staking, rollup, election, w1, alice, gov } = await deployCore();
    await vwc.connect(w1).approve(await staking.getAddress(), ethers.parseEther("1000"));
    await staking.connect(w1).bond(ethers.parseEther("1000"));
    await vwc.connect(alice).approve(await staking.getAddress(), ethers.parseEther("500"));
    await staking.connect(alice).delegate(w1.address, ethers.parseEther("500"));
    // impersonate the rollup to call slash() directly
    const rollupAddr = await rollup.getAddress();
    await ethers.provider.send("hardhat_impersonateAccount", [rollupAddr]);
    await ethers.provider.send("hardhat_setBalance", [rollupAddr, "0x1000000000000000000"]);
    const asRollup = await ethers.getSigner(rollupAddr);
    await staking.connect(asRollup).slash(w1.address, ethers.parseEther("0.2"));   // eps = 0.2, lambda = 0.5
    expect(await staking.stakeOf(w1.address)).to.equal(ethers.parseEther("1350"));
    const n = await staking.nodes(w1.address);
    expect(n.own).to.equal(ethers.parseEther("900"));
    expect(n.delegated).to.equal(ethers.parseEther("450"));
  });

  it("enforces the per-service restaking cap", async () => {
    const { vwc, staking, w1 } = await deployCore();
    await vwc.connect(w1).approve(await staking.getAddress(), ethers.parseEther("1000"));
    await staking.connect(w1).bond(ethers.parseEther("1000"));
    const svc = ethers.keccak256(ethers.toUtf8Bytes("bridge:polygon"));
    await staking.connect(w1).restake(svc, ethers.parseEther("250"));
    await expect(staking.connect(w1).restake(svc, 1n)).to.be.revertedWith("restake cap");
  });

  it("rejects slashing from non-rollup callers", async () => {
    const { staking, w1, alice } = await deployCore();
    await expect(staking.connect(alice).slash(w1.address, 1n)).to.be.revertedWith("not rollup");
  });
});
