#!/usr/bin/env python3

"""Calculate migration claims using exact integer atto-ONE arithmetic.

Raw mode combines verified component evidence with reviewed policy. Snapshot
mode trusts CSV components and checks arithmetic. Qualification includes eligible
WONE; deductions reduce wallet claims before vault claims. Reference CSVs are
comparison inputs only. Probe evidence is rejected.
"""

import argparse
import csv
import json
import sys
from contextlib import nullcontext, ExitStack
from evidence_io import atomic_text, strict_csv
from independent_raw import address as validate_address
from pathlib import Path


ATTO_PER_ONE = 10**18

# Raw chain-measured components (exact atto integers). Everything else is
# derived from these plus the reviewed deduction inputs below.
COMPONENT_FIELDS = (
    "liquid_shard0_atto",
    "liquid_shard1_atto",
    "self_stake_atto",
    "delegated_atto",
    "pending_undelegation_atto",
    "unclaimed_reward_atto",
    "pending_cross_shard_atto",
    "wone_balance_atto",
)

# Reviewed non-issuance amounts taken as inputs (blank counts as zero).
DEDUCTION_FIELDS = (
    "incident_deduction_atto",
    "burn_or_inaccessible_atto",
    "reviewed_contract_non_issuance_atto",
    "wone_not_delivered_atto",
    "wone_reserve_atto",
    "non_issuing_atto",
)

# Snapshot columns recomputed independently and compared under --check.
DERIVED_FIELDS = (
    "native_total_atto",
    "total_balance_atto",
    "wone_airdrop_atto",
    "qualified_1000_one",
    "migration_allocation_atto",
)

REQUIRED_FIELDS = (
    ("eth_address", "one1_address", "row_source", "exchange", "migration_stage",
     "issuance_treatment")
    + COMPONENT_FIELDS
    + DEDUCTION_FIELDS
    + DERIVED_FIELDS
)

LEDGER_SOURCES = ("native_ledger", "wone_only_ledger")


