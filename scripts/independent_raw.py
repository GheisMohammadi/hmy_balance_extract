"""Strict assembly of independently extracted evidence; no pipeline imports.

Policy is an explicit reviewed input. Team results are never used as components.
Amounts are decimal strings, never floating point JSON numbers.
"""
import csv
import hashlib
import json
import re
from pathlib import Path
from evidence_io import strict_csv

COMPONENTS = (
    "liquid_shard0_atto", "liquid_shard1_atto", "self_stake_atto",
    "delegated_atto", "pending_undelegation_atto", "unclaimed_reward_atto",
    "pending_cross_shard_atto", "wone_balance_atto",
)
DEDUCTIONS = (
    "incident_deduction_atto", "burn_or_inaccessible_atto",
    "reviewed_contract_non_issuance_atto", "wone_not_delivered_atto",
    "wone_reserve_atto", "non_issuing_atto",
)
DERIVED = ("native_total_atto", "total_balance_atto", "wone_airdrop_atto",
           "qualified_1000_one", "migration_allocation_atto")


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ValueError(f"invalid address: {value!r}")
    return value.lower()


def uint(value):
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise ValueError(f"expected exact non-negative decimal string: {value!r}")
    return int(value)


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def load(path):
    raw = Path(path).read_bytes()
    obj = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(obj, dict):
        raise ValueError("evidence and policy JSON must be objects")
    return obj, hashlib.sha256(raw).hexdigest()


def receipt_key(r):
    return (r["source_shard"], r["source_block"], r["source_hash"],
            r["destination_shard"], r["tx_hash"])


def pending_components(reports, selected):
    outgoing, incoming = {}, {}
    unsupported = []
    for shard, report in enumerate(reports):
        for kind, index in (("outgoing", outgoing), ("incoming", incoming)):
            for r in report.get(kind, []):
                if address(r["to"]) not in selected:
                    raise ValueError("receipt for unselected address")
                address(r["from"])
                uint(r["amount_atto"])
                for field in ("tx_hash", "source_hash"):
                    if not re.fullmatch(r"0x[0-9a-f]{64}", r[field]):
                        raise ValueError("invalid receipt hash")
                for field in ("source_shard", "destination_shard", "source_block"):
                    if type(r[field]) is not int or r[field] < 0:
                        raise ValueError("invalid receipt integer")
                if not 0 <= r["source_shard"] < 4 or not 0 <= r["destination_shard"] < 4:
                    raise ValueError("unsupported receipt shard")
                if kind == "outgoing":
                    if r["source_shard"] != shard or r["source_block"] > report["cutoff"]["block"]:
                        raise ValueError("outgoing receipt outside cutoff/shard")
                elif (r["destination_shard"] != shard or
                      type(r.get("received_block")) is not int or
                      not 0 <= r["received_block"] <= report["cutoff"]["block"]):
                    raise ValueError("incoming receipt outside cutoff/shard")
                if r["source_shard"] == r["destination_shard"]:
                    raise ValueError("cross-shard receipt cannot target its source shard")
                key = receipt_key(r)
                if key in index:
                    raise ValueError(f"duplicate {kind} receipt: {key}")
                index[key] = r
    for key, r in incoming.items():
        if r["source_shard"] in (2, 3):
            continue  # already reflected in liquid; retired source not a new claim
        out = outgoing.get(key)
        if out is None or any(out[f] != r[f] for f in ("from", "to", "amount_atto")):
            raise ValueError("incoming receipt has no matching canonical outgoing receipt")
    pending = {a: 0 for a in selected}
    for key, r in outgoing.items():
        if r["destination_shard"] in (2, 3):
            unsupported.append(r)  # policy excludes unprovable retired destinations
        elif key not in incoming:
            pending[address(r["to"])] += uint(r["amount_atto"])
    return pending, unsupported


