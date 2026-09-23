#!/usr/bin/env node
// Compute the boundsHash a machine is registered with:
//   boundsHash = Poseidon(lo[0..S-1], hi[0..S-1])      (same as C_twin)
// Usage: node circuits/bounds_hash.js "0,0,0,0,0,0,0,0" "6000,6000,6000,95000,20000,110000,250000,60000"
const { buildPoseidon } = require("circomlibjs");

async function boundsHash(lo, hi) {
  if (lo.length !== hi.length || 2 * lo.length > 16) throw new Error("need 1..8 sensors with lo/hi each");
  const poseidon = await buildPoseidon();
  return poseidon.F.toObject(poseidon([...lo, ...hi].map(BigInt)));
}

module.exports = { boundsHash };

if (require.main === module) {
  const [loArg, hiArg] = process.argv.slice(2);
  if (!loArg || !hiArg) { console.error("usage: bounds_hash.js <lo,csv> <hi,csv>"); process.exit(1); }
  boundsHash(loArg.split(","), hiArg.split(",")).then((h) => console.log(h.toString()));
}
