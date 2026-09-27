# Harmony-migration output compatibility

The balance interchange artifact is a **gross claims CSV**. Use
`--claims-output` to produce it from complete raw evidence. The detailed
`--per-address` audit CSV remains separate: it includes split self/delegated stake,
qualification, deductions and net wallet/vault allocations.

## Pinned reference

The schema matches harmony-migration commit
`11781980d4af680b7c1fac3e6a737711e0de74a1`:

| Option | Reference artifact | Columns | Reference producer |
| --- | --- | ---: | --- |
| `--claims-format native` | `all-address-native-claims-cutoff.csv` | 34 | `toolkit/scripts/claims/format-all-claims.py` |
| `--claims-format migration` (default) | `all-address-migration-claims-cutoff.csv` | 44 | native formatter followed by `toolkit/scripts/claims/apply-wone-qualification.py` |

Exact header order is frozen in
[`harmony-migration-claims-v1.json`](../schemas/harmony-migration-claims-v1.json).
The reference formatter names this a claim even for native-positive accounts
below the distribution threshold. These are balances/gross entitlements, not
final issued amounts. The combined `full-snapshot-*.csv` with `eth_address` is a
different schema; it is not the canonical claims format described here.

## Field mapping

| Independent component | Reference claims column |
| --- | --- |
| Shard-0 liquid | `liquid_shard0_atto` |
| Shard-1 liquid | `liquid_shard1_atto` |
| Both liquid balances | `liquid_total_atto` |
| Self-stake + other delegation | `active_staked_or_delegated_atto`, `staked_to_vault_atto` |
| Pending undelegation | `pending_undelegation_atto` |
| Unclaimed reward | `unclaimed_staking_reward_atto` |
| Unreceived transfers to active shards | `pending_cross_shard_atto` |
| Native wallet components | `native_wallet_airdrop_atto` in WONE format |
| WONE after reviewed exclusions | `wone_balance_atto` |
| Deliverable WONE | `wone_airdrop_atto` |
| Native wallet + deliverable WONE | `wallet_airdrop_atto` in WONE format |
| Native total | `native_total_claim_atto` in WONE format |
| Native total + eligible WONE | `qualification_total_atto` |
| Gross wallet + stake | `total_claim_atto` |

Native format has no WONE fields: its `wallet_airdrop_atto` and `total_claim_atto`
are native-only. Both formats have exact `*_one` decimal counterparts. Do not
rename `self_stake_atto` to `active_staked_or_delegated_atto`: the latter includes
**both** self-stake and other delegation. Do not write net allocations into any
of these gross fields.

## Serialization and metadata

- CSV is UTF-8, comma-delimited, LF-terminated, with the exact ordered header.
- Rows are unique and sorted by ascending lowercase `secure_key` (Keccak-256 of
  the 20 address bytes), not by address, size or input position.
- `address` and `address_or_secure_key` use the extractor's EIP-55 checksum form;
  selected addresses are resolved, so `address_resolved` is `true`.
- `*_atto` fields are decimal integer strings. `*_one` has exactly 18 places.
  No floating point or exponent notation is used.
- Cutoff blocks, price reference block and price come from the manifest.
  USD fields are display-only and have `18 + price fractional digits` places
  (26 with the bundled price). They do not affect eligibility or balances.
- The raw reader emits secure key, checksum address, account presence, nonce and
  code hash. An old state report without this metadata must be regenerated;
  metadata must not be filled from the team's CSV.
- Base native claims inherit metadata only from positive liquid rows. Thus the
  compatible CSV uses blanks for a shard without a positive liquid row, even
  when richer state metadata is available in the evidence. WONE-only recipient
  rows include shard-0 metadata and blank shard-1 metadata, matching the overlay
  producer. These blanks are an **output convention**, not a claim that raw
  metadata is unavailable or that an account doesn't exist.
- Later metadata/activity-enriched variants may contain additional columns or
  filled-in metadata. The comparator checks canonical fields and reports extra
  fields as not compared; metadata differences are reported, not silently
  discarded. Such enriched variants are not the exact export target above.

## Row population and scope

Native claims include selected addresses with positive native total. Migration
claims additionally include WONE-only addresses with deliverable WONE (ordinary
qualification or confirmed exchange delivery). Zero-claim selections and
nonqualifying, nonexchange WONE-only selections have no canonical claim row.
Their addresses are recorded in the JSON report under
`selected_accounts_without_claim_rows`; they are not silently lost.

The output is the **selected-account subset** of the reference format. It does
not assert full-network coverage. The audit report can still contain all selected
accounts, including those with no canonical claim row.

## Usage

After producing complete state and receipt evidence for both shards:

```sh
python3 scripts/independent-recompute.py \
  --raw-state0 artifacts/raw-state0.json \
  --raw-state1 artifacts/raw-state1.json \
  --raw-receipts0 artifacts/raw-receipts0.json \
  --raw-receipts1 artifacts/raw-receipts1.json \
  --policy artifacts/reviewed-policy.json \
  --claims-output artifacts/all-address-migration-claims-cutoff.csv \
  --claims-format migration \
  --per-address artifacts/independent-net-audit.csv \
  --compare artifacts/team-all-address-migration-claims-cutoff.csv \
  --output artifacts/comparison.json
```

Use `--claims-format native` and an appropriate filename for native-only output.
The `--compare` input is detected independently from its headers, so either
reference native or WONE claims can be compared directly. The older combined
snapshot comparison (`eth_address` and eight split components) also remains
supported. Team files never supply measured components or missing metadata.

Missing or duplicate selected team rows are failures. For canonical claims,
unexpected rows for selected addresses that should have no claim are also
failures. Exact one-atto and formatted-decimal differences are reported.

## Regression evidence

`tests/fixtures/migration-claims-golden.json` contains **synthetic** input rows
and expected CSV bytes generated using the reference formatter and WONE overlay
at the pinned commit. It covers mixed liquid/staking components, fractional atto
precision, reviewed deductions (gross must remain gross), below-threshold WONE,
WONE-only recipients, exchange delivery, absent-shard metadata, zero balances,
and secure-key ordering. Runtime and tests do not import the reference repo.

A passing fixture comparison proves schema/serialization compatibility for those
cases. It does not establish production balance agreement or resolve incomplete
raw databases, historical receipt defects, or unreviewed policy inputs.
