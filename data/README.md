# Independent cutoff snapshots

Built from the completed state extracts on `mhe-archs0-01` (shard 0) and
`mhe-explorers1-02` (shard 1) at the pinned 2026-09-10 cutoff. Column layout
matches `README-redacted.md`.

## Included

- Exact liquid balances, self-stake, other delegation, pending undelegation,
  unclaimed reward, and WONE balances from authenticated state.
- Whole-ONE public files use half-up rounding.
- Rows sorted by `one1_address`.

## Not included in this commit

- Pending cross-shard receipts: receipt verification stopped on an incoming-root
  mismatch, so `pending_cross_shard_atto` is blank and is not treated as zero.
- Activity dates, incident labels, exchange stages, and non-issuing policy.
- Positive shard-0 liquid balances without a resolved address (reported in the
  summary under `unresolved_positive_liquid_shard0`).

See `snapshot-20260911-summary.json` for SHA-256 values, row counts, and the
limits list.
