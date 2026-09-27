# Agent instructions

## Purpose

Independently extract Harmony balance components at a pinned cutoff, apply
reviewed migration rules, and compare exact results with reference outputs.
Differences are evidence to investigate; never adjust measured values to force
agreement.

Read `README.md`, `docs/independent-recompute.md`, and
`docs/output-compatibility.md` before changing extraction or accounting logic.

## Architecture

- `extractor/`: standalone Go module with RLP decoding, Merkle Patricia trie
  traversal, validator-wrapper decoding, WONE storage reads, and receipt checks.
- `scripts/independent_raw.py`: validate selected-account JSON evidence from
  both shards and assemble it with reviewed policy.
- `scripts/independent-recompute.py`: exact arithmetic, totals, audit output,
  claims export, and comparison.
- `scripts/migration_format.py`: reference-compatible gross claims serialization.
- `scripts/run-full-server.py`: checkpoint verification and sequential full-state
  and receipt jobs, with status and logs.
- `schemas/`: pinned output contracts. `manifests/`: cutoff and policy context.
- `tests/` and `extractor/*_test.go`: synthetic regression fixtures and tests.

Keep extraction and accounting independent of Harmony, geth, and
harmony-migration code. Standard database and cryptographic dependencies are
allowed. Reference schemas and expected-output fixtures may be used to test
compatibility; reference balance values must not supply measured components.

## Data invariants

- Verify canonical hashes and state roots against the supplied manifest. Never
  substitute head state. Evidence must retain the exact manifest hash.
- Use integer atto-ONE for money: `10^18` atto = 1 ONE. Never use floating point.
- Native wallet = both liquid balances + pending undelegations + rewards +
  pending active-shard transfers. Active stake = self-stake + other delegation.
- Qualification includes eligible WONE and uses inclusive `>= 1000 ONE` before
  deductions. Confirmed exchange delivery follows reviewed manual-delivery rules.
- Apply reviewed deductions wallet-first, then vault. Preserve measured WONE
  separately from exclusions; exclude the WONE contract's self-held WONE.
- Exchange identities, deduction inventories, exclusions, and activity stages
  are reviewed inputs. Never infer them from a desired comparison result.
- Match pending transfers using canonical outgoing and incoming evidence at
  both cutoffs. Report but exclude unprovable retired-shard claims.
- Reject missing nodes, root mismatches, duplicate identities, conflicting
  cutoffs, incomplete evidence, and missing policy. Proven trie absence is zero;
  unavailable evidence is not.

## Coverage and output contracts

Probe JSON is incomplete. Selected-account reports cover only their selection.
The JSON assembler requires the same selection in both state and receipt reports.

Full-state mode exports per-shard component CSVs. Candidate CSVs provide only
address hints; read every amount from the raw database and record candidate
hashes. Preserve unresolved secure keys and report WONE coverage. Full-state
CSVs do not feed the selected-account JSON assembler directly. A full-network
claims workflow must account for all identities, both shards, verified transfers,
and reviewed policy before asserting completeness.

Use `--claims-output` for reference-compatible gross claims: 44 columns for
`migration`, 34 for `native`. Preserve header order, secure-key sorting, checksum
addresses, blank metadata conventions, integer values, 18-place ONE decimals,
valuation precision, and positive-claim row selection. The pinned contract is
`schemas/harmony-migration-claims-v1.json`. `--per-address` is a separate net audit.

Historical body or commit-bitmap defects require independently authenticated
evidence. Do not weaken receipt-root checks or trust spent markers as a repair.

## Database operations

Open only consistent offline databases or checkpoints, read-only at both the
filesystem and LevelDB layers. Respect locks. Do not modify chain data, stop
production services, or create interrupting checkpoints as a test step.

State and history scans require `--allow-global-scan`. Use explicit timeouts and
account caps for probes. Use fixtures for ordinary development; production scans
must follow the requested operational scope. Keep run status, hostnames, paths,
and access instructions in private operational records outside publishable files.

## Documentation and privacy

Comments explain logic, invariants, and reasons for checks. Documentation explains
interfaces and usage. Do not include session history, progress reports, personal
names, login identities, internal hostnames, workstation paths, or credentials.
Use generic paths in examples. Keep legally required license notices and public
dependency identifiers intact.

Keep private policy, account lists, databases, results, binaries, and generated
logs in ignored directories. Never commit credentials, tokens, keys, or private
production fixtures. Preserve unrelated changes. Do not imply that local files
have been committed, pushed, or deployed without verifying that action.

## Validation

Use `make build`, `make build-linux`, `make test`, and `make check`. Format Go with
`gofmt`. Test changed behavior with synthetic data, including malformed evidence,
threshold boundaries, policy contradictions, and exact output formatting.

Report what changed and which checks passed. Distinguish fixture compatibility,
component validation, and complete comparison; claim only what the evidence proves.
