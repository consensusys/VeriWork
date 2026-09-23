const { expect } = require("chai");
const { ethers } = require("hardhat");
const { R, mockProof, badProof, fieldId, twinSignals } = require("./helpers");

describe("FlexFactoryTwinRegistry", () => {
  const BOUNDS = 123456789n;            // stands in for Poseidon(lo[], hi[])

  async function setup() {
    const [deployer, gov, operator, agent, provider, stranger] = await ethers.getSigners();
    const vwc = await ethers.deployContract("VWCToken", [ethers.parseEther("1000000"), deployer.address]);
    const verifier = await ethers.deployContract("MockGroth16Verifier", [false]);
    const twins = await ethers.deployContract("FlexFactoryTwinRegistry",
      [await verifier.getAddress(), await vwc.getAddress(), gov.address]);
    const machine = fieldId("cnc-01");
    const other = fieldId("cnc-02");
    await twins.connect(gov).registerMachine(machine, operator.address, BOUNDS);
    await twins.connect(gov).registerMachine(other, operator.address, BOUNDS);
    await twins.connect(gov).whitelistAgent(agent.address, 42);
    await twins.connect(gov).setProvider(provider.address, true);
    await vwc.transfer(agent.address, ethers.parseEther("10000"));
    await vwc.connect(agent).approve(await twins.getAddress(), ethers.MaxUint256);
    return { gov, operator, agent, provider, stranger, vwc, twins, machine, other };
  }

  // proof as the prover would make it for (machine, operator, prior root, bounds)
  function proofFor({ machine, operator, prevRoot = 0n, newRoot, health, cycle, inputHash = 77n, bounds = BOUNDS }) {
    const pub = twinSignals({ newRoot, inputHash, boundsHash: bounds, prevRoot, health, cycle,
                              machineId: machine, submitter: operator.address });
    return { ...mockProof(pub), inputHash };
  }

  async function update(ctx, { health, cycle, root, prevRoot = 0n }) {
    const newRoot = root ?? ethers.toBeHex(1000n + BigInt(cycle), 32);
    const p = proofFor({ machine: ctx.machine, operator: ctx.operator, prevRoot, newRoot, health, cycle });
    await ctx.twins.connect(ctx.operator).updateTwinState(ctx.machine, newRoot, ethers.toBeHex(p.inputHash, 32),
                                                          health, cycle, p.a, p.b, p.c);
    return newRoot;
  }

  it("accepts a proof bound to machine, operator, bounds and prior state; flags health < 40", async () => {
    const { operator, twins, machine } = await setup();
    const newRoot = ethers.toBeHex(4242n, 32);
    const p = proofFor({ machine, operator, newRoot, health: 35, cycle: 10 });
    await expect(twins.connect(operator).updateTwinState(machine, newRoot, ethers.toBeHex(p.inputHash, 32), 35, 10, p.a, p.b, p.c))
      .to.emit(twins, "TwinUpdated").withArgs(machine, newRoot, 35)
      .and.to.emit(twins, "MaintenanceTriggered").withArgs(machine, operator.address);
    const t = await twins.twins(machine);
    expect(t.maintenanceFlag).to.equal(true);
    expect(await twins.CRITICAL_HEALTH()).to.equal(40);
    expect(await twins.goodProofs(operator.address)).to.equal(1);
    const [valid, invalid] = await twins.outcomes(operator.address);
    expect(valid).to.equal(1); expect(invalid).to.equal(0);
  });

  it("does not flag a machine at or above the threshold", async () => {
    const ctx = await setup();
    await update(ctx, { health: 40, cycle: 1 });
    expect((await ctx.twins.twins(ctx.machine)).maintenanceFlag).to.equal(false);
  });

  it("records (does not revert) a proof replayed on another machine", async () => {
    const { operator, twins, machine, other } = await setup();
    const newRoot = ethers.toBeHex(4242n, 32);
    const p = proofFor({ machine, operator, newRoot, health: 90, cycle: 10 });   // made for `machine`
    await expect(twins.connect(operator).updateTwinState(other, newRoot, ethers.toBeHex(p.inputHash, 32), 90, 10, p.a, p.b, p.c))
      .to.emit(twins, "InvalidProof").withArgs(other, operator.address);
    expect(await twins.badProofs(operator.address)).to.equal(1);
    expect((await twins.twins(other)).cycleCount).to.equal(0);
  });

  it("rejects a proof whose health score was edited after proving", async () => {
    const { operator, twins, machine } = await setup();
    const newRoot = ethers.toBeHex(4242n, 32);
    const p = proofFor({ machine, operator, newRoot, health: 20, cycle: 10 });
    await expect(twins.connect(operator).updateTwinState(machine, newRoot, ethers.toBeHex(p.inputHash, 32), 95, 10, p.a, p.b, p.c))
      .to.emit(twins, "InvalidProof");
  });

  it("binds the registered bounds: a proof under other bounds fails", async () => {
    const { operator, twins, machine } = await setup();
    const newRoot = ethers.toBeHex(4242n, 32);
    const p = proofFor({ machine, operator, newRoot, health: 90, cycle: 10, bounds: 999n });
    await expect(twins.connect(operator).updateTwinState(machine, newRoot, ethers.toBeHex(p.inputHash, 32), 90, 10, p.a, p.b, p.c))
      .to.emit(twins, "InvalidProof");
  });

  it("chains updates on the previous root; old proofs and decreasing cycle counts fail", async () => {
    const ctx = await setup();
    const r1 = await update(ctx, { health: 90, cycle: 10 });
    const r2 = await update(ctx, { health: 88, cycle: 20, prevRoot: BigInt(r1) });
    await update(ctx, { health: 87, cycle: 20, prevRoot: BigInt(r2) });      // idle window: same cycle count
    expect((await ctx.twins.twins(ctx.machine)).cycleCount).to.equal(20);
    // replaying the first proof (built on the empty state) is recorded as invalid
    const old = proofFor({ machine: ctx.machine, operator: ctx.operator, newRoot: r1, health: 90, cycle: 20 });
    await expect(ctx.twins.connect(ctx.operator).updateTwinState(ctx.machine, r1,
      ethers.toBeHex(old.inputHash, 32), 90, 20, old.a, old.b, old.c)).to.emit(ctx.twins, "InvalidProof");
    const p = proofFor({ machine: ctx.machine, operator: ctx.operator, newRoot: 5n, health: 88, cycle: 19 });
    await expect(ctx.twins.connect(ctx.operator).updateTwinState(ctx.machine, ethers.toBeHex(5n, 32),
      ethers.toBeHex(p.inputHash, 32), 88, 19, p.a, p.b, p.c)).to.be.revertedWith("Stale update");
  });

  it("only the registered operator may submit", async () => {
    const { stranger, twins, machine } = await setup();
    const { a, b, c } = badProof();
    await expect(twins.connect(stranger).updateTwinState(machine, ethers.ZeroHash, ethers.ZeroHash, 80, 1, a, b, c))
      .to.be.revertedWith("Not operator");
  });

  it("registration requires field elements and governance", async () => {
    const { gov, operator, stranger, twins } = await setup();
    await expect(twins.connect(gov).registerMachine(ethers.toBeHex(R, 32), operator.address, 1n))
      .to.be.revertedWith("Not a field element");
    await expect(twins.connect(stranger).registerMachine(fieldId("x"), operator.address, 1n))
      .to.be.revertedWith("Not gov");
    await expect(twins.connect(gov).registerMachine(fieldId("cnc-01"), operator.address, 1n))
      .to.be.revertedWith("Registered");
  });

  describe("maintenance escrow and agent rails", () => {
    it("escrows for an approved provider and pays on attested recovery", async () => {
      const ctx = await setup();
      const { agent, provider, vwc, twins, machine } = ctx;
      const r1 = await update(ctx, { health: 20, cycle: 1 });
      await expect(twins.connect(agent).scheduleMaintenance(machine, provider.address, ethers.parseEther("120")))
        .to.emit(twins, "MaintenanceScheduled");
      await expect(twins.connect(agent).scheduleMaintenance(machine, provider.address, ethers.parseEther("10")))
        .to.be.revertedWith("order open");
      await update(ctx, { health: 95, cycle: 2, prevRoot: BigInt(r1) });
      const before = await vwc.balanceOf(provider.address);
      await twins.completeMaintenance(machine);
      expect((await vwc.balanceOf(provider.address)) - before).to.equal(ethers.parseEther("120"));
    });

    it("refuses unapproved providers and unflagged machines", async () => {
      const ctx = await setup();
      const { agent, stranger, twins, machine } = ctx;
      await expect(twins.connect(agent).scheduleMaintenance(machine, stranger.address, 1n))
        .to.be.revertedWith("no maintenance flag");
      await update(ctx, { health: 10, cycle: 1 });
      await expect(twins.connect(agent).scheduleMaintenance(machine, stranger.address, 1n))
        .to.be.revertedWith("provider not approved");
      await expect(twins.connect(stranger).scheduleMaintenance(machine, stranger.address, 1n))
        .to.be.revertedWith("agent not whitelisted");
    });

    it("holds orders above the threshold for human review (approve or refund)", async () => {
      const ctx = await setup();
      const { gov, agent, provider, vwc, twins, machine } = ctx;
      await update(ctx, { health: 10, cycle: 1 });
      const big = ethers.parseEther("600");                      // > 500 threshold
      await expect(twins.connect(agent).scheduleMaintenance(machine, provider.address, big))
        .to.emit(twins, "MaintenanceReviewRequested");
      await expect(twins.completeMaintenance(machine)).to.be.revertedWith("no scheduled order");
      const before = await vwc.balanceOf(agent.address);
      await expect(twins.connect(gov).reviewOrder(machine, false)).to.emit(twins, "MaintenanceRejected");
      expect((await vwc.balanceOf(agent.address)) - before).to.equal(big);
      await twins.connect(agent).scheduleMaintenance(machine, provider.address, big);
      await expect(twins.connect(gov).reviewOrder(machine, true)).to.emit(twins, "MaintenanceScheduled");
    });

    it("governance can suspend an agent (DAO veto)", async () => {
      const ctx = await setup();
      const { gov, agent, provider, twins, machine } = ctx;
      await update(ctx, { health: 10, cycle: 1 });
      await expect(twins.connect(gov).suspendAgent(agent.address)).to.emit(twins, "AgentSuspended");
      await expect(twins.connect(agent).scheduleMaintenance(machine, provider.address, 1n))
        .to.be.revertedWith("agent not whitelisted");
    });

    it("enforces the per-agent daily spending cap", async () => {
      const ctx = await setup();
      const { gov, agent, provider, twins, machine, other, operator } = ctx;
      await twins.connect(gov).setAgentLimits(ethers.parseEther("500"), ethers.parseEther("150"));
      await update(ctx, { health: 10, cycle: 1 });
      const p = proofFor({ machine: other, operator, newRoot: ethers.toBeHex(9n, 32), health: 10, cycle: 1 });
      await twins.connect(operator).updateTwinState(other, ethers.toBeHex(9n, 32), ethers.toBeHex(p.inputHash, 32), 10, 1, p.a, p.b, p.c);
      await twins.connect(agent).scheduleMaintenance(machine, provider.address, ethers.parseEther("100"));
      await expect(twins.connect(agent).scheduleMaintenance(other, provider.address, ethers.parseEther("100")))
        .to.be.revertedWith("daily cap");
      await ethers.provider.send("evm_increaseTime", [86400]);
      await ethers.provider.send("evm_mine", []);
      await expect(twins.connect(agent).scheduleMaintenance(other, provider.address, ethers.parseEther("100")))
        .to.emit(twins, "MaintenanceScheduled");
    });
  });
});
