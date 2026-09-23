const { ethers } = require("hardhat");

const R = 21888242871839275222246405745257275088548364400416034343698204186575808495617n;

/** Proof accepted by MockGroth16Verifier: a[0] = keccak(abi.encode(uint256[n] pub)) % R. */
function mockProof(pub) {
  const enc = ethers.AbiCoder.defaultAbiCoder().encode([`uint256[${pub.length}]`], [pub]);
  const tag = BigInt(ethers.keccak256(enc)) % R;
  return { a: [tag, 1n], b: [[1n, 2n], [3n, 4n]], c: [tag, 2n] };
}

function badProof() { return { a: [7n, 1n], b: [[1n, 2n], [3n, 4n]], c: [7n, 2n] }; }

/** A bytes32 id that is a BN254 field element (what registries require). */
function fieldId(label) {
  return ethers.toBeHex(BigInt(ethers.keccak256(ethers.toUtf8Bytes(label))) % R, 32);
}

/**
 * C_twin public signals in circuit order:
 * [twinHash, inputHash, boundsHash, prevStateRoot, healthScore, cycleCount, machineId, submitter]
 */
function twinSignals({ newRoot, inputHash, boundsHash, prevRoot, health, cycle, machineId, submitter }) {
  return [BigInt(newRoot), BigInt(inputHash), BigInt(boundsHash), BigInt(prevRoot),
          BigInt(health), BigInt(cycle), BigInt(machineId), BigInt(submitter)];
}

async function deployCore() {
  const [deployer, gov, w1, w2, w3, alice] = await ethers.getSigners();
  const vwc = await ethers.deployContract("VWCToken", [ethers.parseEther("1000000"), deployer.address]);
  const verifier = await ethers.deployContract("MockGroth16Verifier", [false]);
  const staking = await ethers.deployContract("PoAWStaking", [await vwc.getAddress(), gov.address]);
  const election = await ethers.deployContract("SequencerElection", [await staking.getAddress(), gov.address]);
  const genesis = ethers.keccak256(ethers.toUtf8Bytes("genesis"));
  const rollup = await ethers.deployContract("VeriWorkRollup",
    [await verifier.getAddress(), await staking.getAddress(), await election.getAddress(), genesis, gov.address]);
  const tasks = await ethers.deployContract("TaskRegistry", [await verifier.getAddress(), await vwc.getAddress()]);
  const twins = await ethers.deployContract("FlexFactoryTwinRegistry",
    [await verifier.getAddress(), await vwc.getAddress(), gov.address]);
  await staking.connect(gov).setRollup(await rollup.getAddress());
  await election.connect(gov).setRollup(await rollup.getAddress());
  await rollup.connect(gov).addOutcomeSource(await twins.getAddress());
  await rollup.connect(gov).addOutcomeSource(await tasks.getAddress());
  for (const w of [w1, w2, w3, alice]) await vwc.transfer(w.address, ethers.parseEther("20000"));
  return { deployer, gov, w1, w2, w3, alice, vwc, verifier, staking, election, rollup, tasks, twins, genesis };
}

module.exports = { R, mockProof, badProof, fieldId, twinSignals, deployCore };
