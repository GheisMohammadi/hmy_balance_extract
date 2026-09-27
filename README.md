# hmy_balance_extract

Extract Harmony balances from raw databases at a fixed cutoff, apply reviewed
migration rules, and compare the results with a reference CSV.

The Go extractor implements RLP decoding, state-trie traversal, and receipt
verification. It opens consistent LevelDB checkpoints read-only. The Python
calculator uses exact integer amounts. Neither tool imports the migration
pipeline's calculation code.

## Build

Requirements: Go 1.23+ and Python 3.9+.

From the repository root:

```sh
make build
make test
make build-linux  # Optional Linux amd64 binary.
```

`make check` also runs Go vet and the Git whitespace check.

## Choose an extraction mode

| Mode | Reads | Output |
| --- | --- | --- |
| Probe | Selected liquid/WONE balances; optional validator list | Incomplete JSON for spot checks |
| Selected state | Selected balances and all shard-0 validator wrappers | State JSON for the selected accounts |
| Full state | Every state leaf; shard-0 staking and WONE storage | Per-shard component CSVs and a summary |
| Receipts | Canonical history from genesis to cutoff | Verified outgoing and incoming receipts |

The bundled manifest pins shard 0 block **93623067** and shard 1 block
**95882100** at the September 10, 2026 cutoff. The extractor verifies their
canonical hashes and state roots. Evidence records the exact manifest hash.
When recomputing existing evidence, pass the manifest stored with that evidence
using `--manifest`. Even a metadata-only edit changes the file hash.

Use a consistent offline database or checkpoint. Do not copy a live LevelDB
file-by-file or bypass its locks. Store private inputs and outputs in the ignored
`artifacts/` or `data/` directories.

## Check a few accounts

Put one `0x` address per line in `artifacts/accounts.txt`:

```sh
bin/independent-db \
  --db /path/to/shard0-checkpoint --shard 0 \
  --manifest manifests/snapshot-2026-09-10.json \
  --accounts artifacts/accounts.txt --max-accounts 5 \
  --mode probe --timeout 2m \
  --output artifacts/shard0-probe.json
```

Probe reports have `complete: false` and cannot produce final claims.

## Extract every account

```sh
bin/independent-db \
  --db /path/to/shard0-checkpoint --shard 0 \
  --manifest manifests/snapshot-2026-09-10.json \
  --all-accounts --mode state --allow-global-scan --timeout 24h \
  --output artifacts/shard0-state
```

Repeat with the shard-1 checkpoint, `--shard 1`, and a new output directory.
The timeout is an operator limit, not an estimated duration.

For shard 0, optional `--candidate-address-csv /path/to/addresses.csv` inputs
help resolve WONE holders. Only their address columns are used; amounts come
from the authenticated storage trie. Candidate file hashes and coverage checks
are recorded. Missing address preimages remain identified by secure key.

Full-state CSVs contain raw components. **They are not final combined claims
and are not accepted directly by the selected-account JSON assembler.**
See [server outputs](SERVER-RESULTS.md) for file meanings and completion checks.

## Calculate and compare selected-account claims

Produce complete state and receipt JSON reports for the same account selection
on both shards, then supply reviewed policy:

```sh
python3 scripts/independent-recompute.py \
  --raw-state0 artifacts/raw-state0.json \
  --raw-state1 artifacts/raw-state1.json \
  --raw-receipts0 artifacts/raw-receipts0.json \
  --raw-receipts1 artifacts/raw-receipts1.json \
  --policy artifacts/reviewed-policy.json \
  --compare artifacts/reference-claims.csv \
  --output artifacts/comparison.json \
  --claims-output artifacts/migration-claims.csv \
  --per-address artifacts/net-audit.csv
```

`--claims-output` uses the 44-column harmony-migration gross claims format.
Use `--claims-format native` for its 34-column native format. `--per-address`
contains separate net allocations. The reference CSV is used only for comparison.

Exchange classifications, exclusions, deductions, and delivery stages require
reviewed policy inputs. Missing evidence and receipt-root mismatches stop the
calculation; they never become zero balances. Full-network final claims require
complete identity coverage, both shards, verified transfers, and reviewed policy.

## Documentation

- [Calculation and extraction guide](docs/independent-recompute.md)
- [Output compatibility](docs/output-compatibility.md)
- [Server output files](SERVER-RESULTS.md)
- [Agent instructions](AGENTS.md)
