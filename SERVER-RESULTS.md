# Server output files

The runner opens a consistent checkpoint read-only, verifies its manifest digest,
and runs state extraction followed by receipt verification. It writes results to
its deployment directory. A failed phase stops the sequence and returns a nonzero exit code.
Each deployment directory supports one run; `run.lock` prevents overlapping or
repeated jobs from sharing output paths. Use a fresh directory for another run.

| File | Meaning |
| --- | --- |
| `run.json` | Phase status, process IDs, commands, and checkpoint digest |
| `runner.log` | Runner errors and diagnostic output |
| `state.log` | State extraction progress or errors |
| `receipts.log` | Receipt verification progress or errors |
| `state/summary.json` | Completed state totals, hashes, counts, and coverage |
| `state/accounts.csv` | Every state leaf, including zero balances |
| `state/liquid.csv` | Positive liquid balances |
| `state/staking.csv` | Active delegation, pending undelegation, and reward totals |
| `state/staking-components.csv` | Self-stake and other delegation kept separate |
| `state/delegations.csv` | Positive principal by validator and delegator |
| `state/wone-holders.csv` | WONE balances resolved to holder addresses |
| `state/wone-storage.csv` | Authenticated storage keys and encoded values |
| `state/unresolved-accounts.csv` | Positive liquid balances without resolved addresses |
| `receipts.json` | Completed verified receipt evidence |

Staking and WONE files apply to shard 0. `.partial` files are unfinished.
A completed state summary does not imply successful receipt verification or
complete migration claims. Inspect phase status and coverage before comparison.

Liquid, staking, delegation, and WONE-holder CSVs use the corresponding
harmony-migration component headers. Final gross claims use the separate format
specified in [output compatibility](docs/output-compatibility.md).

Candidate CSVs provide address hints only. The extractor records their hashes
and reads every WONE amount from the pinned storage trie. Final combined claims
require both shards, verified transfers, resolved coverage, and reviewed policy.

Run metadata contains local database paths and process details. Keep deployment
directories private; share only the artifacts appropriate for the comparison.