class SnapshotError(ValueError):
    """The snapshot cannot be read, or a row is malformed."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path,
                        help="full per-address cutoff snapshot CSV")
    parser.add_argument("--manifest", type=Path,
                        default=Path(__file__).resolve().parents[1] / "manifests/snapshot-2026-09-10.json")
    for name in ("state0", "state1", "receipts0", "receipts1"):
        parser.add_argument("--raw-" + name, type=Path,
                            help="complete independent-db evidence JSON")
    parser.add_argument("--policy", type=Path, help="explicit reviewed policy JSON for raw mode")
    parser.add_argument("--compare", type=Path, help="team result CSV; never used as balance input")
    parser.add_argument("--output", type=Path, help="write the totals JSON here")
    parser.add_argument("--per-address", type=Path,
                        help="write the detailed net-allocation audit CSV here")
    parser.add_argument("--claims-output", type=Path, help="write a harmony-migration-compatible gross balance CSV (raw mode)")
    parser.add_argument("--claims-format", choices=("native", "migration"), default="migration",
                        help="reference claims schema for --claims-output (default migration with WONE)")
    parser.add_argument("--minimum-one", type=int, default=1000,
                        help="qualification threshold in whole ONE (default 1000)")
    parser.add_argument("--check", action="store_true",
                        help="compare recomputed values with the snapshot's own columns")
    parser.add_argument("--max-mismatches", type=int, default=50,
                        help="how many column mismatches to list (default 50)")
    parser.add_argument("--replace", action="store_true", help="overwrite outputs")
    args = parser.parse_args(argv)
    raw = [args.raw_state0, args.raw_state1, args.raw_receipts0, args.raw_receipts1, args.policy]
    if any(raw):
        if not all(raw) or args.snapshot or args.check:
            parser.error("raw mode requires both state reports, both receipt reports and policy; do not use --snapshot/--check")
        if args.minimum_one != 1000:
            parser.error("raw mode is pinned to the agreed 1000 ONE threshold")
    elif args.snapshot is None:
        parser.error("supply --snapshot or complete raw evidence inputs")
    if args.minimum_one < 0 or args.max_mismatches <= 0:
        parser.error("minimum must be non-negative and max-mismatches must be positive")
    if args.compare and not all(raw):
        parser.error("--compare requires raw mode so team balances cannot become inputs")
    outputs = [p.resolve() for p in (args.output, args.per_address, args.claims_output) if p]
    inputs = [p.resolve() for p in (args.snapshot, args.manifest, args.compare, *raw) if p]
    if len(outputs) != len(set(outputs)) or set(outputs) & set(inputs):
        parser.error("output files must be distinct from each other and all input files")
    if args.claims_output and not all(raw):
        parser.error("--claims-output requires complete raw evidence and metadata")
    return args


def parse_uint(value, field, line):
    """Exact non-negative integer; blank is treated as zero."""
    text = (value or "").strip()
    if text == "":
        return 0
    if not text.isdigit():
        raise SnapshotError(f"{field} at line {line}: not a non-negative integer: {value!r}")
    return int(text)


def one_str(atto):
    """Exact fixed 18-decimal ONE rendering of an atto integer, for display."""
    whole, fraction = divmod(atto, ATTO_PER_ONE)
    return f"{whole}.{fraction:018d}"


def whole_one(atto):
    """Whole ONE rounded half up: (atto + 5*10^17) div 10^18."""
    return (atto + 5 * 10**17) // ATTO_PER_ONE


def split_wallet_first(wallet, staked, deduction):
    """Apply a deduction to the wallet part before the vault part.

    Returns (final_wallet, final_vault, applied). The deduction never exceeds
    the gross claim; anything beyond wallet + staked is reported as applied at
    the gross and leaves nothing.
    """
    gross = wallet + staked
    applied = min(deduction, gross)
    final_wallet = max(wallet - applied, 0)
    final_vault = gross - applied - final_wallet
    return final_wallet, final_vault, applied


def recompute_row(row, line, threshold):
    """Recompute one ledger row's claim and delivery from raw components."""
    values = {field: parse_uint(row.get(field), field, line) for field in COMPONENT_FIELDS}
    deductions = {field: parse_uint(row.get(field), field, line) for field in DEDUCTION_FIELDS}

    native_wallet = (
        values["liquid_shard0_atto"]
        + values["liquid_shard1_atto"]
        + values["pending_undelegation_atto"]
        + values["unclaimed_reward_atto"]
        + values["pending_cross_shard_atto"]
    )
    staked = values["self_stake_atto"] + values["delegated_atto"]
    native_total = native_wallet + staked
    wone = values["wone_balance_atto"]
    qualification = native_total + wone
    total_balance = native_total + wone

    is_exchange = (row.get("exchange") or "").strip() != ""
    qualifies = qualification >= threshold
    wone_airdrop = wone if (qualifies or is_exchange) else 0
    wallet_airdrop = native_wallet + wone_airdrop
    total_claim = wallet_airdrop + staked

    # Reviewed deductions that reduce the delivered claim. wone_not_delivered is
    # excluded here: below-threshold WONE never entered wallet_airdrop, so it is
    # absent from the claim rather than deducted from it.
    claim_deduction = (
        deductions["incident_deduction_atto"]
        + deductions["burn_or_inaccessible_atto"]
        + deductions["reviewed_contract_non_issuance_atto"]
        + deductions["wone_reserve_atto"]
    )
    # A migration allocation exists only for a qualifying, non-exchange row.
    # Below-threshold rows receive nothing; exchange rows are delivered manually.
    if qualifies and not is_exchange:
        final_wallet, final_vault, applied = split_wallet_first(wallet_airdrop, staked, claim_deduction)
        migration_allocation = final_wallet + final_vault
    else:
        final_wallet = final_vault = migration_allocation = 0
        applied = 0

    return {
        **values,
        "eth_address": (row.get("eth_address") or "").strip().lower(),
        "one1_address": (row.get("one1_address") or "").strip(),
        "row_source": (row.get("row_source") or "").strip(),
        "is_exchange": is_exchange,
        "qualifies": qualifies,
        "exact_threshold": qualification == threshold,
        "migration_stage": (row.get("migration_stage") or "").strip(),
        "issuance_treatment": (row.get("issuance_treatment") or "").strip(),
        "native_wallet_atto": native_wallet,
        "staked_atto": staked,
        "native_total_atto": native_total,
        "wone_balance_atto": wone,
        "qualification_atto": qualification,
        "total_balance_atto": total_balance,
        "wone_airdrop_atto": wone_airdrop,
        "wallet_airdrop_atto": wallet_airdrop,
        "total_claim_atto": total_claim,
        "claim_deduction_atto": applied,
        "final_wallet_atto": final_wallet,
        "final_vault_atto": final_vault,
        "migration_allocation_atto": migration_allocation,
        "_deductions": deductions,
    }


