#!/usr/bin/env python3
"""Run read-only component extraction and preserve phase status on failure."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from evidence_io import atomic_text


def checkpoint_digest(db):
    db = Path(db)
    stage = json.loads(Path(str(db) + '.stage.json').read_text())
    if not isinstance(stage, dict) or stage.get('status') != 'consistent_checkpoint':
        raise ValueError('a consistent checkpoint is required')
    name = (db / 'CURRENT').read_text().strip()
    if not re.fullmatch(r'MANIFEST-[0-9]+', name) or name != stage.get('manifest'):
        raise ValueError('checkpoint manifest name mismatch')
    digest = hashlib.sha256()
    with (db / name).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    value = digest.hexdigest()
    if value != stage.get('manifest_sha256'):
        raise ValueError('checkpoint manifest changed')
    return value


def run(args, root):
    digest = checkpoint_digest(args.db)
    # Exclusive creation prevents concurrent runners from sharing output paths.
    with (root / 'run.lock').open('x') as lock:
        lock.write(str(os.getpid()) + '\n')
    status = {
        'pid': os.getpid(), 'started_unix': time.time(), 'database': args.db,
        'database_read_only': True, 'shard': args.shard,
        'checkpoint_manifest_sha256': digest,
        'complete_migration_balances': False, 'phases': {},
    }

    def save():
        with atomic_text(root / 'run.json', replace=True) as output:
            json.dump(status, output, indent=2)
            output.write('\n')

    base = [str(root / 'bin/independent-db-linux-amd64'), '--db', args.db,
            '--manifest', str(root / 'manifests/snapshot-2026-09-10.json'),
            '--shard', str(args.shard), '--all-accounts', '--allow-global-scan',
            '--timeout', '168h', '--cache-mb', '1024']
    child = None
    result = 2
    try:
        save()
        for phase in ('state', 'receipts'):
            output = root / ('state' if phase == 'state' else 'receipts.json')
            cmd = ['nice', '-n', '19', 'ionice', '-c', '3'] + base + [
                '--mode', phase, '--output', str(output)]
            if phase == 'state':
                for path in args.candidate_address_csv:
                    cmd += ['--candidate-address-csv', path]
            phase_status = {'status': 'starting', 'command': cmd}
            status['phases'][phase] = phase_status
            save()
            with (root / (phase + '.log')).open('x') as log:
                child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=subprocess.STDOUT)
                phase_status.update(pid=child.pid, status='running')
                save()
                while child.poll() is None:
                    status['heartbeat_unix'] = time.time()
                    save()
                    time.sleep(10)
                result = 0 if child.returncode == 0 else 2
                phase_status.update(status='complete' if result == 0 else 'failed',
                                    exit_code=child.returncode)
                save()
            if result:
                break
    except (OSError, ValueError, KeyboardInterrupt) as error:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        result = 2
        status['error'] = str(error)
        for phase_status in status['phases'].values():
            if phase_status['status'] in ('starting', 'running'):
                phase_status['status'] = 'failed'
    finally:
        status.update(finished_unix=time.time(), exit_code=result)
        save()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--shard', type=int, choices=(0, 1), required=True)
    parser.add_argument('--candidate-address-csv', action='append', default=[])
    args = parser.parse_args()
    os.umask(0o077)
    try:
        return run(args, Path(__file__).resolve().parent.parent)
    except (OSError, ValueError) as error:
        parser.exit(2, f'error: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
