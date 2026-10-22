from contextlib import contextmanager
import csv
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import sqlite3
import io

TRADES = ('Plastering', 'Painting', 'Electrical', 'Plumbing', 'Floor finishes', 'Reinforcement', 'Formwork', 'Concrete', 'Masonry', 'HVAC', 'Site engineering', 'Safety', 'Administration', 'Logistics', 'Other')
CATEGORIES = {'blue_collar': 'Blue collar', 'white_collar': 'White collar', 'administrative': 'Administrative'}


class ValidationError(ValueError):
    pass


def connect(path):
    db = sqlite3.connect(path, timeout=20, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    return db


def initialize(db, currency='TRY'):
    db.executescript(Path(__file__).with_name('schema.sql').read_text())
    if not db.execute('SELECT 1 FROM settings').fetchone():
        if len(currency) != 3 or not currency.isalpha():
            raise ValidationError('Currency must be a three-letter code.')
        db.execute('INSERT INTO settings VALUES(?)', (currency.upper(),))


@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def text(value, label, limit=200, required=True):
    result = str(value or '').strip()
    if len(result) > limit or (required and not result):
        raise ValidationError(f'{label}: enter between 1 and {limit} characters.')
    return result


def scaled(value, scale, label, maximum=10**10):
    try:
        decimal = Decimal(str(value))
        result = decimal * scale
        if not decimal.is_finite() or result < 0 or result > maximum or result != result.to_integral_value():
            raise ValueError()
        return int(result)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f'{label}: enter a nonnegative value with valid precision.')


def minutes(value):
    if isinstance(value, str) and ':' in value:
        parts = value.strip().split(':')
        if len(parts) != 2 or not all(part.isascii() and part.isdigit() for part in parts):
            raise ValidationError('Use decimal hours or H:MM.')
        hour, minute = map(int, parts)
        if minute >= 60 or hour * 60 + minute > 1440:
            raise ValidationError('Hours must be between 0:00 and 24:00.')
        return hour * 60 + minute
    return scaled(value, 60, 'Hours', 1440)


def parse_attendance(db, source):
    if not source or len(source) > 100000:
        raise ValidationError('Paste a CSV attendance table of at most 100 KB.')
    try:
        reader = csv.DictReader(io.StringIO(source.strip()), strict=True)
        required = ['personnel_code', 'regular_hours', 'overtime_hours']
        if not reader.fieldnames or sorted(reader.fieldnames) != sorted(required):
            raise ValidationError('CSV headers must be personnel_code,regular_hours,overtime_hours.')
        result = []
        for row in reader:
            if len(result) >= 100 or None in row or any(value is None for value in row.values()):
                raise ValidationError('Use at most 100 complete attendance rows.')
            person = db.execute('SELECT id FROM workers WHERE code=? AND active=1', (row['personnel_code'].strip().upper(),)).fetchone()
            if not person:
                raise ValidationError(f"Unknown or inactive personnel code: {row['personnel_code'][:40]}")
            result.append((person['id'], row['regular_hours'], row['overtime_hours']))
        if not result:
            raise ValidationError('The attendance CSV has no personnel rows.')
        return result
    except csv.Error:
        raise ValidationError('The attendance CSV is malformed.')


def hours(value):
    return f'{Decimal(value)/60:,.2f}'


def amount(value):
    return f'{Decimal(value)/100:,.2f}'


def quantity(value):
    return f'{Decimal(value)/1000:,.3f}'.rstrip('0').rstrip('.')


def valid_date(value, future=False):
    try:
        result = date.fromisoformat(str(value))
        if not future and result > date.today():
            raise ValueError()
        return result.isoformat()
    except ValueError:
        raise ValidationError('Choose a valid date' + ('.' if future else ' no later than today.'))


def permission(db, actor, roles):
    row = db.execute('SELECT role FROM users WHERE id=?', (actor,)).fetchone()
    if not row or row['role'] not in roles:
        raise ValidationError('Your account cannot perform this action.')


def audit(db, actor, action, sheet=None, detail=''):
    db.execute('INSERT INTO audit(actor,action,sheet,detail) VALUES(?,?,?,?)', (actor, action, sheet, detail))