def column_mismatches(row, computed, line, threshold):
    """Differences between the recomputed values and the snapshot's own columns."""
    problems = []

    def check(field, expected, recomputed):
        actual = parse_uint(row.get(field), field, line)
        if actual != recomputed:
            problems.append(
                f"line {line} {computed['eth_address']}: {field} column {actual} "
                f"!= recomputed {recomputed} ({expected})"
            )

    check("native_total_atto", "wallet + stake components", computed["native_total_atto"])
    check("total_balance_atto", "native total + WONE", computed["total_balance_atto"])
    check("wone_airdrop_atto", "WONE for qualifying or exchange rows", computed["wone_airdrop_atto"])

    qualified_col = (row.get("qualified_1000_one") or "").strip().lower()
    expected_flag = "true" if computed["qualifies"] else "false"
    if qualified_col != expected_flag:
        problems.append(
            f"line {line} {computed['eth_address']}: qualified_1000_one column "
            f"{qualified_col!r} != recomputed {expected_flag}"
        )

    # migration_allocation is only meaningful for issued (non-exchange) rows;
    # exchange wallets are delivered manually and carry no allocation column.
    if not computed["is_exchange"]:
        check("migration_allocation_atto", "claim minus reviewed deductions",
              computed["migration_allocation_atto"])
    return problems


def zero_totals():
    return {
        "rows": 0,
        "native_wallet_atto": 0,
        "staked_atto": 0,
        "native_total_atto": 0,
        "wone_balance_atto": 0,
        "wone_airdrop_atto": 0,
        "wallet_airdrop_atto": 0,
        "total_claim_atto": 0,
        "final_wallet_atto": 0,
        "final_vault_atto": 0,
        "migration_allocation_atto": 0,
    }


def add_totals(bucket, computed):
    bucket["rows"] += 1
    for field in bucket:
        if field != "rows":
            bucket[field] += computed[field]


def per_address_fields():
    return (
        "eth_address", "one1_address", "row_source", "qualifies", "is_exchange",
        "migration_stage", *COMPONENT_FIELDS,
        "native_wallet_atto", "staked_atto", "native_total_atto",
        "qualification_atto", "total_balance_atto", "wone_airdrop_atto", "wallet_airdrop_atto",
        "total_claim_atto", "migration_allocation_atto", "final_wallet_atto",
        "final_vault_atto",
    )


