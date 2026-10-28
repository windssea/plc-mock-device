"""Supervisor lifecycle checks only: no simulator, PLC, database or live port."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('managed',Path(__file__).resolve().parents[1]/'scripts/run_managed.py')
managed=importlib.util.module_from_spec(spec);spec.loader.exec_module(managed)


class Child:
    pid=123
    def __init__(self,code):self.returncode=code
    def poll(self):return self.returncode
    def wait(self,**_):return self.returncode


class Probe:
    def __init__(self,result):self.result=result
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def settimeout(self,_):pass
    def connect_ex(self,_):return self.result


class SupervisorTests(unittest.TestCase):
    def trial(self,code,occupied=False):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);ticks=[0.0]
            def sleep(seconds):ticks[0]+=seconds
            with patch.object(managed.socket,'socket',return_value=Probe(0 if occupied else 1)), \
                 patch.object(managed.subprocess,'Popen',return_value=Child(code)) as spawn, \
                 patch.object(managed.time,'monotonic',side_effect=lambda:ticks[0]), \
                 patch.object(managed.time,'sleep',side_effect=sleep):
                result=managed.supervise(directory,['never-run'],cwd=directory,port=1,status_url='unused')
            rows=[json.loads(line) for line in (directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]
            return result,spawn.call_count,rows

    def test_clean_exit_is_not_restarted(self):
        result,count,rows=self.trial(0)
        self.assertEqual((result,count),(0,1))
        self.assertEqual(rows[-1]['event'],'clean_exit_no_restart')
        self.assertEqual(next(r for r in rows if r['event']=='child_exited')['exitCodeHex'],'0x00000000')

    def test_crashes_back_off_then_stop(self):
        result,count,rows=self.trial(-1073741819)
        self.assertEqual((result,count),(3,3))
        self.assertEqual([r['delaySeconds'] for r in rows if r['event']=='restart_scheduled'],[5,10])
        self.assertEqual(rows[-1]['event'],'crash_limit_reached')
        self.assertEqual(next(r for r in rows if r['event']=='child_exited')['exitCodeHex'],'0xC0000005')

    def test_existing_service_is_never_replaced(self):
        result,count,rows=self.trial(1,occupied=True)
        self.assertEqual((result,count),(2,0))
        self.assertEqual(rows[-1]['event'],'port_conflict')

    def test_exclusive_supervisor_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            first=managed.lock(Path(folder))
            try:
                with self.assertRaises(RuntimeError):managed.lock(Path(folder))
            finally:first.close()
            second=managed.lock(Path(folder));second.close()


if __name__=='__main__':unittest.main()
