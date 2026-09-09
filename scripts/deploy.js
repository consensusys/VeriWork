// Deploys the VeriWork L1 anchor + L2 contracts to the selected network.
// For a real deployment replace MockGroth16Verifier with the verifiers exported
// by circuits/build.sh (contracts/generated/*Verifier.sol).
const hre = require("hardhat");

async function main() {
  const [deployer, gov] = await hre.ethers.getSigners();
  const governance = process.env.GOVERNANCE || gov.address;
  console.log("deployer:", deployer.address, "governance:", governance);

  const supply = hre.ethers.parseEther("1000000000");           // 1e9 VWC
  const vwc = await hre.ethers.deployContract("VWCToken", [supply, deployer.address]);
  const verifier = await hre.ethers.deployContract("MockGroth16Verifier", [false]);
  const staking = await hre.ethers.deployContract("PoAWStaking", [await vwc.getAddress(), governance]);
  const election = await hre.ethers.deployContract("SequencerElection", [await staking.getAddress(), governance]);
  const genesis = hre.ethers.keccak256(hre.ethers.toUtf8Bytes("genesis"));
  const rollup = await hre.ethers.deployContract("VeriWorkRollup",
    [await verifier.getAddress(), await staking.getAddress(), await election.getAddress(), genesis]);
  const ordering = await hre.ethers.deployContract("CommitRevealOrdering", [1]);
  const market = await hre.ethers.deployContract("CreditMarket", [await vwc.getAddress(), governance]);
  const tasks = await hre.ethers.deployContract("TaskRegistry", [await verifier.getAddress(), await vwc.getAddress()]);
  const twins = await hre.ethers.deployContract("FlexFactoryTwinRegistry",
    [await verifier.getAddress(), await vwc.getAddress(), governance]);

  const govSigner = await hre.ethers.getSigner(governance);
  await (await staking.connect(govSigner).setRollup(await rollup.getAddress())).wait();
  await (await election.connect(govSigner).setRollup(await rollup.getAddress())).wait();

  const out = {
    VWCToken: await vwc.getAddress(), Groth16Verifier: await verifier.getAddress(),
    PoAWStaking: await staking.getAddress(), SequencerElection: await election.getAddress(),
    VeriWorkRollup: await rollup.getAddress(), CommitRevealOrdering: await ordering.getAddress(),
    CreditMarket: await market.getAddress(), TaskRegistry: await tasks.getAddress(),
    FlexFactoryTwinRegistry: await twins.getAddress(),
  };
  console.log(JSON.stringify(out, null, 2));
  require("fs").writeFileSync(`deployments.${hre.network.name}.json`, JSON.stringify(out, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