def recompute(path, threshold, check, max_mismatches, per_address_out=None, replace=False,
              input_rows=None):
    all_rows = zero_totals()
    qualifying = zero_totals()
    initial = zero_totals()
    deferred = zero_totals()
    next_stage = zero_totals()
    exchange = zero_totals()
    census = {"rows": 0, "wone_balance_atto": 0}
    exact_threshold_rows = 0
    non_issuing = {field: 0 for field in DEDUCTION_FIELDS}

    writer = None
    stack = ExitStack()

    mismatches = []
    mismatch_count = 0
    with stack:
        if per_address_out is not None:
            handle_out = stack.enter_context(atomic_text(per_address_out, replace))
            writer = csv.DictWriter(handle_out, fieldnames=per_address_fields(), lineterminator="\n")
            writer.writeheader()
        context = path.open(newline="", encoding="utf-8") if input_rows is None else nullcontext()
        with context as source:
            if input_rows is None:
                headers, reader = strict_csv(source)
            else:
                if not input_rows:
                    raise SnapshotError("empty account selection")
                headers, reader = input_rows[0].keys(), input_rows
            seen_addresses = set()
            required = REQUIRED_FIELDS if input_rows is None else tuple(
                f for f in REQUIRED_FIELDS if f not in DERIVED_FIELDS)
            missing = [field for field in required if field not in headers]
            if missing:
                raise SnapshotError(f"snapshot is missing columns: {', '.join(missing)}")
            for line, row in enumerate(reader, start=2):
                source_kind = (row.get("row_source") or "").strip()
                if source_kind == "wone_census":
                    census["rows"] += 1
                    census["wone_balance_atto"] += parse_uint(
                        row.get("wone_balance_atto"), "wone_balance_atto", line
                    )
                    continue
                if source_kind not in LEDGER_SOURCES:
                    raise SnapshotError(f"line {line}: unknown row_source {source_kind!r}")

                addr = (row.get("eth_address") or "").strip().lower()
                validate_address(addr)
                if addr in seen_addresses:
                    raise SnapshotError(f"duplicate ledger address at line {line}: {addr}")
                seen_addresses.add(addr)
                computed = recompute_row(row, line, threshold)
                add_totals(all_rows, computed)
                exact_threshold_rows += int(computed["exact_threshold"])
                for field in non_issuing:
                    non_issuing[field] += computed["_deductions"][field]

                if computed["qualifies"]:
                    add_totals(qualifying, computed)
                if computed["is_exchange"]:
                    add_totals(exchange, computed)
                elif computed["migration_stage"] == "initial":
                    add_totals(initial, computed)
                elif computed["migration_stage"] == "deferred":
                    add_totals(deferred, computed)
                elif computed["migration_stage"] == "next_stage":
                    add_totals(next_stage, computed)

                if check:
                    problems = column_mismatches(row, computed, line, threshold)
                    mismatch_count += len(problems)
                    mismatches.extend(problems[:max(0, max_mismatches - len(mismatches))])

                if writer is not None:
                    writer.writerow({field: _render(field, computed[field]) for field in per_address_fields()})
    result = {
        "threshold_atto": str(threshold),
        "threshold_one": threshold // ATTO_PER_ONE,
        "ledger_rows": all_rows["rows"],
        "qualifying_rows": qualifying["rows"],
        "exact_threshold_rows": exact_threshold_rows,
        "wone_census_only": {"rows": census["rows"], "wone_balance_atto": str(census["wone_balance_atto"])},
        "gross_claim": _string_totals(all_rows),
        "qualifying_claim": _string_totals(qualifying),
        "delivered_by_stage": {
            "initial": _string_totals(initial),
            "deferred": _string_totals(deferred),
            "next_stage": _string_totals(next_stage),
            "exchange_manual": _string_totals(exchange),
        },
        "non_issuing_atto": {field: str(value) for field, value in non_issuing.items()},
    }
    if check:
        result["column_check"] = {
            "mismatches": mismatch_count,
            "truncated": mismatch_count > len(mismatches),
            "examples": mismatches[:max_mismatches],
        }
    return result


