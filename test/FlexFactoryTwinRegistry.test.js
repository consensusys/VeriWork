const { expect } = require("chai");
const { ethers } = require("hardhat");
const { R, mockProof, badProof } = require("./helpers");

describe("FlexFactoryTwinRegistry", () => {
  async function setup() {
    const [deployer, gov, worker, operator, agent, provider] = await ethers.getSigners();
    const vwc = await ethers.deployContract("VWCToken", [ethers.parseEther("1000000"), deployer.address]);
    const verifier = await ethers.deployContract("MockGroth16Verifier", [false]);
    const twins = await ethers.deployContract("FlexFactoryTwinRegistry",
      [await verifier.getAddress(), await vwc.getAddress(), gov.address]);
    const machine = ethers.keccak256(ethers.toUtf8Bytes("cnc-01"));
    await twins.connect(gov).registerMachine(machine, operator.address, worker.address);
    await twins.connect(gov).whitelistAgent(agent.address, 42);
    await vwc.transfer(agent.address, ethers.parseEther("1000"));
    await vwc.connect(agent).approve(await twins.getAddress(), ethers.parseEther("1000"));
    return { gov, worker, operator, agent, provider, vwc, twins, machine };
  }

  function pubFor(prevRoot, newRoot, health) {
    return [BigInt(prevRoot) % R, BigInt(newRoot) % R, BigInt(health)];
  }

  it("accepts a valid ZK telemetry proof and flags maintenance below 30", async () => {
    const { worker, operator, twins, machine } = await setup();
    const newRoot = ethers.keccak256(ethers.toUtf8Bytes("state-1"));
    const { a, b, c } = mockProof(pubFor(ethers.ZeroHash, newRoot, 25));
    await expect(twins.connect(worker).updateTwinState(machine, newRoot, 25, 1000, a, b, c))
      .to.emit(twins, "TwinUpdated").withArgs(machine, newRoot, 25)
      .and.to.emit(twins, "MaintenanceTriggered").withArgs(machine, operator.address);
    const t = await twins.twins(machine);
    expect(t.maintenanceFlag).to.equal(true);
    expect(t.healthScore).to.equal(25);
  });

  it("rejects an invalid proof", async () => {
    const { worker, twins, machine } = await setup();
    const { a, b, c } = badProof();
    await expect(twins.connect(worker).updateTwinState(machine, ethers.ZeroHash, 80, 1, a, b, c))
      .to.be.revertedWith("Invalid ZK telemetry proof");
  });

  it("rejects updates from unauthorised workers", async () => {
    const { agent, twins, machine } = await setup();
    const { a, b, c } = mockProof(pubFor(ethers.ZeroHash, ethers.ZeroHash, 80));
    await expect(twins.connect(agent).updateTwinState(machine, ethers.ZeroHash, 80, 1, a, b, c))
      .to.be.revertedWith("not authorised worker");
  });

  it("lets a whitelisted agent escrow maintenance and pays the provider on attested recovery", async () => {
    const { worker, agent, provider, vwc, twins, machine } = await setup();
    let root = ethers.keccak256(ethers.toUtf8Bytes("state-1"));
    let p = mockProof(pubFor(ethers.ZeroHash, root, 20));
    await twins.connect(worker).updateTwinState(machine, root, 20, 1000, p.a, p.b, p.c);

    await expect(twins.connect(agent).scheduleMaintenance(machine, provider.address, ethers.parseEther("120")))
      .to.emit(twins, "MaintenanceScheduled");
    // orders above the human-review threshold are refused
    await expect(twins.connect(agent).scheduleMaintenance(machine, provider.address, ethers.parseEther("600")))
      .to.be.revertedWith("order open");

    const prev = root; root = ethers.keccak256(ethers.toUtf8Bytes("state-2"));
    p = mockProof(pubFor(prev, root, 95));
    await twins.connect(worker).updateTwinState(machine, root, 95, 1200, p.a, p.b, p.c);
    const before = await vwc.balanceOf(provider.address);
    await twins.completeMaintenance(machine);
    expect((await vwc.balanceOf(provider.address)) - before).to.equal(ethers.parseEther("120"));
  });
});