def assemble(manifest_path, state_paths, receipt_paths, policy_path):
    manifest, manifest_hash = load(manifest_path)
    eligibility = manifest.get("eligibility_1000_one", {})
    if (not isinstance(eligibility, dict) or eligibility.get("threshold_atto") != str(1000 * 10**18) or
            eligibility.get("selected_comparison") != "greater-than-or-equal"):
        raise ValueError("manifest does not specify the supported inclusive 1000 ONE rule")
    if len(state_paths) != 2 or len(receipt_paths) != 2:
        raise ValueError("exactly two state and two receipt reports are required")
    cutoffs = manifest.get("cutoff")
    if not isinstance(cutoffs, dict):
        raise ValueError("manifest requires cutoff objects for both shards")
    for shard in (0, 1):
        cutoff = cutoffs.get(f"shard{shard}")
        if (not isinstance(cutoff, dict) or type(cutoff.get("block")) is not int or
                cutoff["block"] < 0 or any(not isinstance(cutoff.get(field), str) or
                not re.fullmatch(r"0x[0-9a-f]{64}", cutoff[field]) for field in ("hash", "state_root"))):
            raise ValueError("invalid manifest cutoff")
    evidence, provenance = [], {}
    for mode, paths in (("state", state_paths), ("receipts", receipt_paths)):
        group = []
        for shard, path in enumerate(paths):
            report, digest = load(path)
            expected = manifest["cutoff"][f"shard{shard}"]
            cutoff = {k: expected[k] for k in ("block", "hash", "state_root")}
            if (report.get("schema") != "harmony-independent-raw/v1" or
                    report.get("complete") is not True or report.get("mode") != mode or
                    type(report.get("shard")) is not int or report.get("shard") != shard or
                    report.get("all_accounts", False) is not False or report.get("cutoff") != cutoff or
                    report.get("manifest_sha256") != manifest_hash):
                raise ValueError(f"incomplete/wrong cutoff evidence: {path}")
            accounts = report.get("accounts")
            if not isinstance(accounts, dict) or not accounts:
                raise ValueError("empty account selection")
            if any(not isinstance(value, dict) for value in accounts.values()):
                raise ValueError("account evidence must be objects")
            if any(address(a) != a for a in accounts):
                raise ValueError("evidence addresses must be lowercase")
            group.append(report)
            provenance[f"{mode}{shard}"] = {"path": str(path), "sha256": digest}
        evidence.append(group)
    states, receipts = evidence
    selected = set(states[0]["accounts"])
    if any(set(r["accounts"]) != selected for r in states + receipts):
        raise ValueError("evidence account selections differ")
    policy, policy_hash = load(policy_path)
    if (policy.get("schema") != "harmony-independent-policy/v1" or
            policy.get("manifest_sha256") != manifest_hash or
            policy.get("rules") != "independent-recompute/v1" or
            not isinstance(policy.get("review_reference"), str) or
            not policy["review_reference"].strip()):
        raise ValueError("policy needs matching cutoff, explicit rules and review reference")
    if not isinstance(policy.get("accounts"), dict) or set(policy["accounts"]) != selected:
        raise ValueError("policy must explicitly cover every selected account")
    pending, unsupported = pending_components(receipts, selected)
    rows = []
    for addr in sorted(selected):
        p = policy["accounts"][addr]
        if not isinstance(p, dict):
            raise ValueError("account policy must be an object")
        row = {"eth_address": addr, "one1_address": ""}
        metadata0, metadata1 = (s["accounts"][addr] for s in states)
        if "secure_key" in metadata0 and "secure_key" in metadata1:
            if any(metadata0[k] != metadata1[k] for k in ("secure_key", "address")):
                raise ValueError("shard evidence identity metadata differs")
            metadata = {k: metadata0[k] for k in ("secure_key", "address")}
            for shard, source in enumerate((metadata0, metadata1)):
                for prefix in ("nonce", "code_hash", "account_exists"):
                    key = f"{prefix}_shard{shard}"
                    metadata[key] = source[key]
            row["_metadata"] = metadata
        for field in COMPONENTS[:-2]:
            source = states[1] if field == "liquid_shard1_atto" else states[0]
            row[field] = str(uint(source["accounts"][addr][field]))
        row["pending_cross_shard_atto"] = str(pending[addr])
        raw_wone = uint(states[0]["accounts"][addr]["wone_balance_raw_atto"])
        if type(p.get("wone_excluded")) is not bool:
            raise ValueError("policy must explicitly declare wone_excluded")
        if addr == "0xcf664087a5bb0237a0bad6742852ec6c8d69a27a" and not p["wone_excluded"]:
            raise ValueError("WONE self-held tokens must be excluded")
        row["wone_balance_atto"] = "0" if p["wone_excluded"] else str(raw_wone)
        for field in DEDUCTIONS:
            row[field] = str(uint(p[field]))
        for field in ("exchange", "migration_stage", "issuance_treatment"):
            if not isinstance(p.get(field), str):
                raise ValueError(f"policy must explicitly supply {field}")
            row[field] = p[field].strip()
        if row["migration_stage"] not in ("", "initial", "deferred", "next_stage", "exchange_manual"):
            raise ValueError("unknown policy migration stage")
        if row["issuance_treatment"] not in ("issue", "not_issued", "manual_from_reserve"):
            raise ValueError("unknown policy issuance treatment")
        if bool(row["exchange"]) != (row["issuance_treatment"] == "manual_from_reserve"):
            raise ValueError("exchange/manual treatment disagreement")
        stage = row["migration_stage"]
        if row["exchange"] and stage != "exchange_manual":
            raise ValueError("exchange policy requires exchange_manual stage")
        if not row["exchange"] and stage == "exchange_manual":
            raise ValueError("non-exchange policy cannot use exchange_manual stage")
        if row["issuance_treatment"] == "issue" and stage not in ("initial", "deferred", "next_stage"):
            raise ValueError("issued allocation requires an explicit delivery stage")
        native = sum(uint(row[f]) for f in COMPONENTS[:-1])
        row["row_source"] = "native_ledger" if native else "wone_only_ledger"
        rows.append(row)
    provenance["policy"] = {"path": str(policy_path), "sha256": policy_hash,
                            "review_reference": policy["review_reference"]}
    return rows, {"manifest_sha256": manifest_hash, "inputs": provenance,
                  "retired_destination_receipts_excluded": unsupported,
                  "scope": "selected_accounts_only; policy classifications are reviewed inputs"}