def add_project(db, actor, code, name, location):
    with transaction(db):
        permission(db, actor, ('admin',))
        row = db.execute('INSERT INTO projects(code,name,location) VALUES(?,?,?)', (text(code, 'Project code', 40).upper(), text(name, 'Project name'), text(location, 'Location')))
        audit(db, actor, 'project.created', detail=str(row.lastrowid))
        return row.lastrowid


def add_crew(db, actor, name, contractor):
    with transaction(db):
        permission(db, actor, ('admin',))
        row = db.execute('INSERT INTO crews(name,contractor) VALUES(?,?)', (text(name, 'Crew'), text(contractor, 'Contractor')))
        audit(db, actor, 'crew.created', detail=str(row.lastrowid))
        return row.lastrowid


def add_worker(db, actor, code, name, category, trade, crew, rate, overtime, capacity):
    with transaction(db):
        permission(db, actor, ('admin',))
        if category not in CATEGORIES or trade not in TRADES:
            raise ValidationError('Choose a valid personnel category and trade.')
        rate = scaled(rate, 100, 'Hourly cost', 10**8)
        overtime = scaled(overtime, 10000, 'Overtime multiplier', 30000)
        capacity = minutes(capacity)
        if not 10000 <= overtime <= 30000 or capacity < 60:
            raise ValidationError('Overtime multiplier must be 1–3; daily capacity must be 1–24 hours.')
        if not db.execute('SELECT 1 FROM crews WHERE id=?', (crew,)).fetchone():
            raise ValidationError('Choose a valid crew.')
        row = db.execute('INSERT INTO workers(code,name,category,trade,crew,hourly_cents,overtime_bp,daily_minutes) VALUES(?,?,?,?,?,?,?,?)', (text(code, 'Personnel code', 40).upper(), text(name, 'Name'), category, trade, crew, rate, overtime, capacity))
        audit(db, actor, 'worker.created', detail=str(row.lastrowid))
        return row.lastrowid


def update_worker(db, actor, worker, rate, active):
    with transaction(db):
        permission(db, actor, ('admin',))
        if active not in (0, 1):
            raise ValidationError('Invalid personnel status.')
        changed = db.execute('UPDATE workers SET hourly_cents=?,active=? WHERE id=?', (scaled(rate, 100, 'Hourly cost', 10**8), active, worker))
        if not changed.rowcount:
            raise ValidationError('Personnel record not found.')
        audit(db, actor, 'worker.updated', detail=str(worker))


def add_activity(db, actor, project, code, name, trade, zone, kind, unit, target, budget):
    with transaction(db):
        permission(db, actor, ('admin', 'supervisor'))
        if trade not in TRADES or kind not in ('direct', 'indirect'):
            raise ValidationError('Choose a valid trade and work type.')
        if not db.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
            raise ValidationError('Choose a valid project.')
        budget_minutes = scaled(budget, 60, 'Budget hours', 10**9)
        target_milli = scaled(target, 1000, 'Target quantity')
        if budget_minutes <= 0 or (kind == 'direct' and target_milli <= 0):
            raise ValidationError('Set positive budget hours and a positive target for direct work.')
        if kind == 'indirect':
            target_milli = 0
            unit = '—'
        row = db.execute('INSERT INTO activities(project,code,name,trade,zone,kind,unit,target_milli,budget_minutes) VALUES(?,?,?,?,?,?,?,?,?)', (project, text(code, 'Activity code', 40).upper(), text(name, 'Activity'), trade, text(zone, 'Zone'), kind, text(unit, 'Unit', 20), target_milli, budget_minutes))
        audit(db, actor, 'activity.created', detail=str(row.lastrowid))
        return row.lastrowid


