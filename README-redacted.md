# Harmony cutoff snapshot (2026-09-10T14:00:00Z), redacted for independent reproduction

Per-address balances, incident deductions and last activity at the migration
cutoff: shard-0 block `93,623,067`
(`0x23572e11f6ef9afe4c27ab3102f15b99fd7277ae5f685ccaef0ae571fe7ee0b6`) and
shard-1 block `95,882,100`
(`0xf801577a5480175c05a63c8e4e5cf3d2623e1a01bdf176e16b46967405ae02ee`). The
`20260911` in the file names is the accounting date of the migration ledger;
the state itself is the 2026-09-10 cutoff.

## About this version

This README describes the files without their results. Row counts, amounts,
address counts, incident totals, cross-check outcomes and file hashes are
withheld under `harmony-migration/docs/numerical-embargo.md`, so that your
results are not anchored to ours. Cutoff identifiers, thresholds, column
definitions, labels and method are unchanged.

Please produce the four CSVs below and `data/snapshot-20260911-summary.json`
in exactly this format (see Format rules and Summary file), and record the
values listed under Numbers to report before comparing with ours. The same
inputs and rules should give byte-identical files, so SHA-256 is the first
comparison.

| File | Contents |
|---|---|
| `full-snapshot-20260911.csv` | every address, exact atto amounts, policy, incident and activity detail |
| `snapshot-breakdown-20260911.csv` | addresses holding at least 1 ONE (native plus WONE), whole-ONE amounts with the balance breakdown, YYYYMMDD dates |
| `snapshot-20260911.csv` | the same addresses without the balance breakdown |
| `snapshot-20260911-small.csv` | the columns of `snapshot-20260911.csv` for addresses holding at least 10 ONE; sized to commit to GitHub |

All are sorted by `one1_address`. SHA-256 values and the reconciliation
checks go in `data/snapshot-20260911-summary.json`.

## Which addresses are included

- The migration ledger has one row per native position (positive liquid
  balance on shard 0 or 1, stake, pending undelegation, unclaimed reward or
  pending cross-shard receipt), plus the WONE-only rows the migration added
  because they qualified or belong to an exchange.
- The WONE census lists every positive WONE holder at the cutoff. Most of them
  are not ledger rows; the union of the census and the ledger is the address
  universe considered here.
- WONE-only holders (in the census, not in the ledger) holding fewer than 10
  atto WONE are a dust spray and are excluded. The other WONE-only holders are
  included as `row_source = wone_census`.

The ledger records WONE held by excluded system addresses as zero; these files
report actual cutoff balances instead (the WONE contract's own WONE balance and
that of `0x…dead`). Every WONE balance plus the excluded dust equals WONE
`totalSupply()` at the cutoff.

Summing `total_balance` double-counts value that backs other balances: the WONE
contract's native ONE is the reserve behind every WONE balance, and the two
LayerZero contracts hold the ONE behind bridged ONE on other chains.

## Public files

`snapshot-breakdown-20260911.csv`, `snapshot-20260911.csv` and
`snapshot-20260911-small.csv` give amounts in whole ONE, rounded half up, and
dates as UTC `YYYYMMDD`. Reported theft victims are not labeled in any of them.

### `snapshot-breakdown-20260911.csv`

Rows: every address whose exact total (native plus WONE) is at least 1 ONE.
Columns, in this order:

```text
one1_address,eth_address,is_contract,is_validator,total_balance,shard0_liquid_balance,shard1_liquid_balance,staked_balance,delegated_balance,pending_undelegation,unclaimed_reward,wone_balance,last_signed_tx_date,last_inbound_tx_date,special_label,non_issuing_amount
```

