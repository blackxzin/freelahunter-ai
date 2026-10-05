#!/usr/bin/env python3
"""Apply additive SQLite migrations and show schema status."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from freelahunter.core import Database

parser = argparse.ArgumentParser()
parser.add_argument('--database', default=None)
args = parser.parse_args()
db = Database(args.database)
rows = db.conn.execute('SELECT version, applied_at FROM schema_migrations ORDER BY version').fetchall()
print(f'Migrações aplicadas: {len(rows)}')
for version, applied_at in rows:
    print(f'- v{version}: {applied_at}')
