import csv
from datetime import date, timedelta
from functools import wraps
import io
import secrets
import sqlite3
import time

from flask import Flask, abort, flash, g, redirect, render_template, request, Response, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from . import service as s


def create_app(database, secret, secure=False):
    if len(secret) < 32:
        raise ValueError('Use a session secret of at least 32 characters.')
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secret, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=secure, PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=256 * 1024)
    failures = {}

    @app.before_request
    def prepare():
        g.db = s.connect(database)
        g.user = g.db.execute('SELECT id,username,role FROM users WHERE id=?', (session.get('user'),)).fetchone()
        session.setdefault('csrf', secrets.token_urlsafe(32))
        if request.method == 'POST' and not secrets.compare_digest(session['csrf'], request.form.get('csrf', '')):
            abort(400, 'The form expired. Reload the page.')
        if request.endpoint not in ('login', 'static', 'health') and not g.user:
            return redirect(url_for('login'))

    @app.teardown_appcontext
    def close(error):
        if 'db' in g:
            g.db.close()

    @app.after_request
    def headers(response):
        response.headers.update({'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin', 'Cache-Control': 'no-store', 'Content-Security-Policy': "default-src 'self'; style-src 'self'; img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
        return response

    @app.context_processor
    def context():
        currency = g.db.execute('SELECT currency FROM settings').fetchone()[0]
        return dict(user=g.user, csrf=session.get('csrf'), today=date.today().isoformat(), hours=s.hours, money=s.amount, qty=s.quantity, line_cost=s.line_cost, categories=s.CATEGORIES, trades=s.TRADES, currency=currency, key=secrets.token_urlsafe(24), can_cost=bool(g.user and g.user['role'] == 'admin'))

    def role(*roles):
        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                if g.user['role'] not in roles:
                    abort(403)
                return fn(*args, **kwargs)
            return wrapper
        return decorator

    @app.errorhandler(s.ValidationError)
    def invalid(error):
        flash(str(error), 'error')
        return redirect(url_for('dashboard'))

    @app.errorhandler(sqlite3.IntegrityError)
    def integrity(error):
        flash('This record conflicts with an existing code or record. Check the selected values.', 'error')
        return redirect(url_for('dashboard'))

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            now = time.monotonic()
            key = request.remote_addr or 'local'
            attempts = [stamp for stamp in failures.get(key, []) if now - stamp < 600]
            if len(attempts) >= 10:
                abort(429, 'Too many attempts. Wait ten minutes.')
            row = g.db.execute('SELECT * FROM users WHERE username=?', (request.form.get('username', '').strip(),)).fetchone()
            if row and check_password_hash(row['password'], request.form.get('password', '')):
                session.clear()
                session.update(user=row['id'], csrf=secrets.token_urlsafe(32))
                session.permanent = True
                failures.pop(key, None)
                return redirect(url_for('dashboard'))
            failures[key] = attempts + [now]
            flash('Username or password is incorrect.', 'error')
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    def filters():
        start = request.args.get('start') or date.today().replace(day=1).isoformat()
        end = request.args.get('end') or date.today().isoformat()
        project = request.args.get('project', type=int)
        return start, end, project

    @app.get('/')
    def dashboard():
        start, end, project = filters()
        report = s.report(g.db, start, end, project)
        performance = s.performance(g.db, project)
        pending = g.db.execute("SELECT s.*,a.name AS activity_name,p.code AS project_code,u.username FROM sheets s JOIN activities a ON a.id=s.activity JOIN projects p ON p.id=a.project JOIN users u ON u.id=s.creator WHERE s.status IN ('draft','submitted')" + (' AND a.project=?' if project else '') + ' ORDER BY s.work_date DESC,s.id DESC LIMIT 12', (project,) if project else ()).fetchall()
        return render_template('dashboard.html', report=report, performance=performance, projects=g.db.execute('SELECT * FROM projects ORDER BY name').fetchall(), pending=pending, start=start, end=end, selected_project=project)

    @app.route('/personnel', methods=['GET', 'POST'])
    @role('admin')
    def personnel():
        if request.method == 'POST':
            f = request.form
            if f.get('action') == 'update':
                s.update_worker(g.db, g.user['id'], f.get('worker'), f.get('rate'), 1 if f.get('active') else 0)
            elif f.get('action') == 'crew':
                s.add_crew(g.db, g.user['id'], f.get('name'), f.get('contractor'))
            else:
                s.add_worker(g.db, g.user['id'], f.get('code'), f.get('name'), f.get('category'), f.get('trade'), f.get('crew'), f.get('rate'), f.get('overtime'), f.get('capacity'))
            flash('Personnel register updated.', 'success')
            return redirect(url_for('personnel'))
        return render_template('personnel.html', workers=g.db.execute('SELECT w.*,c.name AS crew_name,c.contractor FROM workers w JOIN crews c ON c.id=w.crew ORDER BY w.active DESC,w.name').fetchall(), crews=g.db.execute('SELECT * FROM crews ORDER BY name').fetchall())

    @app.route('/work', methods=['GET', 'POST'])
    @role('admin', 'supervisor')
    def work():
        if request.method == 'POST':
            f = request.form
            if f.get('action') == 'project':
                s.add_project(g.db, g.user['id'], f.get('code'), f.get('name'), f.get('location'))
            else:
                s.add_activity(g.db, g.user['id'], f.get('project'), f.get('code'), f.get('name'), f.get('trade'), f.get('zone'), f.get('kind'), f.get('unit'), f.get('target', '0'), f.get('budget'))
            flash('Work breakdown updated.', 'success')
            return redirect(url_for('work'))
        return render_template('work.html', projects=g.db.execute('SELECT * FROM projects ORDER BY name').fetchall(), activities=s.performance(g.db))

    @app.route('/sheets/new', methods=['GET', 'POST'])
    @role('admin', 'supervisor')
    def new_sheet():
        if request.method == 'POST':
            f = request.form
            workers, regular, overtime = f.getlist('worker'), f.getlist('regular'), f.getlist('overtime')
            if len(workers) != len(regular) or len(workers) != len(overtime):
                raise s.ValidationError('The personnel lines are incomplete.')
            lines = [(w, r or '0', o or '0') for w, r, o in zip(workers, regular, overtime) if w or (r and r != '0') or (o and o != '0')]
            if f.get('attendance_csv', '').strip():
                if lines:
                    raise s.ValidationError('Use either individual attendance rows or the CSV table, not both.')
                lines = s.parse_attendance(g.db, f['attendance_csv'])
            sheet = s.create_sheet(g.db, g.user['id'], f.get('activity'), f.get('date'), f.get('output', '0'), f.get('note'), lines, f.get('request_key'))
            return redirect(url_for('sheet_detail', sheet=sheet))
        return render_template('new_sheet.html', activities=g.db.execute('SELECT a.*,p.code AS project_code FROM activities a JOIN projects p ON p.id=a.project ORDER BY p.code,a.code').fetchall(), workers=g.db.execute('SELECT w.*,c.name AS crew_name FROM workers w JOIN crews c ON c.id=w.crew WHERE active=1 ORDER BY c.name,w.name').fetchall())

    @app.get('/sheets')
    def sheets():
        rows = g.db.execute('SELECT s.*,a.name AS activity_name,a.code AS activity_code,p.code AS project_code,u.username,(SELECT SUM(regular_minutes+overtime_minutes) FROM time_lines WHERE sheet=s.id) AS minutes FROM sheets s JOIN activities a ON a.id=s.activity JOIN projects p ON p.id=a.project JOIN users u ON u.id=s.creator ORDER BY s.work_date DESC,s.id DESC LIMIT 200').fetchall()
        return render_template('sheets.html', sheets=rows)

    @app.get('/sheets/<int:sheet>')
    def sheet_detail(sheet):
        row = g.db.execute('SELECT s.*,a.name AS activity_name,a.unit,a.kind,p.name AS project_name,u.username FROM sheets s JOIN activities a ON a.id=s.activity JOIN projects p ON p.id=a.project JOIN users u ON u.id=s.creator WHERE s.id=?', (sheet,)).fetchone()
        if not row:
            abort(404)
        lines = [dict(line) for line in g.db.execute('SELECT t.*,w.name AS worker_name,w.code AS worker_code FROM time_lines t JOIN workers w ON w.id=t.worker WHERE t.sheet=? ORDER BY w.name', (sheet,))]
        for line in lines:
            line['cost'] = s.line_cost(line)
        return render_template('sheet.html', sheet=row, lines=lines, total_minutes=sum(line['regular_minutes'] + line['overtime_minutes'] for line in lines), total_cost=sum(line['cost'] for line in lines), audit=g.db.execute('SELECT a.*,u.username FROM audit a JOIN users u ON u.id=a.actor WHERE a.sheet=? ORDER BY a.id', (sheet,)).fetchall())

    @app.post('/sheets/<int:sheet>/action')
    @role('admin', 'supervisor')
    def sheet_action(sheet):
        action = request.form.get('action')
        if action == 'submit':
            s.submit(g.db, g.user['id'], sheet)
        elif action == 'void':
            s.void(g.db, g.user['id'], sheet, request.form.get('reason'))
        elif action == 'revise':
            s.revise(g.db, g.user['id'], sheet, request.form.get('output'), request.form.get('note'))
        elif action == 'line':
            s.edit_time(g.db, g.user['id'], sheet, request.form.get('worker'), request.form.get('regular'), request.form.get('overtime'))
        else:
            s.review(g.db, g.user['id'], sheet, action, request.form.get('reason'))
        flash('Daily sheet updated.', 'success')
        return redirect(url_for('sheet_detail', sheet=sheet))

    @app.route('/planning', methods=['GET', 'POST'])
    @role('admin', 'supervisor')
    def planning():
        if request.method == 'POST':
            f = request.form
            s.allocate(g.db, g.user['id'], f.get('worker'), f.get('project'), f.get('date'), f.get('hours'))
            flash('Daily allocation saved.', 'success')
            return redirect(url_for('planning', date=f.get('date')))
        selected_date = s.valid_date(request.args.get('date') or date.today().isoformat(), True)
        workers = g.db.execute('SELECT w.*,c.name AS crew_name,COALESCE((SELECT SUM(minutes) FROM allocations WHERE worker=w.id AND work_date=?),0) AS planned,COALESCE((SELECT SUM(t.regular_minutes+t.overtime_minutes) FROM time_lines t JOIN sheets sh ON sh.id=t.sheet WHERE t.worker=w.id AND sh.work_date=? AND sh.status=\'approved\'),0) AS actual FROM workers w JOIN crews c ON c.id=w.crew WHERE w.active=1 ORDER BY c.name,w.name', (selected_date, selected_date)).fetchall()
        return render_template('planning.html', workers=workers, projects=g.db.execute('SELECT * FROM projects ORDER BY name').fetchall(), allocations=g.db.execute('SELECT a.*,w.name AS worker_name,p.name AS project_name FROM allocations a JOIN workers w ON w.id=a.worker JOIN projects p ON p.id=a.project WHERE a.work_date=? ORDER BY w.name,p.name', (selected_date,)).fetchall(), selected_date=selected_date)

    @app.get('/reports')
    def reports():
        start, end, project = filters()
        return render_template('reports.html', report=s.report(g.db, start, end, project), projects=g.db.execute('SELECT * FROM projects ORDER BY name').fetchall(), start=start, end=end, selected_project=project)

    @app.get('/reports.csv')
    def csv_report():
        start, end, project = filters()
        data = s.report(g.db, start, end, project)
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        financial = g.user['role'] == 'admin'
        writer.writerow(['Date', 'Project', 'Activity', 'Personnel code', 'Name', 'Category', 'Trade', 'Crew', 'Contractor', 'Regular hours', 'Overtime hours'] + (['Labor cost', 'Currency'] if financial else []))
        currency = g.db.execute('SELECT currency FROM settings').fetchone()[0]
        for line in data['lines']:
            values = [line['work_date'], line['project_name'], line['activity_name'], line['worker_code'], line['worker_name'], s.CATEGORIES[line['category']], line['trade'], line['crew_name'], line['contractor'], s.hours(line['regular_minutes']).replace(',', ''), s.hours(line['overtime_minutes']).replace(',', '')]
            if financial:
                values.extend([s.amount(s.line_cost(line)).replace(',', ''), currency])
            writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value for value in values])
        return Response('\ufeff' + stream.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=crewmetric-labor.csv'})

    @app.route('/access', methods=['GET', 'POST'])
    @role('admin')
    def access():
        if request.method == 'POST':
            f = request.form
            password, selected_role = f.get('password', ''), f.get('role')
            if not 12 <= len(password) <= 200 or selected_role not in ('admin', 'supervisor', 'viewer'):
                raise s.ValidationError('Use a password of 12–200 characters and a valid role.')
            with s.transaction(g.db):
                g.db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)', (s.text(f.get('username'), 'Username', 80), generate_password_hash(password), selected_role))
                s.audit(g.db, g.user['id'], 'user.created')
            return redirect(url_for('access'))
        return render_template('access.html', users=g.db.execute('SELECT username,role FROM users ORDER BY username').fetchall(), audit=g.db.execute('SELECT a.*,u.username FROM audit a JOIN users u ON u.id=a.actor ORDER BY a.id DESC LIMIT 100').fetchall())

    return app
