require("@nomicfoundation/hardhat-toolbox");
const { subtask } = require("hardhat/config");
const { TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD } = require("hardhat/builtin-tasks/task-names");

// Offline CI support: if LOCAL_SOLCJS points at an npm `solc` package of the
// matching version, use it instead of downloading a native compiler.
if (process.env.LOCAL_SOLCJS) {
  subtask(TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD, async (args, hre, runSuper) => {
    if (args.solcVersion === "0.8.24") {
      return { compilerPath: require.resolve(process.env.LOCAL_SOLCJS + "/soljson.js"),
               isSolcJs: true, version: args.solcVersion, longVersion: "0.8.24-local" };
    }
    return runSuper();
  });
}

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.24",
    settings: { optimizer: { enabled: true, runs: 200 }, evmVersion: "cancun" },
  },
  networks: {
    hardhat: { hardfork: "cancun" },
    sepolia: {
      url: process.env.SEPOLIA_RPC_URL || "",
      accounts: process.env.DEPLOYER_KEY ? [process.env.DEPLOYER_KEY] : [],
    },
  },
  paths: { sources: "./contracts", tests: "./test" },
};
