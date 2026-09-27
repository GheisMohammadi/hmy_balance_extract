# Extraction and calculation

The extractor reads chain components from consistent LevelDB checkpoints.
The calculator combines those components with reviewed policy and compares
results with a separate reference CSV. All amounts use integer atto-ONE.

## Accounting

```text
native_wallet = liquid_shard0 + liquid_shard1 + pending_undelegation
                + unclaimed_reward + pending_cross_shard
staked        = self_stake + delegated
native_total  = native_wallet + staked
qualification = native_total + eligible_wone
qualifies     = qualification >= 1000 ONE
wone_delivery = eligible_wone if qualifies or exchange else 0
wallet_claim  = native_wallet + wone_delivery
gross_claim   = wallet_claim + staked
```

For qualifying non-exchange accounts, deductions reduce the wallet first:

```text
deduction    = incident + burn_or_inaccessible
               + reviewed_contract_non_issuance + wone_reserve
final_wallet = max(wallet_claim - deduction, 0)
final_vault  = staked - max(deduction - wallet_claim, 0)
net_claim    = final_wallet + final_vault
```

Raw mode rejects deductions above the measured claim. Below-threshold ordinary
accounts receive no allocation. Confirmed exchange entitlements follow manual
delivery policy. Stages and deduction amounts are reviewed inputs, not facts
inferred from balances. The WONE contract's self-held WONE is excluded.

## Inputs and trust boundaries

| Input | Purpose |
| --- | --- |
| Manifest | Pins cutoff blocks, hashes, roots, and accounting context |
| Raw state | Supplies liquid balances, stake, undelegations, rewards, and WONE |
| Raw receipts | Establishes pending transfers at both cutoffs |
| Reviewed policy | Supplies exchange status, exclusions, deductions, and stages |
| Reference CSV | Comparison only; never supplies missing chain components |

`--snapshot` accepts component CSVs for arithmetic checks. It trusts their
components and does not independently extract balances. Raw mode requires
complete JSON evidence with matching selections and manifest hashes.

## Selected-account extraction

Build with `make build`. Put one `0x` address per line in
`artifacts/accounts.txt`. The default cap is 20 accounts; set `--max-accounts`
explicitly for larger selections.

```sh
bin/independent-db \
  --db /path/to/shard0-checkpoint --shard 0 \
  --manifest manifests/snapshot-2026-09-10.json \
  --accounts artifacts/accounts.txt \
  --mode state --allow-global-scan --timeout 2h \
  --output artifacts/raw-state0.json

bin/independent-db \
  --db /path/to/shard0-checkpoint --shard 0 \
  --manifest manifests/snapshot-2026-09-10.json \
  --accounts artifacts/accounts.txt \
  --mode receipts --allow-global-scan --timeout 12h \
  --output artifacts/raw-receipts0.json
```

Repeat for shard 1 with its checkpoint, `--shard 1`, and separate output names.
Use exactly the same account selection for all four reports. Timeouts are limits,
not estimates. Staking scans the entire shard-0 state even for a small selection;
receipt verification scans canonical history from genesis to cutoff.

Use `--mode probe` for bounded liquid/WONE lookups. Optional
`--probe-validators artifacts/validators.txt` adds a bounded validator sample.
Probe reports remain incomplete and are rejected by the final calculator.

The reader opens flat LevelDB layouts and bucketed checkpoints with tables under
`tables/<file-number modulo 256 in hex>/`. Both storage and database access are
read-only. Locks are respected. Opening a large database can precede context
cancellation checks; use an OS process timeout when a strict wall-clock limit
is required. Output paths must not exist.

## How components are extracted

- Liquid: decode balances from the state trie at the manifest's root. Verify
  node hashes and preserve account identity, nonce, and code hash metadata.
- Staking: discover validator wrappers in shard-0 state. Verify code hashes and
  validator identities; sum self-stake, other delegation, undelegations, and rewards.
- WONE: read the balance mapping at slot 3 from authenticated contract storage.
  Verify the pinned runtime code hash before interpreting the layout. Apply
  reviewed exclusions only during policy assembly.
