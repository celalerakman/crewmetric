import concurrent.futures
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest

from crewmetric import service as s


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.sqlite'
        self.db = s.connect(self.path)
        s.initialize(self.db)
        self.db.executemany('INSERT INTO users(username,password,role) VALUES(?,?,?)', [('admin','unused','admin'), ('author','unused','supervisor'), ('reviewer','unused','admin'), ('viewer','unused','viewer')])
        self.project = s.add_project(self.db, 1, 'P1', 'Housing', 'Block A')
        self.other_project = s.add_project(self.db, 1, 'P2', 'Warehouse', 'Hall 1')
        self.crew = s.add_crew(self.db, 1, 'Finishes', 'Internal')
        self.worker = s.add_worker(self.db, 1, 'W1', 'Person One', 'blue_collar', 'Plastering', self.crew, '200', '1.5', '8')
        self.worker2 = s.add_worker(self.db, 1, 'W2', 'Person Two', 'white_collar', 'Site engineering', self.crew, '300', '2', '8')
        self.activity = s.add_activity(self.db, 1, self.project, 'A1', 'Wall plaster', 'Plastering', 'Level 1', 'direct', 'm²', '100', '50')
        self.other = s.add_activity(self.db, 1, self.other_project, 'A1', 'Hall plaster', 'Plastering', 'Hall 1', 'direct', 'm²', '200', '80')
        self.indirect = s.add_activity(self.db, 1, self.project, 'OFF', 'Site engineering', 'Site engineering', 'Office', 'indirect', 'none', '100', '100')
        self.day = date.today().isoformat()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def sheet(self, activity=None, output='10', lines=None, actor=2, key=None, day=None):
        return s.create_sheet(self.db, actor, activity or self.activity, day or self.day, output, 'Field notes', lines or [(self.worker, '8', '1')], key or secrets.token_hex(12))

    def approve(self, doc):
        creator = self.db.execute('SELECT creator FROM sheets WHERE id=?', (doc,)).fetchone()[0]
        s.submit(self.db, creator, doc)
        s.review(self.db, 1 if creator != 1 else 3, doc, 'approve')

    def report(self, project=None):
        return s.report(self.db, self.day, self.day, project)

    def test_minutes_are_exact(self):
        self.assertEqual(s.minutes('7.5'), 450)
        self.assertEqual(s.minutes('0.05'), 3)
        self.assertEqual(s.hours(450), '7.50')
        self.assertEqual(s.minutes('7:30'), 450)
        self.assertEqual(s.minutes('0:01'), 1)
        self.assertEqual(s.minutes('24:00'), 1440)

    def test_invalid_clock_hours(self):
        for value in ('7:60','24:01','-1:30','abc:30','1:2:3'):
            with self.subTest(value=value), self.assertRaises(s.ValidationError):
                s.minutes(value)

    def test_bulk_csv_codes_and_clock_hours(self):
        source = 'personnel_code,regular_hours,overtime_hours\nW1,7:30,1\nW2,8,0'
        lines = s.parse_attendance(self.db, source)
        self.approve(self.sheet(lines=lines))
        self.assertEqual(self.report()['totals']['regular'], 930)
        self.assertEqual(self.report()['totals']['overtime'], 60)

    def test_invalid_bulk_csv(self):
        for source in ('', 'code,hours\nW1,8', 'personnel_code,regular_hours,overtime_hours\nW1,8', 'personnel_code,regular_hours,overtime_hours\nUNKNOWN,8,0', 'personnel_code,regular_hours,overtime_hours\nW1,8,0,extra'):
            with self.subTest(source=source), self.assertRaises(s.ValidationError):
                s.parse_attendance(self.db, source)

    def test_bulk_duplicate_person_rejected_atomically(self):
        lines = s.parse_attendance(self.db,'personnel_code,regular_hours,overtime_hours\nW1,8,0\nW1,4,0')
        with self.assertRaises(s.ValidationError):
            self.sheet(lines=lines)

    def test_bulk_maximum(self):
        source = 'personnel_code,regular_hours,overtime_hours\n' + 'W1,8,0\n'*101
        with self.assertRaises(s.ValidationError):
            s.parse_attendance(self.db, source)

    def test_invalid_hours(self):
        for value in ('NaN','Infinity','-1','24.01','7.333','abc',''):
            with self.subTest(value=value), self.assertRaises(s.ValidationError):
                s.minutes(value)

    def test_invalid_dates(self):
        for value in ('nonsense', '2026-02-30', (date.today()+timedelta(days=1)).isoformat()):
            with self.subTest(value=value), self.assertRaises(s.ValidationError):
                s.valid_date(value)

    def test_drafts_and_submitted_excluded(self):
        doc = self.sheet()
        self.assertEqual(self.report()['totals']['regular'], 0)
        s.submit(self.db, 2, doc)
        self.assertEqual(self.report()['totals']['regular'], 0)

    def test_regular_overtime_and_cost(self):
        self.approve(self.sheet())
        totals = self.report()['totals']
        self.assertEqual(totals['regular'], 480)
        self.assertEqual(totals['overtime'], 60)
        self.assertEqual(totals['cost'], 190000)

    def test_output_counted_once_for_crew(self):
        self.approve(self.sheet(lines=[(1,'8','0'),(2,'8','0')]))
        activity = self.report()['activities'][self.activity]
        self.assertEqual(activity['output'], 10000)
        self.assertEqual(activity['minutes'], 960)

    def test_snapshot_survives_rate_change(self):
        doc = self.sheet()
        s.update_worker(self.db, 1, self.worker, '999', 1)
        self.approve(doc)
        self.assertEqual(self.report()['totals']['cost'], 190000)

    def test_new_sheet_uses_new_rate(self):
        s.update_worker(self.db, 1, self.worker, '250', 1)
        self.approve(self.sheet())
        self.assertEqual(self.report()['totals']['cost'], 237500)

    def test_half_cent_rounding(self):
        line = dict(regular_minutes=3, overtime_minutes=0, hourly_cents=10, overtime_bp=15000)
        self.assertEqual(s.line_cost(line), 1)

    def test_self_approval_forbidden(self):
        doc = self.sheet(actor=1)
        s.submit(self.db, 1, doc)
        with self.assertRaises(s.ValidationError):
            s.review(self.db, 1, doc, 'approve')
        s.review(self.db, 3, doc, 'approve')

    def test_duplicate_activity_day_forbidden(self):
        self.approve(self.sheet())
        second = self.sheet(lines=[(2,'8','0')])
        s.submit(self.db, 2, second)
        with self.assertRaises(s.ValidationError):
            s.review(self.db, 1, second, 'approve')

    def test_cross_project_limit(self):
        self.approve(self.sheet(lines=[(1,'16','0')]))
        doc = self.sheet(activity=self.other, lines=[(1,'8','1')])
        s.submit(self.db, 2, doc)
        with self.assertRaises(s.ValidationError):
            s.review(self.db, 1, doc, 'approve')

    def test_exact_24h_allowed(self):
        self.approve(self.sheet(lines=[(1,'16','0')]))
        self.approve(self.sheet(activity=self.other, lines=[(1,'8','0')]))
        self.assertEqual(self.report()['totals']['regular'], 1440)

    def test_concurrent_approval_serializes_limit(self):
        docs = [self.sheet(lines=[(1,'16','0')]), self.sheet(activity=self.other, lines=[(1,'16','0')])]
        for doc in docs:
            s.submit(self.db, 2, doc)
        def attempt(doc):
            db = s.connect(self.path)
            try:
                s.review(db, 1, doc, 'approve')
                return True
            except s.ValidationError:
                return False
            finally:
                db.close()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(attempt, docs)), 1)

    def test_void_excludes_but_retains(self):
        doc = self.sheet()
        self.approve(doc)
        s.void(self.db, 1, doc, 'Measured quantity corrected')
        self.assertEqual(self.report()['totals']['cost'], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM time_lines WHERE sheet=?', (doc,)).fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT status FROM sheets WHERE id=?', (doc,)).fetchone()[0], 'void')

    def test_void_allows_replacement(self):
        doc = self.sheet()
        self.approve(doc)
        s.void(self.db, 1, doc, 'Replace')
        self.approve(self.sheet(output='12'))
        self.assertEqual(self.report()['activities'][self.activity]['output'], 12000)

    def test_void_requires_reason_and_approved(self):
        doc = self.sheet()
        with self.assertRaises(s.ValidationError):
            s.void(self.db, 1, doc, 'Not approved')
        self.approve(doc)
        with self.assertRaises(s.ValidationError):
            s.void(self.db, 1, doc, '')

    def test_return_revise_resubmit(self):
        doc = self.sheet()
        s.submit(self.db, 2, doc)
        s.review(self.db, 1, doc, 'return', 'Check measured area')
        s.revise(self.db, 2, doc, '15', 'Rechecked')
        s.edit_time(self.db, 2, doc, 1, '7', '0')
        self.approve(doc)
        self.assertEqual(self.report()['totals']['regular'], 420)
        self.assertEqual(self.report()['activities'][self.activity]['output'], 15000)

    def test_approval_notes_preserved(self):
        doc = self.sheet()
        s.submit(self.db, 2, doc)
        s.review(self.db, 1, doc, 'approve', 'Measured area confirmed')
        row = self.db.execute("SELECT detail FROM audit WHERE sheet=? AND action='sheet.approved'", (doc,)).fetchone()
        self.assertEqual(row[0], 'Measured area confirmed')

    def test_only_author_can_submit_and_revise(self):
        doc = self.sheet()
        for action in (lambda: s.submit(self.db,1,doc), lambda: s.revise(self.db,1,doc,'20',''), lambda: s.edit_time(self.db,1,doc,1,'4','0')):
            with self.assertRaises(s.ValidationError):
                action()

    def test_approved_records_frozen(self):
        doc = self.sheet()
        self.approve(doc)
        queries = [('UPDATE sheets SET output_milli=999 WHERE id=?',(doc,)), ('DELETE FROM sheets WHERE id=?',(doc,)), ('UPDATE time_lines SET regular_minutes=1 WHERE sheet=?',(doc,)), ('DELETE FROM time_lines WHERE sheet=?',(doc,))]
        for query, values in queries:
            with self.subTest(query=query), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(query, values)

    def test_void_does_not_permit_hidden_mutation(self):
        doc = self.sheet()
        self.approve(doc)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE sheets SET status='void',output_milli=999 WHERE id=?", (doc,))

    def test_submitted_lines_frozen(self):
        doc = self.sheet()
        s.submit(self.db, 2, doc)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('UPDATE time_lines SET hourly_cents=1 WHERE sheet=?', (doc,))

    def test_idempotent_draft_creation(self):
        self.assertEqual(self.sheet(key='retry'), self.sheet(key='retry'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sheets').fetchone()[0], 1)

    def test_invalid_personnel_lines(self):
        for lines in ([(1,'0','0')],[(1,'24','1')],[(999,'8','0')],[(1,'8','0'),(1,'2','0')]):
            with self.subTest(lines=lines), self.assertRaises(s.ValidationError):
                self.sheet(lines=lines)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sheets').fetchone()[0], 0)

    def test_inactive_personnel_excluded(self):
        s.update_worker(self.db, 1, 1, '200', 0)
        with self.assertRaises(s.ValidationError):
            self.sheet()

    def test_indirect_work_has_no_output(self):
        with self.assertRaises(s.ValidationError):
            self.sheet(activity=self.indirect)
        self.approve(self.sheet(activity=self.indirect, output='0', lines=[(2,'8','0')]))
        self.assertEqual(self.report()['totals']['indirect'], 480)

    def test_category_is_independent_of_work_kind(self):
        self.approve(self.sheet(lines=[(2,'8','0')]))
        data = self.report()
        self.assertEqual(data['categories']['white_collar']['minutes'], 480)
        self.assertEqual(data['totals']['direct'], 480)

    def test_progress_and_efficiency(self):
        self.approve(self.sheet(output='20', lines=[(1,'8','0')]))
        p = next(a for a in s.performance(self.db) if a['id']==self.activity)
        self.assertEqual(p['progress'], 20)
        self.assertEqual(p['earned_minutes'], 600)
        self.assertEqual(p['efficiency'], Decimal('1.25'))
        self.assertEqual(p['forecast_minutes'], 2400)
        self.assertEqual(p['unit_hours'], Decimal('0.4'))

    def test_zero_output_has_no_forecast(self):
        self.approve(self.sheet(output='0'))
        row = next(a for a in s.performance(self.db) if a['id']==self.activity)
        self.assertIsNone(row['forecast_minutes'])
        self.assertEqual(row['efficiency'], 0)

    def test_report_project_filter(self):
        self.approve(self.sheet())
        self.approve(self.sheet(activity=self.other, lines=[(2,'5','0')]))
        self.assertEqual(self.report(self.project)['totals']['regular'], 480)
        self.assertEqual(len(s.performance(self.db, self.other_project)), 1)

    def test_report_date_filter(self):
        yesterday = (date.today()-timedelta(days=1)).isoformat()
        self.approve(self.sheet(day=yesterday))
        self.assertEqual(self.report()['totals']['people'], 0)
        self.assertEqual(s.report(self.db, yesterday, yesterday)['totals']['people'], 1)
        with self.assertRaises(s.ValidationError):
            s.report(self.db, self.day, yesterday)

    def test_planning_capacity_across_projects(self):
        s.allocate(self.db, 2, 1, self.project, self.day, '6')
        s.allocate(self.db, 2, 1, self.other_project, self.day, '2')
        with self.assertRaises(s.ValidationError):
            s.allocate(self.db, 2, 1, self.other_project, self.day, '3')

    def test_allocation_replace_and_remove(self):
        s.allocate(self.db, 2, 1, self.project, self.day, '6')
        s.allocate(self.db, 2, 1, self.project, self.day, '4')
        self.assertEqual(self.db.execute('SELECT minutes FROM allocations').fetchone()[0], 240)
        s.allocate(self.db, 2, 1, self.project, self.day, '0')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM allocations').fetchone()[0], 0)

    def test_no_self_approval_or_viewer_mutations(self):
        with self.assertRaises(s.ValidationError):
            self.sheet(actor=4)
        with self.assertRaises(s.ValidationError):
            s.add_project(self.db, 2, 'NO', 'Invalid', 'Here')
        with self.assertRaises(s.ValidationError):
            s.allocate(self.db, 4, 1, self.project, self.day, '4')

    def test_supervisor_cannot_approve_or_void(self):
        doc = self.sheet()
        s.submit(self.db, 2, doc)
        with self.assertRaises(s.ValidationError):
            s.review(self.db, 2, doc, 'approve')
        s.review(self.db, 1, doc, 'approve')
        with self.assertRaises(s.ValidationError):
            s.void(self.db, 2, doc, 'Invalid')

    def test_invalid_master_inputs(self):
        for category, trade, overtime, capacity in [('unknown','Painting','1.5','8'),('blue_collar','Unknown','1.5','8'),('blue_collar','Painting','0.5','8'),('blue_collar','Painting','1.5','0')]:
            with self.subTest(category=category), self.assertRaises(s.ValidationError):
                s.add_worker(self.db,1,'ERR','Person',category,trade,self.crew,'200',overtime,capacity)
        with self.assertRaises(s.ValidationError):
            s.add_activity(self.db,1,self.project,'ERR','Scope','Painting','Zone','direct','m²','0','8')


if __name__ == '__main__':
    unittest.main()
