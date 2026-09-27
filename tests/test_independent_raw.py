import copy
import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('independent_raw', SCRIPTS / 'independent_raw.py')
RAW = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RAW)
spec = importlib.util.spec_from_file_location('recompute', SCRIPTS / 'independent-recompute.py')
RC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RC)
A = '0x' + '01' * 20
ATTO = 10**18


class RawEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = ROOT / 'manifests/snapshot-2026-09-10.json'
        manifest = json.loads(self.manifest.read_text())
        self.mh = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        self.paths = {}
        self.docs = {}
        for mode in ('state', 'receipts'):
            for shard in (0, 1):
                cutoff = manifest['cutoff'][f'shard{shard}']
                d = {'schema': 'harmony-independent-raw/v1', 'mode': mode,
                     'complete': True, 'shard': shard, 'manifest_sha256': self.mh,
                     'cutoff': {k: cutoff[k] for k in ('block', 'hash', 'state_root')},
                     'accounts': {A: {}}}
                if mode == 'state':
                    d['accounts'][A][f'liquid_shard{shard}_atto'] = str((700 if shard == 0 else 200) * ATTO)
                    if shard == 0:
                        d['accounts'][A].update(self_stake_atto='0', delegated_atto=str(100*ATTO),
                            pending_undelegation_atto=str(20*ATTO), unclaimed_reward_atto=str(3*ATTO),
                            wone_balance_raw_atto=str(5*ATTO))
                key = f'{mode}{shard}'
                self.paths[key] = self.root / f'{key}.json'
                self.docs[key] = d
        policy = {'schema': 'harmony-independent-policy/v1', 'rules': 'independent-recompute/v1',
                  'manifest_sha256': self.mh, 'review_reference': 'synthetic test only',
                  'accounts': {A: dict.fromkeys(RAW.DEDUCTIONS, '0')}}
        policy['accounts'][A].update(wone_excluded=False, exchange='', migration_stage='initial',
                                   issuance_treatment='issue', incident_deduction_atto=str(10*ATTO))
        self.paths['policy'] = self.root / 'policy.json'
        self.docs['policy'] = policy
        self.save()

    def save(self):
        for k, d in self.docs.items():
            self.paths[k].write_text(json.dumps(d))

    def assemble(self):
        self.save()
        return RAW.assemble(self.manifest, [self.paths[f'state{i}'] for i in (0, 1)],
                            [self.paths[f'receipts{i}'] for i in (0, 1)], self.paths['policy'])

    def receipt(self, tx='03', amount='11', dest=1):
        return {'tx_hash': '0x'+tx*32, 'source_shard': 0, 'destination_shard': dest,
                'source_block': 100, 'source_hash': '0x'+'04'*32,
                'to': A, 'from': '0x'+'02'*20, 'amount_atto': amount}

    def test_components_and_wallet_first_deduction(self):
        rows, provenance = self.assemble()
        c = RC.recompute_row(rows[0], 2, 1000*ATTO)
        self.assertEqual(c['total_claim_atto'], 1028*ATTO)
        self.assertEqual(c['final_wallet_atto'], 918*ATTO)
        self.assertEqual(c['final_vault_atto'], 100*ATTO)
        self.assertEqual(provenance['manifest_sha256'], self.mh)

    def test_pending_spent_and_retired_receipts(self):
        pending = self.receipt()
        spent = self.receipt('05', '20')
        retired = self.receipt('06', '30', 2)
        self.docs['receipts0']['outgoing'] = [pending, spent, retired]
        self.docs['receipts1']['incoming'] = [dict(spent, received_block=200)]
        rows, provenance = self.assemble()
        self.assertEqual(rows[0]['pending_cross_shard_atto'], '11')
        self.assertEqual(len(provenance['retired_destination_receipts_excluded']), 1)

    def test_unmatched_or_duplicate_receipts_fail(self):
        r = self.receipt()
        self.docs['receipts1']['incoming'] = [dict(r, received_block=200)]
        with self.assertRaisesRegex(ValueError, 'no matching'):
            self.assemble()
        self.docs['receipts0']['outgoing'] = [r, r]
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.assemble()

    def test_wrong_cutoff_partial_missing_component_or_policy_fail(self):
        original = copy.deepcopy(self.docs)
        mutations = [lambda: self.docs['state0'].update(complete=False),
                     lambda: self.docs['state1']['cutoff'].update(block=0),
                     lambda: self.docs['receipts0'].update(mode='probe'),
                     lambda: self.docs['policy']['accounts'][A].pop('wone_excluded'),
                     lambda: self.docs['state0']['accounts'][A].pop('delegated_atto')]
        for mutation in mutations:
            self.docs = copy.deepcopy(original)
            mutation()
            with self.assertRaises((ValueError, KeyError)):
                self.assemble()

    def test_wone_exclusion_explicit(self):
        self.docs['policy']['accounts'][A]['wone_excluded'] = True
        rows, _ = self.assemble()
        self.assertEqual(rows[0]['wone_balance_atto'], '0')
        self.assertEqual(self.docs['state0']['accounts'][A]['wone_balance_raw_atto'], str(5*ATTO))

    def test_exact_comparison_and_missing_team_rows(self):
        rows, _ = self.assemble()
        path = self.root / 'team.csv'
        headers = ['eth_address', *RAW.COMPONENTS, 'native_total_atto']
        with path.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            row = {k: rows[0][k] for k in headers if k != 'native_total_atto'}
            row['native_total_atto'] = str(1023*ATTO)
            row['liquid_shard0_atto'] = str(700*ATTO+1)
            w.writerow(row)
        result = RAW.compare(path, rows, RC.recompute_row, 1000*ATTO, 1)
        self.assertEqual(result['mismatches'], 1)
        self.assertEqual(result['examples'][0]['field'], 'liquid_shard0_atto')
        path.write_text(','.join(headers)+'\n')
        result = RAW.compare(path, rows, RC.recompute_row, 1000*ATTO, 1)
        self.assertEqual(result['missing_addresses'], [A])

    def test_cli_raw_end_to_end(self):
        output = self.root / 'result.json'
        per = self.root / 'per.csv'
        args = [sys.executable, str(SCRIPTS/'independent-recompute.py'), '--manifest', str(self.manifest),
                '--policy', str(self.paths['policy']), '--output', str(output), '--per-address', str(per)]
        for name in ('state0', 'state1', 'receipts0', 'receipts1'):
            args += ['--raw-'+name, str(self.paths[name])]
        run = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(output.read_text())
        self.assertEqual(result['ledger_rows'], 1)
        self.assertEqual(result['gross_claim']['total_claim_atto'], str(1028*ATTO))
        with per.open() as f:
            self.assertTrue(set(RAW.COMPONENTS) <= set(csv.DictReader(f).fieldnames))
        self.docs['state0']['complete'] = False
        self.save()
        run = subprocess.run(args + ['--replace'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn('incomplete', run.stderr)

    def test_duplicate_json_and_inexact_amounts_fail(self):
        path = self.root / 'duplicate.json'
        path.write_text('{"a":1,"a":2}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            RAW.load(path)
        for value in ('', '-1', '1.0', '1e18', 123, True, '01'):
            with self.assertRaises(ValueError):
                RAW.uint(value)

    def test_mismatch_count_is_not_limited_by_examples(self):
        rows, _ = self.assemble()
        path = self.root / 'team.csv'
        with path.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['eth_address', *RAW.COMPONENTS])
            w.writeheader()
            w.writerow(dict(eth_address=A, **{field: '1' for field in RAW.COMPONENTS}))
        comparison = RAW.compare(path, rows, RC.recompute_row, 1000*ATTO, 1)
        self.assertEqual(comparison['mismatches'], 8)
        self.assertEqual(len(comparison['examples']), 1)
        self.assertTrue(comparison['truncated'])

    def test_snapshot_check_continues_after_example_limit(self):
        rows, _ = self.assemble()
        path = self.root / 'snapshot.csv'
        row = dict(rows[0], **{f: '0' for f in RAW.DERIVED})
        row['qualified_1000_one'] = 'false'
        with path.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=RC.REQUIRED_FIELDS)
            w.writeheader()
            w.writerows([row, dict(row, eth_address='0x'+'02'*20)])
        result = RC.recompute(path, 1000*ATTO, True, 1)
        self.assertEqual(result['column_check']['mismatches'], 10)
        self.assertEqual(len(result['column_check']['examples']), 1)


if __name__ == '__main__':
    unittest.main()
