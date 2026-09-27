"""Strict CSV reading and atomic publication of completed evidence files."""
import csv
import os
from contextlib import contextmanager
from pathlib import Path


def strict_csv(source):
    reader = csv.DictReader(source)
    headers = reader.fieldnames or []
    if not headers or any(not h for h in headers) or len(headers) != len(set(headers)):
        raise ValueError('CSV requires nonempty, unique column names')

    def rows():
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'CSV row width differs from header at line {reader.line_num}')
            yield row

    return headers, rows()


@contextmanager
def atomic_text(path, replace=False):
    """Write privately and publish only after a successful flush and fsync."""
    path = Path(path)
    partial = path.with_name(path.name + '.partial')
    if os.path.lexists(path) and not replace:
        raise FileExistsError(path)
    created = False
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as output:
            yield output
            output.flush()
            os.fsync(output.fileno())
        if replace:
            os.replace(partial, path)
        else:
            os.link(partial, path)
    finally:
        if created:
            partial.unlink(missing_ok=True)
