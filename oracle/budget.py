"""Atomic spend reservations for the single user-authorized $10 AWS trial."""
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .corpus import HarnessError

MICRO = Decimal(1_000_000)


def microdollars(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise HarnessError('Budget amounts must be finite and nonnegative')
    return int((amount * MICRO).to_integral_value(rounding=ROUND_CEILING))


class SpendLedger:
    def __init__(self, path, cap_usd='10'):
        self.path = Path(path)
        self.stage = None
        cap = microdollars(cap_usd)
        if not 0 < cap <= 10_000_000:
            raise HarnessError('This trial cannot authorize more than $10')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS budget(id INTEGER PRIMARY KEY CHECK(id=1),cap_micro INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS charges(id TEXT PRIMARY KEY,category TEXT NOT NULL,
                 reserved_micro INTEGER NOT NULL CHECK(reserved_micro>=0),
                 charged_micro INTEGER CHECK(charged_micro>=0),status TEXT NOT NULL,
                 query_id INTEGER,details TEXT,created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS stage_limits(name TEXT PRIMARY KEY,ceiling_micro INTEGER NOT NULL,
                 allowance_micro INTEGER NOT NULL);''')
            con.execute('BEGIN IMMEDIATE')
            if 'isolated' not in {r[1] for r in con.execute('PRAGMA table_info(stage_limits)')}:
                con.execute('ALTER TABLE stage_limits ADD COLUMN isolated INTEGER NOT NULL DEFAULT 0')
            row = con.execute('SELECT cap_micro FROM budget').fetchone()
            if row is None:
                con.execute('INSERT INTO budget(cap_micro) VALUES(?)', (cap,))
            elif row[0] != cap:
                raise HarnessError('Existing budget cap cannot be changed or reset')
        self.cap = cap

    def use_stage(self, name, additional_usd, *, baseline_before=None, isolated=False):
        """Freeze a separately approved addition once; resumes cannot reset it."""
        allowance = microdollars(additional_usd)
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM stage_limits WHERE name=?', (name,)).fetchone()
            if row is None:
                sql = 'SELECT COALESCE(SUM(COALESCE(charged_micro,reserved_micro)),0) FROM charges'
                baseline = con.execute(sql + (' WHERE created_at<?' if baseline_before is not None else ''),
                                       (baseline_before,) if baseline_before is not None else ()).fetchone()[0]
                ceiling = min(self.cap, baseline + allowance)
                con.execute('INSERT INTO stage_limits(name,ceiling_micro,allowance_micro,isolated) VALUES(?,?,?,?)',
                            (name, ceiling, allowance, int(isolated)))
            elif row['allowance_micro'] != allowance or row['isolated'] != int(isolated):
                raise HarnessError('An approved stage allowance cannot be changed or reset')
        self.stage = name
        return self

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def reserve(self, category, upper_usd, *, query_id=None, details=None):
        reserved = microdollars(upper_usd)
        identity = uuid.uuid4().hex
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute("SELECT 1 FROM charges WHERE status='overrun' LIMIT 1").fetchone():
                raise HarnessError('AWS BUDGET STOP: a previous receipt exceeded its reservation')
            used = con.execute('SELECT COALESCE(SUM(COALESCE(charged_micro,reserved_micro)),0) FROM charges').fetchone()[0]
            ceiling = self.cap
            if self.stage:
                stage = con.execute('SELECT * FROM stage_limits WHERE name=?', (self.stage,)).fetchone()
                if stage['isolated']:
                    stage_used = con.execute('SELECT COALESCE(SUM(COALESCE(charged_micro,reserved_micro)),0) '
                        "FROM charges WHERE json_extract(details,'$.approval_stage')=?", (self.stage,)).fetchone()[0]
                    if stage_used + reserved > stage['allowance_micro']:
                        raise HarnessError('STRICT AWS BUDGET STOP: next operation cannot fit within the approved stage allowance')
                else:
                    ceiling = min(ceiling, stage['ceiling_micro'])
            if used + reserved > ceiling:
                limit = 'approved stage allowance' if self.stage else '$10 cap'
                raise HarnessError('STRICT AWS BUDGET STOP: next operation cannot fit within the ' + limit)
            con.execute('INSERT INTO charges VALUES(?,?,?,?,?,?,?,?)',
                        (identity, category, reserved, None, 'reserved', query_id,
                         json.dumps({**(details or {}), 'approval_stage': self.stage}), time.time()))
        return identity

    def settle(self, identity, cost_usd, *, details=None, estimated=False):
        actual = microdollars(cost_usd)
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM charges WHERE id=?', (identity,)).fetchone()
            if row is None or row['status'] != 'reserved':
                raise HarnessError('Budget reservation is missing or already settled')
            if actual > row['reserved_micro']:
                # Preserve the larger receipt and stop further work; never hide it.
                merged = {**json.loads(row['details']), **(details or {}), 'upper_bound_exceeded': True}
                merged['approval_stage'] = json.loads(row['details']).get('approval_stage')
                con.execute('UPDATE charges SET charged_micro=?,status=?,details=? WHERE id=?',
                            (actual, 'overrun', json.dumps(merged), identity))
            else:
                merged = {**json.loads(row['details']), **(details or {})}
                merged['approval_stage'] = json.loads(row['details']).get('approval_stage')
                con.execute('UPDATE charges SET charged_micro=?,status=?,details=? WHERE id=?',
                            (actual, 'estimated' if estimated else 'calculated', json.dumps(merged), identity))
        if actual > row['reserved_micro']:
            raise HarnessError('AWS BUDGET STOP: receipt exceeded its conservative reservation')

    def summary(self):
        with self.connection() as con:
            rows = [dict(row) for row in con.execute('SELECT * FROM charges ORDER BY created_at,id')]
            stages = [dict(row) for row in con.execute('SELECT * FROM stage_limits ORDER BY name')]
        for row in rows:
            row['details'] = json.loads(row['details'])
        used = sum(r['charged_micro'] if r['charged_micro'] is not None else r['reserved_micro'] for r in rows)
        return {'cap_usd': self.cap / 1e6, 'committed_upper_usd': used / 1e6,
                'remaining_usd': (self.cap - used) / 1e6, 'charges': rows,
                'stage_limits': [{'name': s['name'], 'ceiling_usd': None if s['isolated'] else s['ceiling_micro'] / 1e6,
                                  'allowance_usd': s['allowance_micro'] / 1e6,
                                  'remaining_usd': ((s['allowance_micro'] - sum(
                                      r['charged_micro'] if r['charged_micro'] is not None else r['reserved_micro']
                                      for r in rows if r['details'].get('approval_stage') == s['name']))
                                      if s['isolated'] else s['ceiling_micro'] - used) / 1e6} for s in stages],
                'basis': 'Calculated token charges plus conservative reservations/estimates; not an AWS invoice.'}
