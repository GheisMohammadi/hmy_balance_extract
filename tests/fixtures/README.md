# Synthetic compatibility reference

`migration-claims-golden.json` contains invented component balances for six
repeated-byte address patterns. It contains no extracted production balances.

The reference CSV strings were generated from harmony-migration commit
`11781980d4af680b7c1fac3e6a737711e0de74a1`:

1. Serialize native-positive input rows as the source ledger (self stake and
   delegated principal combined into `active_delegation_atto`).
2. Run `toolkit/scripts/claims/format-all-claims.py` with the bundled cutoff
   blocks and valuation to obtain the expected 34-column native CSV.
3. Apply that repository's `apply_overlay`/`new_holder_row` functions from
   `toolkit/scripts/claims/apply-wone-qualification.py`, with the synthetic WONE
   balances and exchange flag, then write its `output_fields` in secure-key order.
4. Freeze the native and WONE CSV bytes in this JSON file.

Only schema/expected-output fixtures are retained here, not source calculation
functions. Ordinary tests use these fixed expectations and do not read or import
harmony-migration. The reference price is display-only. The tested atto amounts
include a one-atto fraction so floating-point loss would fail the regression.
