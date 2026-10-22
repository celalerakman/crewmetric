from datetime import date, timedelta
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from crewmetric import service as s


class CliTests(unittest.TestCase):
    def test_demo_check_backup_and_duplicate_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'demo.sqlite'
            def run(*args):
                return subprocess.run([sys.executable,'-m','crewmetric',*args],capture_output=True,text=True,timeout=40)
            result = run('demo','--database',str(path))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(path.with_name('demo-credential.txt').exists())
            password = path.with_name('demo-credential.txt').read_text()
            self.assertGreaterEqual(len(password),12)
            db = s.connect(path)
            report = s.report(db,(date.today()-timedelta(days=10)).isoformat(),date.today().isoformat())
            self.assertEqual(report['totals']['regular'],576*60)
            self.assertEqual(report['totals']['overtime'],12*60)
            self.assertEqual(report['totals']['cost'],15903000)
            self.assertEqual(report['totals']['people'],12)
            db.close()
            self.assertEqual(run('check','--database',str(path)).returncode,0)
            backup = Path(directory)/'backup.sqlite'
            self.assertEqual(run('backup','--database',str(path),'--output',str(backup)).returncode,0)
            self.assertEqual(run('check','--database',str(backup)).returncode,0)
            self.assertNotEqual(run('backup','--database',str(path),'--output',str(backup)).returncode,0)
            self.assertNotEqual(run('demo','--database',str(path)).returncode,0)
            self.assertEqual(path.with_name('demo-credential.txt').read_text(),password)

    def test_missing_database_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'missing.sqlite'
            result = subprocess.run([sys.executable,'-m','crewmetric','check','--database',str(path)],capture_output=True,text=True,timeout=20)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
