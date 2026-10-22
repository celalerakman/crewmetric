import argparse
import getpass
import os
from pathlib import Path
import secrets
import sqlite3

from werkzeug.security import generate_password_hash
from . import service as s
from .web import create_app


def main():
    parser = argparse.ArgumentParser(description='Construction labor and productivity')
    parser.add_argument('command', choices=('init', 'demo', 'serve', 'backup', 'check'))
    parser.add_argument('--database', default='runtime/crewmetric.sqlite')
    parser.add_argument('--username', default='admin')
    parser.add_argument('--currency', default='TRY')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8772)
    parser.add_argument('--output')
    parser.add_argument('--secure-cookies', action='store_true')
    args = parser.parse_args()
    path = Path(args.database).resolve()
    if args.command in ('init', 'demo'):
        path.parent.mkdir(parents=True, exist_ok=True)
        db = s.connect(path)
        s.initialize(db, args.currency)
        if db.execute('SELECT 1 FROM users').fetchone():
            parser.error('Database already initialized. Choose a new database path.')
        password = secrets.token_urlsafe(18) if args.command == 'demo' else getpass.getpass('Administrator password (12 characters minimum): ')
        if len(password) < 12:
            parser.error('Use at least 12 characters.')
        db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)', (args.username, generate_password_hash(password), 'admin'))
        if args.command == 'demo':
            from .seed import seed
            seed(db, password)
            credential = path.with_name(path.stem + '-credential.txt')
            descriptor = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(password)
            print(f'Demo ready. Accounts: {args.username} (administrator), foreman (supervisor). Generated demo password saved in {credential}')
        else:
            print('Workspace ready. Add another administrator for independent sheet approval.')
        db.close()
        return
    if not path.exists():
        parser.error('Database not found. Run init or demo first.')
    if args.command == 'backup':
        if not args.output or Path(args.output).exists():
            parser.error('Choose a new backup path with --output.')
        source, target = s.connect(path), sqlite3.connect(args.output)
        source.backup(target)
        source.close()
        target.close()
        print('Consistent backup created.')
        return
    if args.command == 'check':
        db = s.connect(path)
        valid = db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok' and not db.execute('PRAGMA foreign_key_check').fetchall()
        invalid = db.execute("SELECT t.worker,s.work_date FROM time_lines t JOIN sheets s ON s.id=t.sheet WHERE s.status='approved' GROUP BY t.worker,s.work_date HAVING SUM(t.regular_minutes+t.overtime_minutes)>1440").fetchall()
        db.close()
        if not valid or invalid:
            raise SystemExit('Integrity verification failed.')
        print('Database structure, references and approved daily hour limits verified.')
        return
    secret = os.environ.get('CREWMETRIC_SECRET_KEY')
    if not secret:
        key_path = path.parent / '.crewmetric-key'
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        secret = key_path.read_text().strip()
    from waitress import serve
    print(f'CrewMetric listening on http://{args.host}:{args.port}', flush=True)
    serve(create_app(path, secret, args.secure_cookies), host=args.host, port=args.port)
