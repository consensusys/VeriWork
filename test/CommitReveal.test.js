const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("CommitRevealOrdering", () => {
  it("hides tx contents for one block and enforces canonical order", async () => {
    const [alice, bob] = await ethers.getSigners();
    const cr = await ethers.deployContract("CommitRevealOrdering", [2]);
    const tx = ethers.toUtf8Bytes("swap 500 FXC-A -> SUP-B");
    const r = ethers.randomBytes(32);
    const c = ethers.keccak256(ethers.concat([tx, ethers.toUtf8Bytes("||"), r]));
    await cr.connect(alice).commitTx(c);
    await expect(cr.connect(bob).revealTx(tx, r)).to.be.revertedWith("sender/commit mismatch");
    await cr.connect(alice).revealTx(tx, r);           // next block: ok
    await expect(cr.connect(alice).revealTx(tx, r)).to.be.revertedWith("already");
    const sorted = [c, ethers.keccak256(ethers.toUtf8Bytes("x"))].sort((a, b) => (BigInt(a) < BigInt(b) ? -1 : 1));
    expect(await cr.orderRoot(sorted)).to.not.equal(ethers.ZeroHash);
    await expect(cr.orderRoot([sorted[1], sorted[0]])).to.be.revertedWith("not canonical");
  });
});
