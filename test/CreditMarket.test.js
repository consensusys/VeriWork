const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("CreditMarket", () => {
  async function setup() {
    const [deployer, gov, alice] = await ethers.getSigners();
    const vwc = await ethers.deployContract("VWCToken", [ethers.parseEther("1000000"), deployer.address]);
    const market = await ethers.deployContract("CreditMarket", [await vwc.getAddress(), gov.address]);
    await vwc.approve(await market.getAddress(), ethers.parseEther("100000"));
    const FXA = ethers.keccak256(ethers.toUtf8Bytes("FXC-A"));
    const SUPB = ethers.keccak256(ethers.toUtf8Bytes("SUP-B"));
    await market.registerCredit(FXA, ethers.parseEther("1000"), ethers.parseEther("2000"), 5000);
    await market.registerCredit(SUPB, ethers.parseEther("3000"), ethers.parseEther("1000"), 2500);
    return { deployer, gov, alice, vwc, market, FXA, SUPB };
  }

  it("spot price P = B / (O * W)", async () => {
    const { market, FXA } = await setup();
    expect(await market.spotPrice(FXA)).to.equal(ethers.parseEther("1"));        // 1000/(2000*0.5)
  });

  it("buy raises price; buy-then-sell round-trips within rounding", async () => {
    const { deployer, market, FXA, vwc } = await setup();
    const p0 = await market.spotPrice(FXA);
    const balBefore = await vwc.balanceOf(deployer.address);
    const tx = await market.buy(FXA, ethers.parseEther("100"), 0);
    await tx.wait();
    expect(await market.spotPrice(FXA)).to.be.gt(p0);
    const minted = await market.balanceOf(FXA, deployer.address) - ethers.parseEther("2000");
    await market.sell(FXA, minted, 0);
    const balAfter = await vwc.balanceOf(deployer.address);
    expect(balBefore - balAfter).to.be.lt(ethers.parseEther("0.001"));
  });

  it("swaps credit_a -> credit_b atomically and conserves VWC reserves", async () => {
    const { market, FXA, SUPB, deployer } = await setup();
    const rA0 = (await market.credits(FXA)).reserve, rB0 = (await market.credits(SUPB)).reserve;
    await market.swap(FXA, SUPB, ethers.parseEther("100"), 0);
    const rA1 = (await market.credits(FXA)).reserve, rB1 = (await market.credits(SUPB)).reserve;
    expect(rA0 + rB0).to.equal(rA1 + rB1);
    expect(await market.balanceOf(SUPB, deployer.address)).to.be.gt(ethers.parseEther("1000"));
  });

  it("adaptive multiplier follows P_c = P_0 (1 + alpha D/S) with surge cap", async () => {
    const { market, gov } = await setup();
    expect(await market.adaptiveMultiplier()).to.equal(ethers.parseEther("1"));            // balanced
    for (let i = 0; i < 16; i++) await market.connect(gov).updateDemandSignal(2000, 1000);   // ratio -> 2
    const m = await market.adaptiveMultiplier();
    expect(m).to.be.closeTo(ethers.parseEther("1.35"), ethers.parseEther("0.01"));
    for (let i = 0; i < 20; i++) await market.connect(gov).updateDemandSignal(100000, 1000);
    expect(await market.adaptiveMultiplier()).to.equal(ethers.parseEther("3"));
  });
});