- Transfers: verify a continuous canonical header chain, outgoing receipt roots,
  and incoming body roots. Match recipient, amount, source block, and source hash.
  Count transfers to active shards only when absent from the destination cutoff.
  Report unprovable retired-shard receipts separately and exclude them from claims.

Missing nodes and receipt-root mismatches are errors. Historical body defects
require independently authenticated repair evidence. Lookup indexes and spent
flags cannot replace canonical verification.

## Full-state exports

Use `--all-accounts` instead of `--accounts` to enumerate the full state or
include every receipt recipient. Full-state mode writes a new directory of CSVs;
full-receipts mode writes JSON. See [the README](../README.md) for a full-state
command and [server outputs](../SERVER-RESULTS.md) for file definitions.

Optional candidate CSVs must have an `address` or `eth_address` column. Their
amounts are ignored. Candidate addresses map secure state/storage keys to
identities; balances are decoded directly from authenticated trie leaves.
Unknown identities remain secure keys. The summary records unresolved accounts,
candidate file hashes, and whether resolved WONE balances equal pinned backing.
A WONE coverage gap prevents a complete holder claim.

Full-state CSVs are component evidence, not combined migration claims. The
selected-account JSON assembler does not ingest these directories. Full-network
claims require an assembly workflow that checks population coverage, both shards,
verified pending transfers, and reviewed policy.

### Reviewed policy contract

Policy is intentionally separate from raw chain extraction. Exchange identity,
reviewed deductions, WONE exclusions and stage classifications cannot all be
inferred from balances. Supply a JSON object with this shape, one explicit entry
for **every** selected address:

```json
{
  "schema": "harmony-independent-policy/v1",
  "rules": "independent-recompute/v1",
  "manifest_sha256": "SHA256_OF_THE_EXACT_MANIFEST_FILE",
  "review_reference": "reviewed inventory versions and deduction mapping approval",
  "accounts": {
    "0xADDRESS_WITH_40_HEX_DIGITS": {
      "exchange": "",
      "migration_stage": "initial",
      "issuance_treatment": "issue",
      "wone_excluded": false,
      "incident_deduction_atto": "0",
      "burn_or_inaccessible_atto": "0",
      "reviewed_contract_non_issuance_atto": "0",
      "wone_not_delivered_atto": "0",
      "wone_reserve_atto": "0",
      "non_issuing_atto": "0"
    }
  }
}
```

This is a schema illustration, **not a default policy**. Do not fill unknown
amounts with zeros. The review reference must resolve the deduction mapping
between inventory fields and deduction fields. Accounting uses the
inclusive 1000 ONE test before deductions, exchange manual delivery,
and wallet-first deduction. Raw mode rejects deductions above the measured
claim and contradictory issue/not-issued classifications. Activity-based stage
assignment remains a reviewed input; it is not independently reconstructed.


## Calculate and compare

Use the command in [the README](../README.md#calculate-and-compare-selected-account-claims)
with complete state/receipt JSON and policy. State evidence must include identity,
nonce, and code metadata; missing metadata does not prove account absence.

`--claims-output` writes gross claims in the reference format. `--per-address`
writes split components and net allocations. `--compare` accepts native claims
(34 columns), WONE claims (44 columns), or combined snapshots containing
`eth_address` and the eight component columns.

Comparison uses exact integers and formatted values. Missing or duplicate selected
reference rows fail comparison. Extra reference accounts outside the selection
are ignored. Optional derived snapshot columns are compared when present and
reported as not compared when absent. The JSON report records input hashes,
cutoff, compared fields, mismatch counts, and bounded examples.

CSV inputs require unique headers and consistent row widths. Duplicate ledger
addresses are rejected. Output files are published after successful writes; a
failed replacement preserves the existing file. Each artifact is published
separately, so use the final totals report to identify a completed calculation.

Exit codes: `0` success, `1` comparison differences, `2` invalid or incomplete
inputs. Selected-account totals describe only the selection.

For a CSV arithmetic check:

```sh
python3 scripts/independent-recompute.py \
  --snapshot artifacts/components.csv \
  --output artifacts/totals.json \
  --per-address artifacts/net-audit.csv --check
```

`--check` compares derived snapshot columns with recalculated values. Reviewed
inventories and activity stages remain supplied inputs in this mode. Keep private
inputs and generated reports outside version control.
