// Deploys the VeriWork L1 anchor + L2 contracts to the selected network.
//
// Verifiers: by default this uses MockGroth16Verifier (NOT sound).  For a real
// deployment run circuits/build.sh and set
//   TWIN_VERIFIER=TelemetryAttestVerifier    (contract exported for C_twin)
// The aggregated-batch verifier has no circuit in this release (see README),
// so the rollup keeps the mock unless AGG_VERIFIER names a real contract.
const hre = require("hardhat");

async function main() {
  const [deployer, gov] = await hre.ethers.getSigners();
  const governance = process.env.GOVERNANCE || gov.address;
  console.log("deployer:", deployer.address, "governance:", governance);

  const supply = hre.ethers.parseEther("1000000000");           // 1e9 VWC
  const vwc = await hre.ethers.deployContract("VWCToken", [supply, deployer.address]);
  const mock = await hre.ethers.deployContract("MockGroth16Verifier", [false]);
  const twinVerifier = process.env.TWIN_VERIFIER
    ? await hre.ethers.deployContract(process.env.TWIN_VERIFIER) : mock;
  const aggVerifier = process.env.AGG_VERIFIER
    ? await hre.ethers.deployContract(process.env.AGG_VERIFIER) : mock;
  if (!process.env.TWIN_VERIFIER) console.warn("WARNING: C_twin proofs verified by MockGroth16Verifier");

  const staking = await hre.ethers.deployContract("PoAWStaking", [await vwc.getAddress(), governance]);
  const election = await hre.ethers.deployContract("SequencerElection", [await staking.getAddress(), governance]);
  const genesis = hre.ethers.keccak256(hre.ethers.toUtf8Bytes("genesis"));
  const rollup = await hre.ethers.deployContract("VeriWorkRollup",
    [await aggVerifier.getAddress(), await staking.getAddress(), await election.getAddress(), genesis, governance]);
  const ordering = await hre.ethers.deployContract("CommitRevealOrdering", [1]);
  const market = await hre.ethers.deployContract("CreditMarket", [await vwc.getAddress(), governance]);
  const tasks = await hre.ethers.deployContract("TaskRegistry", [await twinVerifier.getAddress(), await vwc.getAddress()]);
  const twins = await hre.ethers.deployContract("FlexFactoryTwinRegistry",
    [await twinVerifier.getAddress(), await vwc.getAddress(), governance]);

  const govSigner = await hre.ethers.getSigner(governance);
  await (await staking.connect(govSigner).setRollup(await rollup.getAddress())).wait();
  await (await election.connect(govSigner).setRollup(await rollup.getAddress())).wait();
  // epsilon_i is read from the contracts that verify proofs (objective slashing)
  await (await rollup.connect(govSigner).addOutcomeSource(await twins.getAddress())).wait();
  await (await rollup.connect(govSigner).addOutcomeSource(await tasks.getAddress())).wait();

  const out = {
    VWCToken: await vwc.getAddress(),
    TwinVerifier: await twinVerifier.getAddress(), AggregatedVerifier: await aggVerifier.getAddress(),
    PoAWStaking: await staking.getAddress(), SequencerElection: await election.getAddress(),
    VeriWorkRollup: await rollup.getAddress(), CommitRevealOrdering: await ordering.getAddress(),
    CreditMarket: await market.getAddress(), TaskRegistry: await tasks.getAddress(),
    FlexFactoryTwinRegistry: await twins.getAddress(),
  };
  console.log(JSON.stringify(out, null, 2));
  require("fs").writeFileSync(`deployments.${hre.network.name}.json`, JSON.stringify(out, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
