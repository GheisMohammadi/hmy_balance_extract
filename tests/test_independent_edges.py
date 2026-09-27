import argparse
import copy
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from evidence_io import atomic_text, strict_csv
import test_independent_raw as raw_tests
A = raw_tests.A

spec = importlib.util.spec_from_file_location('server_runner', ROOT / 'scripts/run-full-server.py')
RUNNER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RUNNER)


class IOEdges(unittest.TestCase):
    def test_malformed_csv_rejected(self):
        for text in ('a,a\n1,2\n', 'a,b\n1\n', 'a,b\n1,2,3\n', ',b\n1,2\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                headers, rows = strict_csv(io.StringIO(text))
                list(rows)

    def test_failed_write_preserves_existing_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result'
            path.write_text('original')
            with self.assertRaisesRegex(ValueError, 'invalid'):
                with atomic_text(path, replace=True) as output:
                    output.write('unfinished')
                    raise ValueError('invalid')
            self.assertEqual(path.read_text(), 'original')
            self.assertFalse(path.with_name('result.partial').exists())

    def test_atomic_no_replace_race_and_foreign_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result'
            with self.assertRaises(FileExistsError):
                with atomic_text(path) as output:
                    output.write('ours')
                    path.write_text('other')
            self.assertEqual(path.read_text(), 'other')
            path.unlink()
            partial = path.with_name('result.partial')
            partial.write_text('unfinished other writer')
            with self.assertRaises(FileExistsError):
                with atomic_text(path):
                    self.fail('must not open')
            self.assertEqual(partial.read_text(), 'unfinished other writer')


class PolicyEdges(unittest.TestCase):
    def setUp(self):
        self.fixture = raw_tests.RawEvidenceTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_stage_contradictions_rejected(self):
        baseline = copy.deepcopy(self.fixture.docs)
        for updates in ({'migration_stage': ''}, {'migration_stage': 'exchange_manual'},
                        {'exchange': 'synthetic', 'issuance_treatment': 'manual_from_reserve',
                         'migration_stage': 'initial'}):
            self.fixture.docs = copy.deepcopy(baseline)
            self.fixture.docs['policy']['accounts'][A].update(updates)
            with self.assertRaisesRegex(ValueError, 'stage'):
                self.fixture.assemble()

    def test_boolean_shard_and_full_selection_rejected(self):
        baseline = copy.deepcopy(self.fixture.docs)
        for updates in ({'shard': False}, {'all_accounts': True}):
            self.fixture.docs = copy.deepcopy(baseline)
            self.fixture.docs['state0'].update(updates)
            with self.assertRaises(ValueError):
                self.fixture.assemble()

    def test_duplicate_snapshot_and_failed_audit_leave_no_output(self):
        rows, _ = self.fixture.assemble()
        row = dict(rows[0], **{field: '0' for field in raw_tests.RAW.DERIVED})
        row['qualified_1000_one'] = 'false'
        source = self.fixture.root / 'snapshot.csv'
        output = self.fixture.root / 'audit.csv'
        with source.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=raw_tests.RC.REQUIRED_FIELDS)
            writer.writeheader()
            writer.writerows([row, row])
        with self.assertRaisesRegex(ValueError, 'duplicate ledger'):
            raw_tests.RC.recompute(source, 1000 * 10**18, False, 1, per_address_out=output)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_name('audit.csv.partial').exists())

    def test_same_shard_transfer_rejected(self):
        self.fixture.docs['receipts0']['outgoing'] = [self.fixture.receipt(dest=0)]
        with self.assertRaisesRegex(ValueError, 'source shard'):
            self.fixture.assemble()


class RunnerEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'db'
        self.db.mkdir()
        (self.db / 'CURRENT').write_text('MANIFEST-1\n')
        (self.db / 'MANIFEST-1').write_bytes(b'synthetic checkpoint')
        self.stage = {'status': 'consistent_checkpoint', 'manifest': 'MANIFEST-1',
                      'manifest_sha256': hashlib.sha256(b'synthetic checkpoint').hexdigest()}
        self.save_stage()

    def save_stage(self):
        Path(str(self.db) + '.stage.json').write_text(json.dumps(self.stage))

    def test_rejects_unpinned_or_changed_manifest(self):
        self.assertEqual(RUNNER.checkpoint_digest(self.db), self.stage['manifest_sha256'])
        for current in ('../MANIFEST-1', '/tmp/MANIFEST-1', 'MANIFEST-2'):
            (self.db / 'CURRENT').write_text(current)
            with self.assertRaises(ValueError):
                RUNNER.checkpoint_digest(self.db)
        (self.db / 'CURRENT').write_text('MANIFEST-1')
        (self.db / 'MANIFEST-1').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            RUNNER.checkpoint_digest(self.db)

    def test_phase_failure_returns_nonzero_and_stops(self):
        child = Mock(returncode=2)
        child.pid = 123
        child.poll.return_value = 2
        args = argparse.Namespace(db=str(self.db), shard=0, candidate_address_csv=[])
        with patch.object(RUNNER.subprocess, 'Popen', return_value=child) as spawn:
            self.assertEqual(RUNNER.run(args, self.root), 2)
            self.assertEqual(spawn.call_count, 1)
        status = json.loads((self.root / 'run.json').read_text())
        self.assertEqual(status['phases']['state']['status'], 'failed')
        self.assertEqual(status['exit_code'], 2)
        with self.assertRaises(FileExistsError):
            RUNNER.run(args, self.root)

    def test_spawn_failure_recorded(self):
        args = argparse.Namespace(db=str(self.db), shard=1, candidate_address_csv=[])
        with patch.object(RUNNER.subprocess, 'Popen', side_effect=FileNotFoundError('missing tool')):
            self.assertEqual(RUNNER.run(args, self.root), 2)
        status = json.loads((self.root / 'run.json').read_text())
        self.assertEqual(status['phases']['state']['status'], 'failed')
        self.assertIn('missing tool', status['error'])
