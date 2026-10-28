"""Single mock instance with durable exit evidence and bounded crash recovery.

Uses the existing configuration DB. No login task, Windows service, or database
is created. An operator stops it by creating <state-dir>/stop.request.
"""
import argparse
from collections import deque
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen


def utc():return datetime.now(timezone.utc).isoformat()


def event(directory,kind,**fields):
    value=dict(atUtc=utc(),event=kind,supervisorPid=os.getpid(),**fields)
    with (directory/'events.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(value,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
    temporary=directory/'state.next'
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,directory/'state.json')


def exit_details(code):
    return dict(exitCode=code,exitCodeHex=f'0x{code & 0xffffffff:08X}')


def lock(directory):
    import msvcrt
    stream=(directory/'supervisor.lock').open('a+b')
    stream.seek(0,2)
    if stream.tell()==0:stream.write(b'\0');stream.flush()
    stream.seek(0)
    try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        stream.close();raise RuntimeError('A managed mock supervisor already owns this directory') from None
    return stream


def supervise(directory,command,*,cwd,port,status_url,max_crashes=3):
    crashes=deque();attempt=0
    event(directory,'supervisor_started',python=sys.executable,command=command,cwd=str(cwd),parentPid=os.getppid())
    while not (directory/'stop.request').exists():
        # A surviving orphan or another service must never be killed/replaced.
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1',port))==0:
                event(directory,'port_conflict',port=port);return 2
        attempt+=1;started=time.monotonic();run=f'{datetime.now():%Y%m%d-%H%M%S}-{attempt}'
        with (directory/(run+'.stdout.log')).open('ab',buffering=0) as out,(directory/(run+'.stderr.log')).open('ab',buffering=0) as err:
            child=subprocess.Popen(command,cwd=cwd,stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                creationflags=(subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP) if os.name=='nt' else 0,
                env=dict(os.environ,PYTHONUNBUFFERED='1',PYTHONUTF8='1'))
            event(directory,'child_started',childPid=child.pid,attempt=attempt,stdout=out.name,stderr=err.name)
            next_check=0;last_health=None
            while child.poll() is None and not (directory/'stop.request').exists():
                if time.monotonic()>=next_check:
                    try:
                        with urlopen(status_url,timeout=3) as response:
                            status=json.load(response)
                        # Log transitions, not a high-volume polling trace.
                        health=dict(reachable=True,configError=status.get('config_error'),
                                    deviceCount=len(status.get('devices',{})))
                    except Exception as exc:health=dict(reachable=False,error=type(exc).__name__)
                    if health!=last_health:
                        event(directory,'health_changed',childPid=child.pid,health=health);last_health=health
                    next_check=time.monotonic()+10
                time.sleep(1)
            if (directory/'stop.request').exists():
                child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
                event(directory,'operator_stopped',childPid=child.pid,**exit_details(child.returncode));return 0
            code=child.wait()
            event(directory,'child_exited',childPid=child.pid,uptimeSeconds=round(time.monotonic()-started,3),**exit_details(code))
        if code==0:
            event(directory,'clean_exit_no_restart');return 0
        now=time.monotonic()
        while crashes and now-crashes[0]>=300:crashes.popleft()
        crashes.append(now)
        if len(crashes)>=max_crashes:
            event(directory,'crash_limit_reached',crashesWithin300Seconds=len(crashes));return 3
        delay=min(60,5*2**(len(crashes)-1))
        event(directory,'restart_scheduled',delaySeconds=delay)
        until=time.monotonic()+delay
        while time.monotonic()<until:
            if (directory/'stop.request').exists():return 0
            time.sleep(.5)
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--state-dir',required=True,type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];database=args.database.resolve();config=args.config.resolve()
    if not database.is_file() or not config.is_file():raise ValueError('Existing database and bootstrap file required')
    directory=args.state_dir.resolve();directory.mkdir(parents=True,exist_ok=True)
    if (directory/'stop.request').exists():raise ValueError('Operator stop marker remains; remove explicitly before restart')
    with lock(directory):
        return supervise(directory,[sys.executable,'-m','iot_sim.main','serve','--config',str(config),'--database',str(database)],
            cwd=root,port=18080,status_url='http://127.0.0.1:18080/api/v1/status')


if __name__=='__main__':raise SystemExit(main())
