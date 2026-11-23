"""Transactional, backed-up upgrade from the legacy CRM to the new CRM."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'crm_v2_1'
MAPPINGS = {
    'customer': ('customers', {'full_name': 'contact_person', 'company': 'company_name', 'emails': 'email', 'tax_info': 'tax_number'}),
    'interaction': ('interactions', {'summary': 'notes', 'staff_name': 'sales_rep', 'created_at': 'interaction_date', 'type': 'result'}),
    'proposal': ('quotes', {'quote_no': 'code', 'footer_note': 'notes', 'created_at': 'created_date'}),
    'task': ('tasks', {'note': 'description'}),
    'user': ('staff', {'name': 'full_name', 'smtp_pass': 'email_pass', 'signature': 'email_signature'}),
    'email_template': ('mail_templates', {'name': 'title'}),
}


def copy_legacy(conn, source, target, mapping):
    source_columns = conn.execute(f'PRAGMA table_info("{source}")').fetchall()
    target_columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{target}")')}
    # Keep every legacy field, including fields absent from the new interface.
    for column in source_columns:
        name = column[1]
        if name not in target_columns:
            conn.execute(f'ALTER TABLE "{target}" ADD COLUMN "{name}" {column[2] or "TEXT"}')
            target_columns.add(name)
    rows = conn.execute(f'SELECT * FROM "{source}"').fetchall()
    if rows and conn.execute(f'SELECT 1 FROM "{target}" LIMIT 1').fetchone():
        raise RuntimeError(f'Both {source} and {target} contain data. Migration requires reconciliation.')
    for row in rows:
        values = dict(row)
        values.update({dest: row[src] for dest, src in mapping.items() if src in row.keys()})
        if source == 'customer':
            values['full_name'] = values.get('full_name') or values.get('company') or ''
            values['tax_info'] = ' / '.join(str(row[k]) for k in ('tax_office', 'tax_number') if k in row.keys() and row[k])
        if source == 'task':
            due = str(values.get('due_date') or '')
            values['due_date'] = due[:10]
            values['due_time'] = due[11:16] or '09:00'
        if source == 'proposal':
            items = json.loads(values.get('items_json') or '[]')
            for item in items:
                item['desc'] = ' — '.join(str(item[k]) for k in ('name', 'desc') if item.get(k))
            values['items_json'] = json.dumps(items, ensure_ascii=False)
            subtotal = row['subtotal'] if 'subtotal' in row.keys() else 0
            tax = row['tax_total'] if 'tax_total' in row.keys() else 0
            values['tax_rate'] = (tax or 0) / subtotal * 100 if subtotal else 0
        columns = ','.join(f'"{key}"' for key in values)
        placeholders = ','.join('?' for _ in values)
        conn.execute(f'INSERT INTO "{target}" ({columns}) VALUES ({placeholders})', list(values.values()))
    if conn.execute(f'SELECT COUNT(*) FROM "{target}"').fetchone()[0] < len(rows):
        raise RuntimeError(f'Row count verification failed: {source}')
    print(f'Migrated {source} -> {target}: {len(rows)} rows')


def main():
    from app import DB_PATH, init_db
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.is_file()
    if not existed and os.getenv('REQUIRE_EXISTING_DB', '1') == '1':
        raise RuntimeError(f'Existing database required: {path}')
    with sqlite3.connect(path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite integrity check failed')
        if existed:
            backups = path.parent / 'backups'
            backups.mkdir(exist_ok=True)
            backup = backups / (path.name + datetime.now(timezone.utc).strftime('.%Y%m%dT%H%M%S%f.bak'))
            with sqlite3.connect(backup) as dest:
                conn.backup(dest)
            print(f'SQLite backup created: {backup}')
        conn.execute('BEGIN IMMEDIATE')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if existed and not ({'customer', 'user'} <= tables or {'customers', 'staff'} <= tables):
            raise RuntimeError('Unrecognized existing database schema')
        conn.execute('CREATE TABLE IF NOT EXISTS crm_migrations (name TEXT PRIMARY KEY)')
        completed = conn.execute('SELECT 1 FROM crm_migrations WHERE name=?', (VERSION,)).fetchone()
        init_db(conn, seed=False)
        if not completed:
            for source, (target, mapping) in MAPPINGS.items():
                if source in tables:
                    copy_legacy(conn, source, target, mapping)
            if 'system_setting' in tables:
                old = {r['key']: r['value'] for r in conn.execute('SELECT key,value FROM system_setting')}
                company = {key: old.get('COMPANY_' + key.upper(), '') for key in ('name', 'address', 'phone', 'email', 'logo')}
                conn.execute("INSERT OR IGNORE INTO settings VALUES ('company', ?)", (json.dumps(company),))
            conn.execute('INSERT INTO crm_migrations VALUES (?)', (VERSION,))
        conn.execute('CREATE TABLE IF NOT EXISTS crm_users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL)')
        if 'user' in tables:
            conn.execute('INSERT OR IGNORE INTO crm_users SELECT id, username, password_hash FROM user')
        if not conn.execute('SELECT 1 FROM crm_users LIMIT 1').fetchone():
            from werkzeug.security import generate_password_hash
            password = os.getenv('ADMIN_INITIAL_PASSWORD', '')
            if len(password) < 12:
                raise RuntimeError('ADMIN_INITIAL_PASSWORD must contain at least 12 characters for a new installation')
            conn.execute('INSERT INTO crm_users (username,password_hash) VALUES (?,?)', ('admin', generate_password_hash(password)))
        init_db(conn, seed=True)
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Post-migration integrity check failed')
        conn.commit()
    print('CRM schema ready. Legacy tables retained.')


if __name__ == '__main__':
    main()
