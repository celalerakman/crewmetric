from datetime import date, timedelta
from werkzeug.security import generate_password_hash
from . import service as s


def seed(db, password):
    db.execute("INSERT INTO users(username,password,role) VALUES('foreman',?,'supervisor')", (generate_password_hash(password),))
    db.execute("INSERT INTO users(username,password,role) VALUES('reviewer',?,'admin')", (generate_password_hash(password),))
    p1 = s.add_project(db, 1, 'RIV-01', 'Riverside Residences', 'Blocks A–C / main compound')
    p2 = s.add_project(db, 1, 'LOG-02', 'North Logistics Hall', 'Industrial zone / phase 2')
    c1 = s.add_crew(db, 1, 'Finishes team', 'Internal workforce')
    c2 = s.add_crew(db, 1, 'Structure team', 'Atlas Structure')
    c3 = s.add_crew(db, 1, 'MEP team', 'Northline MEP')
    c4 = s.add_crew(db, 1, 'Site office', 'Internal workforce')
    people = [('P-001','Emre Kaya','blue_collar','Plastering',c1,'220'), ('P-002','Mert Aydin','blue_collar','Plastering',c1,'210'), ('P-003','Deniz Aras','blue_collar','Painting',c1,'205'), ('P-004','Ali Demir','blue_collar','Floor finishes',c1,'230'), ('P-005','Can Yilmaz','blue_collar','Reinforcement',c2,'260'), ('P-006','Efe Sahin','blue_collar','Formwork',c2,'250'), ('P-007','Bora Celik','blue_collar','Electrical',c3,'280'), ('P-008','Arda Eren','blue_collar','Plumbing',c3,'270'), ('P-009','Selin Aksoy','white_collar','Site engineering',c4,'420'), ('P-010','Ece Deniz','white_collar','Safety',c4,'390'), ('P-011','Derya Acar','administrative','Administration',c4,'300'), ('P-012','Ozan Tan','blue_collar','Logistics',c4,'190')]
    for code, name, category, trade, crew, rate in people:
        s.add_worker(db, 1, code, name, category, trade, crew, rate, '1.5', '8')
    specifications = [(p1,'FIN-110','Internal wall plaster','Plastering','Block A · level 2','direct','m²','4200','700'), (p1,'FIN-120','Primer and finish coats','Painting','Block A · level 1','direct','m²','6000','800'), (p1,'FIN-130','Ceramic floor installation','Floor finishes','Block B · ground floor','direct','m²','1800','600'), (p1,'STR-210','Rebar fixing','Reinforcement','Block C · foundation','direct','tonne','120','1800'), (p1,'STR-220','Column and slab formwork','Formwork','Block C · level 1','direct','m²','2400','1200'), (p1,'MEP-310','Electrical conduit installation','Electrical','Block B · level 1','direct','m','8000','1000'), (p1,'MEP-320','Domestic water pipework','Plumbing','Block B · risers','direct','m','3000','600'), (p1,'SUP-410','Engineering and site coordination','Site engineering','Site office','indirect','—','0','900'), (p1,'SUP-420','Safety inspections','Safety','All work fronts','indirect','—','0','500'), (p1,'ADM-510','Administration and site logistics','Administration','Site office','indirect','—','0','700'), (p2,'STR-110','Hall floor reinforcement','Reinforcement','Hall 1','direct','tonne','200','2400')]
    activities = [s.add_activity(db, 1, *spec) for spec in specifications]
    for offset in range(6, 0, -1):
        day = (date.today() - timedelta(days=offset)).isoformat()
        entries = [(activities[0], '65', [(1,'8','0'),(2,'8','1')]), (activities[1], '48', [(3,'8','0')]), (activities[2], '24', [(4,'8','0')]), (activities[3], '1.8', [(5,'8','1')]), (activities[4], '18', [(6,'8','0')]), (activities[5], '75', [(7,'8','0')]), (activities[6], '42', [(8,'8','0')]), (activities[7], '0', [(9,'8','0')]), (activities[8], '0', [(10,'8','0')]), (activities[9], '0', [(11,'8','0'),(12,'8','0')])]
        for activity, output, lines in entries:
            sheet = s.create_sheet(db, 2, activity, day, output, 'Daily measured output and attendance checked at the work front.', lines, f'demo-{offset}-{activity}')
            s.submit(db, 2, sheet)
            s.review(db, 1, sheet, 'approve')
    today = date.today().isoformat()
    sheet = s.create_sheet(db, 2, activities[0], today, '72', 'East elevation ready for inspection.', [(1,'8','0'),(2,'8','0')], 'demo-pending')
    s.submit(db, 2, sheet)
    s.create_sheet(db, 2, activities[5], today, '68', 'Waiting for final length measurement.', [(7,'8','0')], 'demo-draft')
    for worker in range(1,13):
        s.allocate(db, 1, worker, p1, today, '8')