def compare(path, rows, compute, threshold, max_examples, manifest=None):
    """Stream team CSV once. Components are mandatory; derived columns optional.

    An absent derived column is reported as not compared, never as a match.
    """
    with Path(path).open(newline="", encoding="utf-8") as source:
        headers = next(csv.reader(source), [])
    if "address" in headers and "active_staked_or_delegated_atto" in headers:
        if manifest is None:
            raise ValueError("canonical claims comparison needs the cutoff manifest")
        import migration_format
        result = migration_format.compare_claims(path, rows, compute, threshold, manifest, max_examples)
        result["team_csv_sha256"] = file_hash(path)
        return result
    selected = {r["eth_address"]: r for r in rows}
    expected = {}
    for addr, r in selected.items():
        c = compute(r, 0, threshold)
        expected[addr] = dict(r, **{k: str(v) for k, v in c.items() if k.endswith("_atto")})
        expected[addr]["qualified_1000_one"] = "true" if c["qualifies"] else "false"
    count, seen, examples = 0, set(), []
    skipped_exchange_allocations = 0
    with Path(path).open(newline="", encoding="utf-8") as source:
        headers, reader = strict_csv(source)
        if len(headers) != len(set(headers)) or not {"eth_address", *COMPONENTS} <= set(headers):
            raise ValueError("team CSV needs unique headers, eth_address and all eight components")
        fields = list(COMPONENTS) + [f for f in DERIVED if f in headers]
        fields += [f for f in ("final_wallet_atto", "final_vault_atto") if f in headers]
        for line, row in enumerate(reader, 2):
            addr = address(row["eth_address"])
            if addr not in selected:
                continue
            if addr in seen:
                raise ValueError(f"duplicate selected team address at line {line}: {addr}")
            seen.add(addr)
            for field in fields:
                if field == "migration_allocation_atto" and selected[addr]["exchange"]:
                    # Exchange delivery is manual; the snapshot allocation
                    # column is not defined for these rows (often blank).
                    skipped_exchange_allocations += 1
                    continue
                actual = row[field]
                if field == "qualified_1000_one":
                    if actual not in ("true", "false"):
                        raise ValueError(f"invalid team qualification flag at line {line}")
                else:
                    actual = str(uint(actual))
                wanted = expected[addr][field]
                if actual != wanted:
                    count += 1
                    if len(examples) < max_examples:
                        examples.append({"eth_address": addr, "field": field,
                                         "independent": wanted, "team": actual})
    missing = sorted(set(selected) - seen)
    return {"mismatches": count, "examples": examples, "truncated": count > len(examples),
            "missing_addresses": missing, "compared_addresses": len(seen),
            "compared_fields": fields,
            "exchange_allocation_rows_not_compared": skipped_exchange_allocations,
            "derived_fields_not_compared": sorted(set(DERIVED) - set(fields)),
            "team_csv_sha256": file_hash(path)}


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()