def _render(field, value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _string_totals(bucket):
    out = {}
    for field, value in bucket.items():
        out[field] = value if field == "rows" else str(value)
        if field.endswith("_atto"):
            out[field[:-5] + "_one"] = one_str(value)
    return out


def print_summary(result):
    print("Independent recompute from " + (
        "raw database evidence (selected accounts)" if "raw_evidence" in result
        else "the supplied cutoff snapshot CSV"))
    print(f"threshold        : {result['threshold_atto']} atto ({result['threshold_one']} ONE, inclusive)")
    print(f"ledger rows      : {result['ledger_rows']}")
    print(f"qualifying rows  : {result['qualifying_rows']} (exactly at threshold: {result['exact_threshold_rows']})")
    census = result["wone_census_only"]
    print(f"WONE census only : {census['rows']} rows, {one_str(int(census['wone_balance_atto']))} WONE")
    gross = result["qualifying_claim"]
    print("\nQualifying claim (gross, before deductions):")
    print(f"  wallet airdrop : {gross['wallet_airdrop_one']} ONE")
    print(f"  staked to vault: {gross['staked_one']} ONE")
    print(f"  total claim    : {gross['total_claim_one']} ONE")
    print("\nDelivered by stage (net of reviewed deductions):")
    for stage, totals in result["delivered_by_stage"].items():
        label = "manual from reserve" if stage == "exchange_manual" else "migration allocation"
        amount = totals["total_claim_one"] if stage == "exchange_manual" else totals["migration_allocation_one"]
        print(f"  {stage:<16}: {totals['rows']} rows, {amount} ONE {label}")
    if "column_check" in result:
        check = result["column_check"]
        state = "OK" if check["mismatches"] == 0 else f"{check['mismatches']} MISMATCH(ES)"
        print(f"\ncolumn check     : {state}")
        for example in check["examples"][:20]:
            print(f"  - {example}")


def main(argv=None):
    args = parse_args(argv)
    threshold = args.minimum_one * ATTO_PER_ONE
    try:
        for path in (args.output, args.per_address, args.claims_output):
            if path is not None and path.exists() and not args.replace:
                raise SnapshotError(f"{path} exists; pass --replace")
        rows = provenance = comparison = claims_rows = None
        if args.raw_state0:
            # Import only this independent evidence assembler, not pipeline code.
            import independent_raw
            rows, provenance = independent_raw.assemble(
                args.manifest, (args.raw_state0, args.raw_state1),
                (args.raw_receipts0, args.raw_receipts1), args.policy)
            for line, row in enumerate(rows, 2):
                c = recompute_row(row, line, threshold)
                requested = sum(c["_deductions"][f] for f in (
                    "incident_deduction_atto", "burn_or_inaccessible_atto",
                    "reviewed_contract_non_issuance_atto", "wone_reserve_atto"))
                if requested > c["total_claim_atto"]:
                    raise SnapshotError("reviewed deductions exceed independently measured claim")
                if row["issuance_treatment"] == "not_issued" and c["migration_allocation_atto"]:
                    raise SnapshotError("policy says not_issued but deductions leave an allocation")
                if row["issuance_treatment"] == "issue" and not c["migration_allocation_atto"]:
                    raise SnapshotError("policy says issue but independent allocation is zero")
            manifest, _ = independent_raw.load(args.manifest)
            if args.claims_output:
                import migration_format
                claims_rows, omitted = migration_format.rows_for_claims(
                    rows, recompute_row, threshold, manifest, args.claims_format)
            if args.compare:
                comparison = independent_raw.compare(
                    args.compare, rows, recompute_row, threshold, args.max_mismatches, manifest=manifest)
        result = recompute(
            args.snapshot, threshold, args.check, args.max_mismatches,
            per_address_out=args.per_address, replace=args.replace, input_rows=rows,
        )
        if provenance is not None:
            result["raw_evidence"] = provenance
        if comparison is not None:
            result["team_comparison"] = comparison
        if claims_rows is not None:
            migration_format.write_claims(args.claims_output, claims_rows, args.claims_format, args.replace)
            result["claims_export"] = {
                "format": args.claims_format, "rows": len(claims_rows),
                "path": str(args.claims_output), "sha256": independent_raw.file_hash(args.claims_output),
                "selected_accounts_without_claim_rows": omitted,
                "schema": migration_format.SCHEMA["schema"],
                "reference_commit": migration_format.SCHEMA["reference_commit"],
                "scope": "selected accounts; gross claims, before reviewed deductions"}

        if args.output is not None:
            with atomic_text(args.output, args.replace) as output:
                output.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    except (ValueError, OSError, KeyError, TypeError, csv.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print_summary(result)
    if comparison is not None:
        print(f"\nteam comparison  : {comparison['mismatches']} mismatches, "
              f"{len(comparison['missing_addresses'])} missing selected addresses, "
              f"{len(comparison.get('unexpected_selected_addresses', []))} unexpected selected rows")
    if args.per_address is not None:
        print(f"\nper-address      : {args.per_address}")
    if args.claims_output is not None:
        print(f"claims CSV       : {args.claims_output} ({args.claims_format})")
    if args.output is not None:
        print(f"totals           : {args.output}")
    if args.check and result["column_check"]["mismatches"]:
        return 1
    if comparison and (comparison["mismatches"] or comparison["missing_addresses"] or comparison.get("unexpected_selected_addresses")):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
