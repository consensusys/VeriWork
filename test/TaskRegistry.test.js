const { expect } = require("chai");
const { ethers } = require("hardhat");
const { mockProof, twinSignals, deployCore } = require("./helpers");

describe("TaskRegistry", () => {
  const INPUT = ethers.toBeHex(1234n, 32);     // H_in
  const BOUNDS = 99n;                          // Poseidon(lo[], hi[])

  async function newTask(ctx) {
    const { tasks, vwc, alice } = ctx;
    await vwc.connect(alice).approve(await tasks.getAddress(), ethers.MaxUint256);
    const deadline = (await ethers.provider.getBlock("latest")).timestamp + 3600;
    const tx = await tasks.connect(alice).createTask(INPUT, BOUNDS, deadline, ethers.parseEther("10"));
    const ev = (await tx.wait()).logs.map((l) => tasks.interface.parseLog(l)).find((e) => e && e.name === "TaskCreated");
    return ev.args.id;
  }

  function proofFor(ctx, id, worker, outputHash = 777n) {
    const pub = twinSignals({ newRoot: outputHash, inputHash: INPUT, boundsHash: BOUNDS, prevRoot: 0n,
                              health: 88, cycle: 5, machineId: BigInt(id) % 21888242871839275222246405745257275088548364400416034343698204186575808495617n,
                              submitter: worker.address });
    return mockProof(pub);
  }

  it("pays the worker the proof is bound to", async () => {
    const ctx = await deployCore();
    const { tasks, vwc, w1 } = ctx;
    const id = await newTask(ctx);
    const p = proofFor(ctx, id, w1);
    const before = await vwc.balanceOf(w1.address);
    await expect(tasks.connect(w1).submitResult(id, ethers.toBeHex(777n, 32), 88, 5, "bafy-1", p.a, p.b, p.c))
      .to.emit(tasks, "TaskVerified");
    expect((await vwc.balanceOf(w1.address)) - before).to.equal(ethers.parseEther("10"));
    expect((await tasks.outcomes(w1.address))[0]).to.equal(1);
  });

  it("a proof copied from the mempool fails for the copier and stays claimable by its prover", async () => {
    const ctx = await deployCore();
    const { tasks, w1, w2 } = ctx;
    const id = await newTask(ctx);
    const p = proofFor(ctx, id, w1);                         // w1's proof
    await expect(tasks.connect(w2).submitResult(id, ethers.toBeHex(777n, 32), 88, 5, "bafy-1", p.a, p.b, p.c))
      .to.emit(tasks, "TaskRejected").withArgs(id, w2.address);
    expect(await tasks.invalidCount(w2.address)).to.equal(1);
    await expect(tasks.connect(w1).submitResult(id, ethers.toBeHex(777n, 32), 88, 5, "bafy-1", p.a, p.b, p.c))
      .to.emit(tasks, "TaskVerified");
  });

  it("a proof cannot be replayed on another task with the same inputs", async () => {
    const ctx = await deployCore();
    const { tasks, w1 } = ctx;
    const id1 = await newTask(ctx);
    const id2 = await newTask(ctx);
    const p = proofFor(ctx, id1, w1);
    await expect(tasks.connect(w1).submitResult(id2, ethers.toBeHex(777n, 32), 88, 5, "bafy-1", p.a, p.b, p.c))
      .to.emit(tasks, "TaskRejected");
  });
});
