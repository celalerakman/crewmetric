import csv
from datetime import date
import io
from pathlib import Path
import tempfile
import unittest

from werkzeug.security import generate_password_hash
from crewmetric import service as s
from crewmetric.web import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'web.sqlite'
        db = s.connect(self.path)
        s.initialize(db)
        self.password = 'test-only-password-123'
        password_hash = generate_password_hash(self.password)
        db.executemany('INSERT INTO users(username,password,role) VALUES(?,?,?)', [('admin',password_hash,'admin'),('foreman',password_hash,'supervisor'),('viewer',password_hash,'viewer'),('reviewer',password_hash,'admin')])
        project = s.add_project(db,1,'P1','Housing','Block A')
        crew = s.add_crew(db,1,'Finishes','Internal')
        s.add_worker(db,1,'W1','Test Person','blue_collar','Plastering',crew,'217.37','1.5','8')
        self.activity = s.add_activity(db,1,project,'PL-01','Wall plaster','Plastering','Floor 1','direct','m²','100','50')
        self.day = date.today().isoformat()
        self.sheet = s.create_sheet(db,2,self.activity,self.day,'20','Field note',[(1,'8','1')],'fixture')
        db.close()
        self.app = create_app(self.path, 'x'*64)
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def csrf(self):
        self.client.get('/login')
        with self.client.session_transaction() as state:
            return state['csrf']

    def login(self, username='admin'):
        return self.client.post('/login', data={'csrf':self.csrf(),'username':username,'password':self.password})

    def post(self, path, **data):
        with self.client.session_transaction() as state:
            data['csrf'] = state['csrf']
        return self.client.post(path, data=data)

    def approve_fixture(self):
        db = s.connect(self.path)
        s.submit(db,2,self.sheet)
        s.review(db,1,self.sheet,'approve')
        db.close()

    def test_anonymous_protected(self):
        for path in ('/','/reports','/sheets/1','/reports.csv','/planning'):
            self.assertEqual(self.client.get(path).status_code,302)
        self.assertEqual(self.client.get('/health').json,{'status':'ok'})

    def test_all_pages_render_admin(self):
        self.login()
        for path in ('/','/sheets','/sheets/1','/sheets/new','/work','/personnel','/planning','/reports','/access','/reports.csv'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code,200)

    def test_author_draft_template_renders(self):
        self.login('foreman')
        html = self.client.get('/sheets/1').data
        self.assertIn(b'Revise draft attendance',html)
        self.assertIn(b'Submit for approval',html)

    def test_submitted_approved_and_void_templates(self):
        self.login('foreman')
        self.post('/sheets/1/action',action='submit')
        self.login()
        self.assertIn(b'Independent review',self.client.get('/sheets/1').data)
        self.post('/sheets/1/action',action='approve')
        self.assertIn(b'Void an incorrect',self.client.get('/sheets/1').data)
        self.post('/sheets/1/action',action='void',reason='Incorrect area')
        self.assertIn(b'Incorrect area',self.client.get('/sheets/1').data)

    def test_return_and_time_correction(self):
        self.login('foreman')
        self.post('/sheets/1/action',action='submit')
        self.login()
        self.post('/sheets/1/action',action='return',reason='Recheck attendance')
        self.login('foreman')
        self.post('/sheets/1/action',action='line',worker='1',regular='7.5',overtime='0')
        self.post('/sheets/1/action',action='revise',output='15',note='Checked')
        self.assertIn(b'7.50',self.client.get('/sheets/1').data)

    def test_csrf_all_post_routes(self):
        self.assertEqual(self.client.post('/login',data={}).status_code,400)
        self.login()
        for path in ('/logout','/personnel','/work','/planning','/sheets/new','/sheets/1/action','/access'):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path,data={}).status_code,400)

    def test_session_rotation_and_logout(self):
        old = self.csrf()
        self.login()
        with self.client.session_transaction() as state:
            self.assertNotEqual(old,state['csrf'])
        self.post('/logout')
        self.assertEqual(self.client.get('/').status_code,302)

    def test_bad_login_and_rate_limit(self):
        token = self.csrf()
        for _ in range(10):
            self.client.post('/login',data={'csrf':token,'username':'admin','password':'wrong'})
        self.assertEqual(self.client.post('/login',data={'csrf':token}).status_code,429)

    def test_viewer_routes(self):
        self.login('viewer')
        for path in ('/personnel','/work','/planning','/access','/sheets/new'):
            self.assertEqual(self.client.get(path).status_code,403)
        self.assertEqual(self.post('/sheets/1/action',action='approve').status_code,403)

    def test_supervisor_no_personnel_admin(self):
        self.login('foreman')
        self.assertEqual(self.client.get('/personnel').status_code,403)
        self.assertEqual(self.client.get('/access').status_code,403)
        self.assertEqual(self.client.get('/planning').status_code,200)

    def test_costs_absent_from_nonadmin_html_and_csv(self):
        self.approve_fixture()
        for user in ('foreman','viewer'):
            self.login(user)
            for path in ('/','/sheets/1','/reports'):
                html = self.client.get(path).data
                self.assertNotIn(b'217.37',html)
                self.assertNotIn(b'Hourly cost',html)
                self.assertNotIn(b'Labor cost',html)
            csv_text = self.client.get('/reports.csv').data.decode('utf-8-sig')
            self.assertNotIn('Labor cost',csv_text)
            self.assertNotIn('217.37',csv_text)

    def test_admin_cost_snapshot_visible(self):
        self.approve_fixture()
        self.login()
        html = self.client.get('/sheets/1').data
        self.assertIn(b'217.37',html)
        self.assertIn(b'2,065.02',html)

    def test_csv_formula_neutralized(self):
        db = s.connect(self.path)
        db.execute('UPDATE workers SET name=? WHERE id=1',('=HYPERLINK("bad")',))
        db.close()
        self.approve_fixture()
        self.login()
        rows = list(csv.reader(io.StringIO(self.client.get('/reports.csv').data.decode('utf-8-sig'))))
        self.assertTrue(rows[1][4].startswith("'="))
        self.assertEqual(rows[1][-2],'2065.02')

    def test_create_sheet_route_and_replay(self):
        self.login('foreman')
        data = dict(activity=str(self.activity),date=self.day,output='25',note='New',worker=['1',''],regular=['8',''],overtime=['0',''],request_key='new')
        a = self.post('/sheets/new',**data)
        b = self.post('/sheets/new',**data)
        self.assertEqual(a.location,b.location)
        self.assertIn('/sheets/2',a.location)

    def test_malformed_lines_rejected(self):
        self.login('foreman')
        response = self.post('/sheets/new',activity='1',date=self.day,output='1',worker=['1','2'],regular=['8'],overtime=['0'],request_key='bad')
        self.assertEqual(response.status_code,302)
        self.assertIn(b'incomplete',self.client.get('/').data)

    def test_bulk_creation_route(self):
        self.login('foreman')
        response = self.post('/sheets/new',activity='1',date=self.day,output='10',attendance_csv='personnel_code,regular_hours,overtime_hours\nW1,7:30,0',request_key='bulk')
        self.assertIn('/sheets/2',response.location)
        self.assertIn(b'7.50',self.client.get(response.location).data)

    def test_bulk_and_individual_conflict(self):
        self.login('foreman')
        response = self.post('/sheets/new',activity='1',date=self.day,output='10',worker='1',regular='8',overtime='0',attendance_csv='personnel_code,regular_hours,overtime_hours\nW1,8,0',request_key='conflict')
        self.assertEqual(response.status_code,302)
        self.assertIn(b'either individual',self.client.get('/').data)

    def test_personnel_crew_project_activity_forms(self):
        self.login()
        self.post('/personnel',action='crew',name='MEP',contractor='Internal')
        self.post('/personnel',code='W2',name='Second Person',category='administrative',trade='Administration',crew='2',rate='200',overtime='1.5',capacity='8')
        self.assertIn(b'Second Person',self.client.get('/personnel').data)
        self.post('/personnel',action='update',worker='2',rate='250',active='on')
        self.post('/work',action='project',code='P2',name='Hall',location='North')
        self.post('/work',project='2',code='OFF-1',name='Office',trade='Administration',zone='Office',kind='indirect',unit='none',target='0',budget='100')
        self.assertIn(b'OFF-1',self.client.get('/work').data)

    def test_planning_route(self):
        self.login()
        self.post('/planning',worker='1',project='1',date=self.day,hours='8')
        self.assertIn(b'8.00',self.client.get('/planning').data)

    def test_create_account(self):
        self.login()
        self.post('/access',username='new-reader',password='new-reader-password',role='viewer')
        self.assertIn(b'new-reader',self.client.get('/access').data)

    def test_security_headers_and_escaping(self):
        self.login()
        self.post('/personnel',action='crew',name='<script>alert(1)</script>',contractor='Internal')
        result = self.client.get('/personnel')
        self.assertIn(b'&lt;script&gt;',result.data)
        self.assertNotIn(b'<script>alert',result.data)
        self.assertEqual(result.headers['X-Frame-Options'],'DENY')
        self.assertIn("default-src 'self'",result.headers['Content-Security-Policy'])

    def test_missing_sheet_and_invalid_dates(self):
        self.login()
        self.assertEqual(self.client.get('/sheets/999').status_code,404)
        self.assertEqual(self.client.get('/reports?start=invalid').status_code,302)


if __name__ == '__main__':
    unittest.main()
