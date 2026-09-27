import copy
import csv
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import migration_format as FORMAT
import independent_raw as RAW

spec = importlib.util.spec_from_file_location('format_recompute', ROOT / 'scripts/independent-recompute.py')
RC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RC)
ATTO = 10**18


class ClaimsFormatTest(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((ROOT / 'tests/fixtures/migration-claims-golden.json').read_text())
        self.rows = self.fixture['input_rows']
        self.manifest = json.loads((ROOT / 'manifests/snapshot-2026-09-10.json').read_text())
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def formatted(self, variant='migration'):
        return FORMAT.rows_for_claims(self.rows, RC.recompute_row, 1000*ATTO, self.manifest, variant)

    def test_exact_bytes_match_reference_native_and_wone_formats(self):
        # Synthetic golden CSVs pin reference serialization without importing
        # the reference repository during tests or export.
        for variant, size, count in [('native', 34, 3), ('migration', 44, 4)]:
            with self.subTest(variant=variant):
                rows, omitted = self.formatted(variant)
                path = self.directory / (variant + '.csv')
                FORMAT.write_claims(path, rows, variant)
                self.assertEqual(path.read_bytes(), self.fixture[variant+'_csv'].encode())
                self.assertEqual(len(FORMAT.fieldnames(variant)), size)
                self.assertEqual(len(rows), count)
                self.assertEqual(len(omitted), len(self.rows)-count)
                self.assertEqual([r['secure_key'] for r in rows], sorted(r['secure_key'] for r in rows))
                self.assertTrue(all(r['address_resolved'] == 'true' for r in rows))

    def test_mapping_precision_gross_not_net_and_metadata_blanks(self):
        rows, _ = self.formatted()
        lookup = {r['address'].lower(): r for r in rows}
        a = lookup['0x'+'01'*20]
        self.assertEqual(a['active_staked_or_delegated_atto'], str(100*ATTO))
        self.assertEqual(a['unclaimed_staking_reward_atto'], str(3*ATTO))
        self.assertEqual(a['total_claim_atto'], str(1039*ATTO+1))
        self.assertEqual(a['total_claim_one'], '1039.000000000000000001')
        self.assertEqual(len(a['total_usd'].split('.')[1]), 26)
        c = RC.recompute_row(self.rows[0], 0, 1000*ATTO)
        self.assertEqual(int(a['total_claim_atto'])-c['migration_allocation_atto'], 10*ATTO)
        low = lookup['0x'+'02'*20]
        self.assertEqual(low['wone_airdrop_atto'], '0')
        self.assertEqual(low['nonce_shard1'], '')
        self.assertEqual(low['code_hash_shard1'], '')
        self.assertEqual(lookup['0x'+'03'*20]['nonce_shard0'], '0')
        self.assertEqual(lookup['0x'+'04'*20]['wone_airdrop_atto'], str(2*ATTO))

    def test_missing_metadata_and_duplicate_keys_rejected(self):
        rows = copy.deepcopy(self.rows)
        del rows[0]['_metadata']
        with self.assertRaisesRegex(ValueError, 'regenerate'):
            FORMAT.rows_for_claims(rows, RC.recompute_row, 1000*ATTO, self.manifest)
        rows = copy.deepcopy(self.rows)
        rows[1]['_metadata']['secure_key'] = rows[0]['_metadata']['secure_key']
        with self.assertRaisesRegex(ValueError, 'duplicate extracted'):
            FORMAT.rows_for_claims(rows, RC.recompute_row, 1000*ATTO, self.manifest)
        rows = copy.deepcopy(self.rows)
        rows[0]['_metadata']['account_exists_shard1'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'unknown state'):
            FORMAT.rows_for_claims(rows, RC.recompute_row, 1000*ATTO, self.manifest)

    def test_comparison_accepts_both_reference_formats(self):
        for variant in ('native', 'migration'):
            path = self.directory / (variant+'.csv')
            path.write_text(self.fixture[variant+'_csv'])
            result = RAW.compare(path, self.rows, RC.recompute_row, 1000*ATTO, 10, self.manifest)
            self.assertEqual(result['mismatches'], 0)
            self.assertEqual(result['missing_addresses'], [])
            self.assertEqual(result['unexpected_selected_addresses'], [])
            self.assertEqual(result['format'], 'harmony-migration-'+variant)

    def test_comparison_catches_one_atto_missing_and_duplicate(self):
        rows, _ = self.formatted()
        path = self.directory / 'team.csv'
        rows[0]['total_claim_atto'] = str(int(rows[0]['total_claim_atto'])+1)
        FORMAT.write_claims(path, rows, 'migration')
        result = RAW.compare(path, self.rows, RC.recompute_row, 1000*ATTO, 1, self.manifest)
        self.assertEqual(result['mismatches'], 1)
        self.assertEqual(result['examples'][0]['field'], 'total_claim_atto')
        FORMAT.write_claims(path, rows[1:], 'migration', True)
        result = RAW.compare(path, self.rows, RC.recompute_row, 1000*ATTO, 1, self.manifest)
        self.assertEqual(result['missing_addresses'], [rows[0]['address'].lower()])
        FORMAT.write_claims(path, [rows[0], rows[0]], 'migration', True)
        with self.assertRaisesRegex(ValueError, 'duplicate selected'):
            RAW.compare(path, self.rows, RC.recompute_row, 1000*ATTO, 1, self.manifest)

    def test_comparison_rejects_unexpected_selected_claim_row(self):
        rows, omitted = self.formatted()
        extra = dict(rows[0], address=omitted[0], address_or_secure_key=omitted[0])
        path = self.directory / 'extra.csv'
        FORMAT.write_claims(path, rows + [extra], 'migration')
        result = RAW.compare(path, self.rows, RC.recompute_row, 1000*ATTO, 1, self.manifest)
        self.assertEqual(result['unexpected_selected_addresses'], [omitted[0]])

    def test_cli_output_paths_cannot_overwrite_inputs(self):
        run = subprocess.run([sys.executable, str(ROOT/'scripts/independent-recompute.py'),
                              '--snapshot', str(self.directory/'input.csv'),
                              '--output', str(self.directory/'input.csv'), '--replace'],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn('output files must be distinct', run.stderr)

    def test_csv_refuses_overwrite(self):
        rows, _ = self.formatted()
        path = self.directory / 'claims.csv'
        FORMAT.write_claims(path, rows, 'migration')
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            FORMAT.write_claims(path, [], 'migration')
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(path.with_name(path.name+'.partial').exists())

    def test_full_raw_cli_emits_compatible_claims_and_compares(self):
        manifest_path = ROOT/'manifests/snapshot-2026-09-10.json'
        mh = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        policies = {}
        args = [sys.executable, str(ROOT/'scripts/independent-recompute.py'), '--manifest', str(manifest_path)]
        for mode in ('state', 'receipts'):
            for shard in (0, 1):
                cp = self.manifest['cutoff'][f'shard{shard}']
                report = dict(schema='harmony-independent-raw/v1', mode=mode, complete=True,
                              shard=shard, manifest_sha256=mh,
                              cutoff={k: cp[k] for k in ('block', 'hash', 'state_root')},
                              accounts={r['eth_address']: {} for r in self.rows})
                for row in self.rows:
                    addr = row['eth_address']
                    if mode == 'state':
                        fields = [f'liquid_shard{shard}_atto']
                        if shard == 0:
                            fields += ['self_stake_atto', 'delegated_atto', 'pending_undelegation_atto', 'unclaimed_reward_atto']
                        report['accounts'][addr] = {f: row[f] for f in fields}
                        report['accounts'][addr].update({k:v for k,v in row['_metadata'].items() if k in ('address','secure_key') or k.endswith(f'shard{shard}')})
                        if shard == 0:
                            report['accounts'][addr]['wone_balance_raw_atto'] = row['wone_balance_atto']
                    elif shard == 0 and int(row['pending_cross_shard_atto']):
                        report.setdefault('outgoing', []).append({'tx_hash':'0x'+'ab'*32, 'from':addr, 'to':addr,
                                                 'source_shard':0, 'destination_shard':1, 'source_block':100,
                                                 'source_hash':'0x'+'cd'*32, 'amount_atto':row['pending_cross_shard_atto']})
                path=self.directory/f'{mode}{shard}.json';path.write_text(json.dumps(report))
                args += ['--raw-'+mode+str(shard), str(path)]
        for row in self.rows:
            c=RC.recompute_row(row,0,1000*ATTO)
            policy={f:row[f] for f in RAW.DEDUCTIONS}
            policy.update(wone_excluded=False, exchange=row['exchange'],
                          migration_stage='exchange_manual' if row['exchange'] else ('initial' if c['migration_allocation_atto'] else ''),
                          issuance_treatment='manual_from_reserve' if row['exchange'] else ('issue' if c['migration_allocation_atto'] else 'not_issued'))
            policies[row['eth_address']]=policy
        policy_path=self.directory/'policy.json';policy_path.write_text(json.dumps(dict(schema='harmony-independent-policy/v1',rules='independent-recompute/v1',manifest_sha256=mh,review_reference='synthetic fixture',accounts=policies)))
        team=self.directory/'team.csv';team.write_text(self.fixture['migration_csv'])
        claims=self.directory/'claims.csv';totals=self.directory/'totals.json'
        args += ['--policy',str(policy_path),'--compare',str(team),'--claims-output',str(claims),'--output',str(totals)]
        run=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(claims.read_bytes(),team.read_bytes())
        result=json.loads(totals.read_text())
        self.assertEqual(result['claims_export']['rows'],4)
        self.assertEqual(result['team_comparison']['mismatches'],0)
        self.assertEqual(len(result['claims_export']['selected_accounts_without_claim_rows']),2)
        # Wrong derived value is a comparison failure (exit 1), not a successful
        # format conversion or an input copied into the independent result.
        text=team.read_text().replace('1039000000000000000001','1039000000000000000002')
        team.write_text(text)
        run=subprocess.run(args+['--replace'],capture_output=True,text=True)
        self.assertEqual(run.returncode,1,run.stderr)
        self.assertEqual(claims.read_text(),self.fixture['migration_csv'])


if __name__ == '__main__':
    unittest.main()
