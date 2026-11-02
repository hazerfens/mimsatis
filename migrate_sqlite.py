#!/usr/bin/env python3
"""Non-destructive SQLite schema upgrade for the Mim Sales app. No rows are deleted."""
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB = Path(os.getenv('DATABASE_PATH', '/app/data/mim_reklam_satis_sistemi.db'))
REQUIRE_EXISTING = os.getenv('REQUIRE_EXISTING_DB', '1') == '1'
CHANGES = {
    'user': {
        'title': "VARCHAR(100) DEFAULT 'Satış Temsilcisi'",
        'email_pass': 'VARCHAR(100)',
        'email_signature': 'TEXT',
        'allowed_menus': "TEXT DEFAULT 'customers,tasks,dashboard,bulk_email'",
    },
    'customer': {'trade_registry_no': 'VARCHAR(100)'},
    'interaction': {'planned_datetime': 'DATETIME'},
}

def main():
    DB.parent.mkdir(parents=True, exist_ok=True)
    if not DB.is_file():
        if REQUIRE_EXISTING:
            raise SystemExit(f'ABORT: Existing SQLite DB not found: {DB}. No empty DB will be created.')
        print('New installation: DB absent; init_system will initialize it.')
        return
    with sqlite3.connect(str(DB), timeout=30) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise SystemExit('ABORT: SQLite integrity check failed')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'user', 'customer', 'interaction'}.issubset(tables):
            raise SystemExit('ABORT: Unexpected schema; manual review required')
        pending=[]
        for table, cols in CHANGES.items():
            existing={r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            pending.extend((table,col,spec) for col,spec in cols.items() if col not in existing)
        if not pending:
            print('Schema up to date; existing DB unchanged.')
            return
        backup_dir=DB.parent/'backups'
        backup_dir.mkdir(exist_ok=True)
        backup_path=backup_dir/(DB.name+'.pre-upgrade-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.bak')
        if backup_path.exists():
            raise SystemExit('ABORT: Backup name collision, retry later')
        with sqlite3.connect(str(backup_path)) as out:
            conn.backup(out)
        print('SQLite backup created:',backup_path)
        try:
            conn.execute('BEGIN IMMEDIATE')
            for table,col,spec in pending:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {spec}')
                print('Added column:',table+'.'+col)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise SystemExit('ABORT: Post-migration integrity check failed')
    print('Schema updated. Data rows preserved.')

if __name__ == '__main__':
    main()