| Column | Meaning |
|---|---|
| `one1_address` | bech32 form of the account |
| `eth_address` | lowercase hex form of the same account |
| `is_contract` | `true` if the address has EVM code on shard 0 or 1 at the cutoff; validators are `false` (Harmony stores a validator's wrapper in its code field) |
| `is_validator` | `true` if the address is a registered validator (`hmyv2_getAllValidatorAddresses`, checked against the cutoff state-root validator discovery) |
| `total_balance` | the seven balance columns below, plus pending cross-shard receipts (which have no column here) |
| `shard0_liquid_balance` | liquid ONE on shard 0 |
| `shard1_liquid_balance` | liquid ONE on shard 1 |
| `staked_balance` | a validator's active self-delegation |
| `delegated_balance` | active delegation to validators other than the address itself |
| `pending_undelegation` | undelegated principal not yet released |
| `unclaimed_reward` | claimable staking reward |
| `wone_balance` | WONE token balance |
| `last_signed_tx_date` | date of the newest regular or staking transaction signed by the address on either shard; blank if it never signed (or is a contract) |
| `last_inbound_tx_date` | date of the newest regular transaction sent to the address by another account, on either shard; blank if none |
| `special_label` | why part or all of the balance is not issued, `;`-separated: incident slugs and the labels in Non-issuing amount and special labels |
| `non_issuing_amount` | ONE of this address's balance that will not be issued to it; compare with `total_balance` for partial amounts |

Each column is rounded separately, so the components need not add up to
`total_balance` exactly.

### `snapshot-20260911.csv`

Rows: the same addresses as `snapshot-breakdown-20260911.csv`. Columns, in
this order:

```text
one1_address,eth_address,account_type,total_balance,last_signed_tx_date,last_inbound_tx_date,special_label,non_issuing_amount
```

| Column | Meaning |
|---|---|
| `one1_address` | bech32 form of the account |
| `eth_address` | lowercase hex form of the same account |
| `account_type` | `c` contract, `v` validator, blank for an ordinary wallet (the breakdown's `is_contract` and `is_validator` combined) |
| `total_balance` | native total (liquid on both shards, self-stake, delegation, pending undelegation, unclaimed reward and pending cross-shard receipts) plus WONE |
| `last_signed_tx_date` | date of the newest regular or staking transaction signed by the address on either shard; blank if it never signed (or is a contract) |
| `last_inbound_tx_date` | date of the newest regular transaction sent to the address by another account, on either shard; blank if none |
| `special_label` | why part or all of the balance is not issued, `;`-separated: incident slugs and the labels in Non-issuing amount and special labels |
| `non_issuing_amount` | ONE of this address's balance that will not be issued to it; compare with `total_balance` for partial amounts |

### `snapshot-20260911-small.csv`

Rows: the addresses of `snapshot-20260911.csv` whose exact total is at least
10 ONE; sized to commit to GitHub. Columns, in this order, with the same
meanings and values as in `snapshot-20260911.csv`:

```text
one1_address,eth_address,account_type,total_balance,last_signed_tx_date,last_inbound_tx_date,special_label,non_issuing_amount
```

## Non-issuing amount and special labels

`non_issuing_amount` is the sum of:

| Component | Label |
|---|---|
| Incident deductions | incident slug (see Incidents) |
| Burn addresses: `0x7bdef7…e7ad` (hard-coded supply-endpoint burn), `0x…dead`, the zero address | `burn-address` |
| Precompile-range addresses (`0x01` to `0xff`) in the migration's burn-or-inaccessible inventory | `precompile` |
| `0xd5cd…f9d3`, on Ethereum the legacy bridged Harmony ONE (1ONE) ERC-20 contract | `inaccessible` |
| SmartVault contracts excluded from the migration | `excluded-smartvault` |
| Other reviewed genuine contracts excluded from the migration | `excluded-contract` |
| The WONE contract: its native ONE backs every WONE balance and is redistributed to qualifying WONE holders | `wone-reserve` |
| WONE held below the combined 1,000 ONE threshold (not delivered in the current migration; retained in the 2050 reserve) | `wone-below-threshold` |

Across all rows the amounts should reconcile exactly: non-issuing total = the
migration's compiled not-issued total + the WONE reserve (equal to WONE
`totalSupply()`) − the excluded WONE dust. The WONE reserve overlaps the WONE
balances it backs, just as in `total_balance`.

Exchange wallets are not non-issuing: their whole entitlement is delivered
manually to the exchange from the 2050 supply reserve.

## `full-snapshot-20260911.csv`

All amounts are exact integers in atto (1 ONE = 10^18 atto) and times are
ISO 8601 UTC.

- Identity: `one1_address`, `eth_address`, `row_source` (`native_ledger`,
  `wone_only_ledger`, `wone_census`), `is_contract`, `is_validator`,
  `contract_category` (reviewed contracts only: `erc20-token`, `known-app`,
  `multisig-wallet`, `nft-contract`, `onewallet`, `pattern-identified`,
  `smartvault-wallet`, `unidentified`), `nonce_shard0`, `nonce_shard1`
  (blank when not measured).
- Balances: `liquid_shard0_atto`, `liquid_shard1_atto`, `self_stake_atto`,
  `delegated_atto`, `pending_undelegation_atto`, `unclaimed_reward_atto`,
  `pending_cross_shard_atto`, `native_total_atto`, `wone_balance_atto`,
  `total_balance_atto`.
- Migration policy: `qualified_1000_one` (native plus WONE at least 1,000 ONE,
  gross), `exchange` (confirmed exchange inventory; `;`-separated lowercase
  exchange slugs), `migration_stage` (`initial`, `deferred`, `next_stage`,
  `exchange_manual` or blank) and `issuance_treatment` (`issue`, `not_issued`,
  `manual_from_reserve` or blank) from the stage policy (exchange wallets are
  `exchange_manual` / `manual_from_reserve`), `wone_airdrop_atto`,
  `migration_allocation_atto` (qualified rows only). A qualified wallet whose
  total minus its incident deductions is below 1,000 ONE is `deferred`, not
  `initial`.
- Labels and incidents: `special_label` (as in the public files, but including
  reported victims), `related_incident`, `incident_role`,
  `incident_deduction_atto`, `incident_deduction_scope` (`none`, `partial`,
  `full` relative to the native total), and the deduction by kind:
  `exploit_distribution_retained_atto`, `revert_leak_credit_atto`,
  `extra_payout_atto`, `wallet_theft_atto`.
- Other non-issuance: `burn_or_inaccessible_atto`,
  `reviewed_contract_non_issuance_atto`, `wone_not_delivered_atto`,
  `wone_reserve_atto`, and `non_issuing_atto`, which is all of them plus the
  incident deduction.
- Activity (addresses of at least 1 ONE, `activity_coverage = collected`;
  otherwise `activity_coverage = not_collected_below_1_one`): `signed_check`,
  `last_signed_time_utc`, `last_signed_shard`, `last_signed_type` (`regular`
  or `staking`), `last_signed_tx_hash`, `last_inbound_time_utc`,
  `last_inbound_shard`, `last_inbound_from`, `last_inbound_value_atto`,
  `last_inbound_tx_hash`.

## Incidents

Only addresses in the migration's reviewed inventories are labeled. Broader
investigative scopes (the historical-hack routing inventory, every affected
December 2023 delegator) are not, because appearing there is not a finding
about the address. Slugs start with the first UTC date of the incident;
`data/incidents-20260910.csv` lists all of them (`incident_slug`,
`first_date`, `last_date`, `description`, `source`).

| Slug | Roles |
|---|---|
| `20231103-undelegation-repeat-payout` (repeated matured-undelegation payouts after HIP-30, through 2023-12-16) | `extra_payout_recipient` |
| `20250518-cx-revert-leak`, `20260408-cx-revert-leak` | `exploit_distribution_recipient` (retained initial-distribution cap) |
| every revert-leak cohort, including `20260616-…` and `20260713-…` | `leak_credit_recipient` |
| `YYYYMMDD-wallet-theft-<report tag or case>` | `reported_perpetrator`, `theft_recipient` |
| same wallet-theft slugs | `reported_victim` (never deducted; full snapshot only) |

Deductions are capped by what the address still held, so many are partial.

A revert leak is a cross-shard transfer whose source debit was reverted while
the destination credit still applied, creating unbacked ONE. The
harmony-migration and harmony-supply-audit files call it a "rollback leak"; it is
unrelated to the August 2026 chain rollback that removed the forged credits.

Victims are dated only from transaction evidence: the inventory's own source
transaction, or the victim's transfer to a reported perpetrator address.
Victims with no such transfer carry `undated-wallet-theft-caseNNN`. Roles come
from the supplied theft reports; they are allegations, not findings of
identity or intent. Victim names are not included.

## Activity method

- Shard 0: `harmony-migration/toolkit/cmd/account-directional-activity`, run on
  the archival node against its explorer index and chain database. Every
  examined index entry is classified from the canonical block body by the
  recovered sender, because the index keeps only the received flag for a
  self-transfer or a validator's own staking message. Entries that point at the
  wrong in-block position are repaired from the block, and entries absent from
  the canonical chain (for example the discarded August 2026 branch) are
  skipped.
- Shard 1: the archival RPC transaction history (no shard-1 database is
  available), with canonical block-hash checks on recent entries.
- Signed results are checked against the cutoff nonce (`signed_check`):
  `verified` means the found transaction carries nonce cutoff − 1.
  `verified_rpc_index` means the same, for a shard-0 index gap resolved from
  the archival RPC transaction history. `located_by_nonce` means an archival
  nonce binary search found the transaction the index missed.
  `unsigned_nonce_change` means the last nonce increase came from contract
  creation at that address, not a signature. `never_signed` means a zero
  nonce and `contract` a contract address. When the two shards differ, the
  values other than `never_signed` are sorted and `;`-joined.
- Cross-checks: a subset of addresses was also crawled over RPC and compared
  on last inbound and last signed block, and a sample was rescanned with an
  order-independent scan of every index entry and compared with the shard-0
  results. Their sizes and outcomes are withheld; report yours.
- Only direct top-level transactions count. Internal contract transfers, WONE
  or other token transfers, staking rewards, and receipts applied on the
  destination shard are not inbound activity. A cross-shard transfer counts at
  its source transaction.
- Transaction hashes are the Harmony hash, or the Ethereum hash for
  Ethereum-format transactions; both resolve through the archival RPC.

## Format rules

These determine the bytes of each file, so they must match for SHA-256 values
to be comparable.

- UTF-8, one header row, `\n` line endings, Python `csv` default quoting.
- Rows sorted by `one1_address` as a plain string.
- Booleans are `true` / `false`. Atto amounts are base-10 integers (`0` when
  zero); fields that do not apply are blank.
- Whole ONE is `(atto + 5×10^17) div 10^18`. Inclusion in the at-least-1-ONE
  files uses exact `total_balance_atto ≥ 10^18` before rounding, and in the
  small file `total_balance_atto ≥ 10 × 10^18`.
- Full-snapshot times are `YYYY-MM-DDTHH:MM:SSZ`; the public files take the
  first ten characters without dashes (`YYYYMMDD`).
- `special_label`: sorted unique incident slugs, then the non-incident labels
  in the order `burn-address` / `precompile` / `inaccessible`, then
  `wone-reserve` / `excluded-smartvault` / `excluded-contract`, then
  `wone-below-threshold`. The public files omit slugs that come only from a
  `reported_victim` role.
- `account_type` is `c` when `is_contract` is `true`, otherwise `v` when
  `is_validator` is `true`, otherwise blank.

## Summary file

`data/snapshot-20260911-summary.json`: two-space indent, sorted keys, trailing
newline, atto amounts as decimal strings.

- `cutoff_time_utc`, and `cutoff` keyed by shard with `block` and `hash`.
- `full_snapshot`, `snapshot_breakdown`, `snapshot`, `snapshot_small`: each
  `path`, `rows`, `sha256`; `snapshot_small` also has
  `minimum_total_balance_one` (`10`).
- `non_issuing_components_atto`: `incident_deduction_atto`,
  `burn_or_inaccessible_atto`, `reviewed_contract_non_issuance_atto`,
  `wone_not_delivered_atto`, `wone_reserve_atto`, `non_issuing_atto`.
- `row_counts` (over all rows): `rows_native_ledger`, `rows_wone_only_ledger`,
  `rows_wone_census`, `rows_at_least_1_one`, `rows_non_issuing`,
  `incident_scope_none` / `_partial` / `_full`, and `label_<label>` for each
  non-incident label.
- `ledger_wone_overrides`: `address`, `ledger_wone_atto`, `census_wone_atto`
  for each ledger row whose WONE differs from the census.
- `wone_dust`: `rule`, `rows_excluded`, `atto_excluded`.
- `reconciliation`: `{ "value": ..., "expected": ... }` for each check below.
- `inputs` (`path`, `sha256` of each external input) and `derived_inputs`
  (`sha256` of each intermediate file).

| Check | Value | Expected |
|---|---|---|
| `ledger_rows` | ledger rows read | the migration ledger's row count |
| `native_total_atto` | sum of `native_total_atto` | the ledger's total native claim |
| `wone_including_excluded_dust_atto` | sum of `wone_balance_atto` plus excluded dust | WONE `totalSupply()` at the cutoff |
| `incident_deduction_atto` | sum of incident deductions | the migration's total incident deductions |
| `burn_or_inaccessible_atto` | sum of `burn_or_inaccessible_atto` | the migration's burn-or-inaccessible total |
| `reviewed_contract_non_issuance_atto` | sum of `reviewed_contract_non_issuance_atto` | the migration's reviewed-contract total |
| `wone_reserve_atto` | `wone_reserve_atto` of the WONE contract | WONE `totalSupply()` |
| `non_issuing_atto` | sum of `non_issuing_atto` | not-issued total + WONE reserve − excluded dust |
| `activity_rows` | rows of at least 1 ONE | activity records collected |

## Numbers to report

Besides the summary file, please record:

- rows and SHA-256 of each of the four CSVs;
- migration ledger rows, split into native positions and WONE-only ledger rows;
- positive WONE holders in the census, how many of them are ledger rows, and
  the size of the union;
- WONE-only holders, how many are dust (below 10 atto) with their total atto,
  and how many are included as `wone_census`;
- WONE `totalSupply()` at the cutoff, and the WONE balances of the WONE
  contract and `0x…dead`;
- registered validators;
- rows per non-issuance label, over all rows and over rows of at least 1 ONE;
- the non-issuing total, the migration's compiled not-issued total and the WONE
  reserve;
- rows and withheld ONE for each row of the Incidents table;
- incidents in `data/incidents-20260910.csv`, and undated victims;
- the activity cross-checks: addresses compared and mismatches found.

## Reproduce

```sh
cd scripts
python3 fetch_validators.py        # data/validators-20260910.csv
python3 prepare_populations.py     # work/activity-population.csv, work/wone-only-census-rows.csv
python3 fetch_account_meta.py      # work/account-meta.csv, work/activity-candidates.csv
# shard 0 on the archival node (node stopped; see the tool's -h):
#   account-directional-activity -shard 0 -candidates activity-candidates.csv ...
python3 fetch_activity.py --population ../work/activity-candidates.csv --output-dir ../work/activity-shard1 --shards 1
python3 merge_activity.py          # data/activity-20260910.csv
python3 build_incidents.py         # data/incident-roles-20260910.csv, data/incidents-20260910.csv
python3 build_snapshot.py          # the four snapshot CSVs and data/snapshot-20260911-summary.json
```

Inputs are read from sibling checkouts of `harmony-migration` and
`harmony-supply-audit` (override with `HARMONY_MIGRATION` /
`HARMONY_SUPPLY_AUDIT`); their paths and SHA-256 values are pinned in the
`data/*-summary.json` files. `work/` holds intermediate and raw crawl outputs
and is not tracked.
