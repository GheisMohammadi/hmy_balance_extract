import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "independent-recompute.py"
ATTO = 10**18


import sys
sys.path.insert(0, str(ROOT / 'scripts'))


def load():
    spec = importlib.util.spec_from_file_location("independent_recompute", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RC = load()

# Column order mirrors the snapshot README. Only the columns the recompute reads
# are populated with meaningful values; the rest are filled to a valid shape.
COLUMNS = (
    "one1_address", "eth_address", "row_source", "is_contract", "is_validator",
    "contract_category", "nonce_shard0", "nonce_shard1",
    "liquid_shard0_atto", "liquid_shard1_atto", "self_stake_atto", "delegated_atto",
    "pending_undelegation_atto", "unclaimed_reward_atto", "pending_cross_shard_atto",
    "native_total_atto", "wone_balance_atto", "total_balance_atto",
    "qualified_1000_one", "exchange", "migration_stage", "issuance_treatment",
    "wone_airdrop_atto", "migration_allocation_atto",
    "special_label", "related_incident", "incident_role", "incident_deduction_atto",
    "incident_deduction_scope", "exploit_distribution_retained_atto",
    "revert_leak_credit_atto", "extra_payout_atto", "wallet_theft_atto",
    "burn_or_inaccessible_atto", "reviewed_contract_non_issuance_atto",
    "wone_not_delivered_atto", "wone_reserve_atto", "non_issuing_atto",
)


def snapshot_row(**overrides):
    row = {field: "" for field in COLUMNS}
    row.update({
        "row_source": "native_ledger", "is_contract": "false", "is_validator": "false",
        "qualified_1000_one": "false", "incident_deduction_scope": "none",
    })
    for field in COLUMNS:
        if field.endswith("_atto") and row[field] == "":
            row[field] = "0"
    row.update(overrides)
    return row


def address(index):
    return "0x" + f"{index:040x}"


def write_snapshot(rows):
    handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    handle.close()
    return Path(handle.name)


class RecomputeArithmeticTest(unittest.TestCase):
    def test_wallet_and_claim_from_components(self):
        row = snapshot_row(
            eth_address=address(1), one1_address="one1a",
            liquid_shard0_atto=str(900 * ATTO), liquid_shard1_atto=str(50 * ATTO),
            self_stake_atto=str(0), delegated_atto=str(100 * ATTO),
            pending_undelegation_atto=str(40 * ATTO), unclaimed_reward_atto=str(3 * ATTO),
            pending_cross_shard_atto=str(20 * ATTO), wone_balance_atto=str(0),
        )
        computed = RC.recompute_row(row, 2, 1000 * ATTO)
        self.assertEqual(computed["native_wallet_atto"], (900 + 50 + 40 + 3 + 20) * ATTO)
        self.assertEqual(computed["staked_atto"], 100 * ATTO)
        self.assertEqual(computed["native_total_atto"], (900 + 50 + 100 + 40 + 3 + 20) * ATTO)
        self.assertEqual(computed["qualification_atto"], 1113 * ATTO)
        self.assertTrue(computed["qualifies"])
        self.assertEqual(computed["wallet_airdrop_atto"], (900 + 50 + 40 + 3 + 20) * ATTO)
        self.assertEqual(computed["total_claim_atto"], 1113 * ATTO)

    def test_threshold_is_inclusive_and_exact(self):
        row = snapshot_row(eth_address=address(2), liquid_shard0_atto=str(1000 * ATTO))
        computed = RC.recompute_row(row, 2, 1000 * ATTO)
        self.assertTrue(computed["qualifies"])
        self.assertTrue(computed["exact_threshold"])
        below = snapshot_row(eth_address=address(3), liquid_shard0_atto=str(1000 * ATTO - 1))
        self.assertFalse(RC.recompute_row(below, 2, 1000 * ATTO)["qualifies"])

    def test_wone_added_only_when_qualifying_or_exchange(self):
        below = snapshot_row(eth_address=address(4), liquid_shard0_atto=str(10 * ATTO),
                             wone_balance_atto=str(5 * ATTO))
        self.assertEqual(RC.recompute_row(below, 2, 1000 * ATTO)["wone_airdrop_atto"], 0)
        qualifying = snapshot_row(eth_address=address(5), liquid_shard0_atto=str(1000 * ATTO),
                                  wone_balance_atto=str(5 * ATTO))
        self.assertEqual(RC.recompute_row(qualifying, 2, 1000 * ATTO)["wone_airdrop_atto"], 5 * ATTO)
        exchange = snapshot_row(eth_address=address(6), liquid_shard0_atto=str(10 * ATTO),
                                wone_balance_atto=str(5 * ATTO), exchange="gate")
        self.assertEqual(RC.recompute_row(exchange, 2, 1000 * ATTO)["wone_airdrop_atto"], 5 * ATTO)

    def test_deduction_taken_from_wallet_before_vault(self):
        row = snapshot_row(
            eth_address=address(7), liquid_shard0_atto=str(1200 * ATTO),
            delegated_atto=str(300 * ATTO), incident_deduction_atto=str(200 * ATTO),
        )
        computed = RC.recompute_row(row, 2, 1000 * ATTO)
        self.assertEqual(computed["final_wallet_atto"], 1000 * ATTO)
        self.assertEqual(computed["final_vault_atto"], 300 * ATTO)
        self.assertEqual(computed["migration_allocation_atto"], 1300 * ATTO)

    def test_deduction_larger_than_wallet_reduces_vault(self):
        row = snapshot_row(
            eth_address=address(8), liquid_shard0_atto=str(100 * ATTO),
            delegated_atto=str(2000 * ATTO), incident_deduction_atto=str(150 * ATTO),
        )
        computed = RC.recompute_row(row, 2, 1000 * ATTO)
        self.assertEqual(computed["final_wallet_atto"], 0)
        self.assertEqual(computed["final_vault_atto"], (100 + 2000 - 150) * ATTO)

    def test_blank_amounts_are_zero(self):
        row = snapshot_row(eth_address=address(9), liquid_shard0_atto="")
        self.assertEqual(RC.recompute_row(row, 2, 1000 * ATTO)["native_wallet_atto"], 0)

    def test_non_integer_amount_rejected(self):
        row = snapshot_row(eth_address=address(10), liquid_shard0_atto="1.5")
        with self.assertRaisesRegex(RC.SnapshotError, "not a non-negative integer"):
            RC.recompute_row(row, 2, 1000 * ATTO)


class RecomputeTotalsTest(unittest.TestCase):
    def build(self):
        rows = [
            snapshot_row(  # qualifying initial wallet
                one1_address="one1init", eth_address=address(11),
                liquid_shard0_atto=str(2000 * ATTO), native_total_atto=str(2000 * ATTO),
                total_balance_atto=str(2000 * ATTO), qualified_1000_one="true",
                migration_stage="initial", issuance_treatment="issue",
                migration_allocation_atto=str(2000 * ATTO),
            ),
            snapshot_row(  # below threshold, no delivery
                one1_address="one1low", eth_address=address(12),
                liquid_shard0_atto=str(10 * ATTO), native_total_atto=str(10 * ATTO),
                total_balance_atto=str(10 * ATTO), qualified_1000_one="false",
            ),
            snapshot_row(  # incident-deducted, still initial
                one1_address="one1inc", eth_address=address(13),
                liquid_shard0_atto=str(1500 * ATTO), native_total_atto=str(1500 * ATTO),
                total_balance_atto=str(1500 * ATTO), qualified_1000_one="true",
                migration_stage="initial", issuance_treatment="issue",
                incident_deduction_atto=str(200 * ATTO), wallet_theft_atto=str(200 * ATTO),
                non_issuing_atto=str(200 * ATTO), migration_allocation_atto=str(1300 * ATTO),
            ),
            snapshot_row(  # exchange, manual delivery
                one1_address="one1exch", eth_address=address(14),
                liquid_shard0_atto=str(5000 * ATTO), delegated_atto=str(200 * ATTO),
                native_total_atto=str(5200 * ATTO), total_balance_atto=str(5200 * ATTO),
                qualified_1000_one="true", exchange="gate",
                migration_stage="exchange_manual", issuance_treatment="manual_from_reserve",
            ),
            snapshot_row(  # WONE census only (dust excluded elsewhere; here a real holder)
                one1_address="one1cen", eth_address=address(15), row_source="wone_census",
                wone_balance_atto=str(50 * ATTO), total_balance_atto=str(50 * ATTO),
            ),
        ]
        return write_snapshot(rows)

    def test_totals(self):
        path = self.build()
        result = RC.recompute(path, 1000 * ATTO, check=True, max_mismatches=50)
        self.assertEqual(result["ledger_rows"], 4)
        self.assertEqual(result["qualifying_rows"], 3)
        self.assertEqual(result["wone_census_only"]["rows"], 1)
        self.assertEqual(result["wone_census_only"]["wone_balance_atto"], str(50 * ATTO))
        initial = result["delivered_by_stage"]["initial"]
        self.assertEqual(initial["rows"], 2)
        self.assertEqual(initial["migration_allocation_atto"], str((2000 + 1300) * ATTO))
        exchange = result["delivered_by_stage"]["exchange_manual"]
        self.assertEqual(exchange["total_claim_atto"], str(5200 * ATTO))
        self.assertEqual(result["column_check"]["mismatches"], 0)

    def test_column_check_flags_a_wrong_allocation(self):
        rows = [snapshot_row(
            one1_address="one1bad", eth_address=address(16),
            liquid_shard0_atto=str(2000 * ATTO), native_total_atto=str(2000 * ATTO),
            total_balance_atto=str(2000 * ATTO), qualified_1000_one="true",
            migration_stage="initial", issuance_treatment="issue",
            migration_allocation_atto=str(1999 * ATTO),  # wrong on purpose
        )]
        path = write_snapshot(rows)
        result = RC.recompute(path, 1000 * ATTO, check=True, max_mismatches=50)
        self.assertEqual(result["column_check"]["mismatches"], 1)
        self.assertIn("migration_allocation_atto", result["column_check"]["examples"][0])

    def test_column_check_flags_wrong_qualification(self):
        rows = [snapshot_row(
            eth_address=address(17), liquid_shard0_atto=str(10 * ATTO),
            native_total_atto=str(10 * ATTO), total_balance_atto=str(10 * ATTO),
            qualified_1000_one="true",  # wrong: below threshold
        )]
        path = write_snapshot(rows)
        result = RC.recompute(path, 1000 * ATTO, check=True, max_mismatches=50)
        self.assertTrue(any("qualified_1000_one" in e for e in result["column_check"]["examples"]))

    def test_cli_outputs(self):
        import subprocess
        import sys
        path = self.build()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "totals.json"
            per = Path(directory) / "by-address.csv"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--snapshot", str(path),
                 "--output", str(out), "--per-address", str(per), "--check"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            totals = json.loads(out.read_text())
            self.assertEqual(totals["ledger_rows"], 4)
            with per.open() as handle:
                recomputed = list(csv.DictReader(handle))
            self.assertEqual(len(recomputed), 4)
            self.assertIn("migration_allocation_atto", recomputed[0])


if __name__ == "__main__":
    unittest.main()