def line_cost(line):
    regular = Decimal(line['regular_minutes']) * line['hourly_cents'] / 60
    overtime = Decimal(line['overtime_minutes']) * line['hourly_cents'] * line['overtime_bp'] / 600000
    return int((regular + overtime).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def create_sheet(db, actor, activity, work_date, output, note, lines, request_key):
    with transaction(db):
        permission(db, actor, ('admin', 'supervisor'))
        key = text(request_key, 'Request key', 100)
        existing = db.execute('SELECT id FROM sheets WHERE request_key=?', (key,)).fetchone()
        if existing:
            return existing[0]
        activity_row = db.execute('SELECT * FROM activities WHERE id=?', (activity,)).fetchone()
        if not activity_row:
            raise ValidationError('Choose a valid work activity.')
        work_date = valid_date(work_date)
        output = scaled(output, 1000, 'Completed quantity')
        if activity_row['kind'] == 'indirect' and output:
            raise ValidationError('Indirect activities record hours without production quantities.')
        if not 1 <= len(lines) <= 100:
            raise ValidationError('Add between 1 and 100 personnel lines.')
        prepared = []
        seen = set()
        for worker, regular, overtime in lines:
            record = db.execute('SELECT w.*,c.name AS crew_name,c.contractor FROM workers w JOIN crews c ON c.id=w.crew WHERE w.id=? AND w.active=1', (worker,)).fetchone()
            if not record or record['id'] in seen:
                raise ValidationError('Choose active, distinct personnel for each line.')
            seen.add(record['id'])
            regular, overtime = minutes(regular), minutes(overtime)
            if not 0 < regular + overtime <= 1440:
                raise ValidationError('Each personnel line must contain 1 minute to 24 hours.')
            prepared.append((record, regular, overtime))
        doc = db.execute('INSERT INTO sheets(activity,work_date,output_milli,note,creator,request_key) VALUES(?,?,?,?,?,?)', (activity, work_date, output, text(note, 'Notes', 3000, False), actor, key)).lastrowid
        for record, regular, overtime in prepared:
            db.execute('INSERT INTO time_lines(sheet,worker,regular_minutes,overtime_minutes,hourly_cents,overtime_bp,category,trade,crew_name,contractor) VALUES(?,?,?,?,?,?,?,?,?,?)', (doc, record['id'], regular, overtime, record['hourly_cents'], record['overtime_bp'], record['category'], record['trade'], record['crew_name'], record['contractor']))
        audit(db, actor, 'sheet.created', doc)
        return doc


def submit(db, actor, sheet):
    with transaction(db):
        permission(db, actor, ('admin', 'supervisor'))
        row = db.execute('SELECT * FROM sheets WHERE id=?', (sheet,)).fetchone()
        if not row or row['creator'] != actor or row['status'] != 'draft':
            raise ValidationError('Only the author can submit a draft.')
        db.execute("UPDATE sheets SET status='submitted' WHERE id=?", (sheet,))
        audit(db, actor, 'sheet.submitted', sheet)


def review(db, actor, sheet, decision, reason=''):
    with transaction(db):
        permission(db, actor, ('admin',))
        doc = db.execute('SELECT * FROM sheets WHERE id=?', (sheet,)).fetchone()
        if not doc or doc['status'] != 'submitted':
            raise ValidationError('Only submitted sheets can be reviewed.')
        if doc['creator'] == actor:
            raise ValidationError('A different administrator must review your own sheet.')
        if decision == 'return':
            reason = text(reason, 'Return reason', 1000)
            db.execute("UPDATE sheets SET status='draft' WHERE id=?", (sheet,))
            audit(db, actor, 'sheet.returned', sheet, reason)
            return
        if decision != 'approve':
            raise ValidationError('Choose an approval action.')
        if db.execute("SELECT 1 FROM sheets WHERE activity=? AND work_date=? AND status='approved'", (doc['activity'], doc['work_date'])).fetchone():
            raise ValidationError('An approved sheet already exists for this activity and date. Consolidate the daily output into one sheet.')
        for line in db.execute('SELECT * FROM time_lines WHERE sheet=?', (sheet,)):
            used = db.execute("SELECT COALESCE(SUM(t.regular_minutes+t.overtime_minutes),0) FROM time_lines t JOIN sheets s ON s.id=t.sheet WHERE t.worker=? AND s.work_date=? AND s.status='approved'", (line['worker'], doc['work_date'])).fetchone()[0]
            if used + line['regular_minutes'] + line['overtime_minutes'] > 1440:
                raise ValidationError('Approval would exceed 24 hours for a person on this date, across all projects.')
        db.execute("UPDATE sheets SET status='approved',reviewer=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?", (actor, sheet))
        audit(db, actor, 'sheet.approved', sheet, text(reason, 'Review notes', 1000, False))


def revise(db, actor, sheet, output, note):
    with transaction(db):
        permission(db, actor, ('admin', 'supervisor'))
        row = db.execute('SELECT s.*,a.kind FROM sheets s JOIN activities a ON a.id=s.activity WHERE s.id=?', (sheet,)).fetchone()
        if not row or row['creator'] != actor or row['status'] != 'draft':
            raise ValidationError('Only the author can revise a draft.')
        output = scaled(output, 1000, 'Completed quantity')
        if row['kind'] == 'indirect' and output:
            raise ValidationError('Indirect activities cannot record production.')
        db.execute('UPDATE sheets SET output_milli=?,note=? WHERE id=?', (output, text(note, 'Notes', 3000, False), sheet))
        audit(db, actor, 'sheet.revised', sheet)


def void(db, actor, sheet, reason):
    with transaction(db):
        permission(db, actor, ('admin',))
        reason = text(reason, 'Void reason', 1000)
        changed = db.execute("UPDATE sheets SET status='void' WHERE id=? AND status='approved'", (sheet,))
        if not changed.rowcount:
            raise ValidationError('Only approved sheets can be voided.')
        audit(db, actor, 'sheet.voided', sheet, reason)


def edit_time(db, actor, sheet, worker, regular, overtime):
    with transaction(db):
        permission(db, actor, ('admin', 'supervisor'))
        doc = db.execute('SELECT * FROM sheets WHERE id=?', (sheet,)).fetchone()
        line = db.execute('SELECT * FROM time_lines WHERE sheet=? AND worker=?', (sheet, worker)).fetchone()
        if not doc or doc['creator'] != actor or doc['status'] != 'draft' or not line:
            raise ValidationError('Only the author can edit an existing draft attendance line.')
        regular, overtime = minutes(regular), minutes(overtime)
        if not 0 < regular + overtime <= 1440:
            raise ValidationError('Record between 1 minute and 24 hours per person.')
        db.execute('UPDATE time_lines SET regular_minutes=?,overtime_minutes=? WHERE id=?', (regular, overtime, line['id']))
        audit(db, actor, 'attendance.revised', sheet, f'Personnel {worker}: {regular} regular + {overtime} overtime minutes')


def allocate(db, actor, worker, project, work_date, planned_hours):
    with transaction(db):
        permission(db, actor, ('admin', 'supervisor'))
        work_date = valid_date(work_date, True)
        planned = minutes(planned_hours)
        person = db.execute('SELECT * FROM workers WHERE id=? AND active=1', (worker,)).fetchone()
        if not person or not db.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
            raise ValidationError('Choose active personnel and a valid project.')
        others = db.execute('SELECT COALESCE(SUM(minutes),0) FROM allocations WHERE worker=? AND work_date=? AND project!=?', (worker, work_date, project)).fetchone()[0]
        if planned + others > person['daily_minutes']:
            raise ValidationError('Planned hours exceed this person’s daily capacity across projects.')
        if not planned:
            db.execute('DELETE FROM allocations WHERE worker=? AND project=? AND work_date=?', (worker, project, work_date))
        else:
            db.execute('INSERT INTO allocations(worker,project,work_date,minutes) VALUES(?,?,?,?) ON CONFLICT(worker,project,work_date) DO UPDATE SET minutes=excluded.minutes', (worker, project, work_date, planned))
        audit(db, actor, 'allocation.updated', detail=f'{worker}/{project}/{work_date}')


def report(db, start, end, project=None):
    start, end = valid_date(start, True), valid_date(end, True)
    if start > end:
        raise ValidationError('The report start date must precede its end date.')
    clauses = 's.work_date BETWEEN ? AND ?'
    params = [start, end]
    if project:
        clauses += ' AND a.project=?'
        params.append(project)
    sheets = db.execute("SELECT s.*,a.name AS activity_name,a.code AS activity_code,a.project,a.unit,a.kind,a.trade AS activity_trade,p.name AS project_name FROM sheets s JOIN activities a ON a.id=s.activity JOIN projects p ON p.id=a.project WHERE s.status='approved' AND " + clauses + ' ORDER BY s.work_date,s.id', params).fetchall()
    lines = db.execute("SELECT t.*,s.work_date,s.activity,w.name AS worker_name,w.code AS worker_code,a.name AS activity_name,a.trade AS activity_trade,p.name AS project_name FROM time_lines t JOIN sheets s ON s.id=t.sheet JOIN workers w ON w.id=t.worker JOIN activities a ON a.id=s.activity JOIN projects p ON p.id=a.project WHERE s.status='approved' AND " + clauses + ' ORDER BY s.work_date,w.name', params).fetchall()
    totals = {'regular': 0, 'overtime': 0, 'cost': 0, 'direct': 0, 'indirect': 0, 'people': len({line['worker'] for line in lines}), 'sheets': len(sheets)}
    categories = {key: {'minutes': 0, 'cost': 0, 'people': set()} for key in CATEGORIES}
    trades, activities, daily = {}, {}, {}
    for sheet in sheets:
        activities.setdefault(sheet['activity'], {'name': sheet['activity_name'], 'unit': sheet['unit'], 'output': 0, 'minutes': 0, 'cost': 0, 'kind': sheet['kind']})['output'] += sheet['output_milli']
        daily.setdefault(sheet['work_date'], {'minutes': 0, 'cost': 0})
    for line in lines:
        total = line['regular_minutes'] + line['overtime_minutes']
        cost = line_cost(line)
        totals['regular'] += line['regular_minutes']
        totals['overtime'] += line['overtime_minutes']
        totals['cost'] += cost
        activity = activities[line['activity']]
        activity['minutes'] += total
        activity['cost'] += cost
        totals[activity['kind']] += total
        category = categories[line['category']]
        category['minutes'] += total
        category['cost'] += cost
        category['people'].add(line['worker'])
        trade = trades.setdefault(line['activity_trade'], {'minutes': 0, 'cost': 0})
        trade['minutes'] += total
        trade['cost'] += cost
        daily[line['work_date']]['minutes'] += total
        daily[line['work_date']]['cost'] += cost
    return dict(totals=totals, categories=categories, trades=trades, activities=activities, daily=daily, lines=lines, sheets=sheets)


def performance(db, project=None):
    activities = db.execute('SELECT a.*,p.name AS project_name FROM activities a JOIN projects p ON p.id=a.project' + (' WHERE a.project=?' if project else '') + ' ORDER BY p.name,a.code', (project,) if project else ()).fetchall()
    result = []
    for row in activities:
        item = dict(row)
        item['output'] = db.execute("SELECT COALESCE(SUM(output_milli),0) FROM sheets WHERE activity=? AND status='approved'", (row['id'],)).fetchone()[0]
        lines = db.execute("SELECT t.* FROM time_lines t JOIN sheets s ON s.id=t.sheet WHERE s.activity=? AND s.status='approved'", (row['id'],)).fetchall()
        item['minutes'] = sum(l['regular_minutes'] + l['overtime_minutes'] for l in lines)
        item['cost'] = sum(line_cost(l) for l in lines)
        item['progress'] = Decimal(item['output']) * 100 / row['target_milli'] if row['target_milli'] else None
        item['earned_minutes'] = Decimal(row['budget_minutes']) * item['output'] / row['target_milli'] if row['target_milli'] else None
        item['efficiency'] = item['earned_minutes'] / item['minutes'] if item['minutes'] and item['earned_minutes'] is not None else None
        item['forecast_minutes'] = Decimal(item['minutes']) * row['target_milli'] / item['output'] if item['output'] and row['target_milli'] else None
        item['unit_hours'] = Decimal(item['minutes']) * 1000 / 60 / item['output'] if item['output'] else None
        result.append(item)
    return result
