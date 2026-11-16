"""Live contextual/adaptive reuse pairs from identical, actually earned snapshots."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import time
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import run_real_update_comparison as runner


@contextmanager
def orchestration_lock(path):
    """An OS-owned lock survives neither process exit nor a stale PID file."""
    with path.open('a+b') as handle:
        handle.seek(0,2)
        if handle.tell()==0:
            handle.write(b'1');handle.flush()
        handle.seek(0)
        try:
            if sys.platform=='win32':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Another retrieval comparison runner owns the orchestration lock; do not submit again.') from None
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform=='win32':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def windows_arguments(command):
    import ctypes
    from ctypes import wintypes
    parse=ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_int)]
    parse.restype=ctypes.POINTER(wintypes.LPWSTR)
    free=ctypes.windll.kernel32.LocalFree
    free.argtypes=[wintypes.HLOCAL];free.restype=wintypes.HLOCAL
    count=ctypes.c_int()
    values=parse(command,ctypes.byref(count))
    if not values:raise RuntimeError('Could not parse a comparison service command line.')
    try:return [values[index] for index in range(count.value)]
    finally:free(values)


def powershell_json(command):
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "+command],
        capture_output=True,encoding='utf-8',check=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(result.stdout)


def running_python_processes():
    """Read command lines; never signal a process (os.kill(pid, 0) is unsafe on Windows)."""
    if sys.platform!='win32':
        raise RuntimeError('This Windows comparison launcher requires read-only Windows process inspection.')
    rows=powershell_json("ConvertTo-Json -InputObject @(Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%.exe' OR Name='py.exe'\" | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine) -Compress")
    return {int(row['ProcessId']):dict(parent=int(row['ParentProcessId']),executable=row.get('ExecutablePath'),
        arguments=windows_arguments(row['CommandLine']) if row.get('CommandLine') else None) for row in rows}


def listening_owners(port):
    if type(port) is not int or not 1<=port<=65535:raise ValueError('Invalid service port')
    return set(powershell_json(f'ConvertTo-Json -InputObject @(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique) -Compress'))


def option(arguments,name):
    matches=[]
    for index,value in enumerate(arguments):
        if value==name:
            if index+1==len(arguments):raise ValueError('Service option is missing its value')
            matches.append(arguments[index+1])
        elif value.startswith(name+'='):matches.append(value[len(name)+1:])
    if len(matches)>1:raise ValueError('Service has ambiguous duplicate options')
    return matches[0] if matches else None


def resolved(value):
    path=Path(value)
    return (ROOT/path).resolve() if not path.is_absolute() else path.resolve()


def service_owners(folder,processes,known):
    known_pids={row['backend_pid'] for row in known if row.get('arm')==folder.name and row.get('backend_pid')}
    owners={}
    for pid,process in processes.items():
        arguments=process['arguments']
        if arguments is None:
            if pid in known_pids:raise ValueError('A recorded service is alive but its database ownership cannot be inspected; stop recovery.')
            continue
        service=any(Path(value).name=='real_comparison_service.py' or value=='scripts.real_comparison_service' for value in arguments)
        directory=option(arguments,'--arm-dir') if service else None
        if directory is not None and resolved(directory)==folder.resolve():owners[pid]=arguments
    # Windows venv python.exe is a redirector that remains alive while its base
    # interpreter child serves the API. Treat that exact parent/child pair as
    # one owner; independent siblings still constitute competing DB owners.
    launchers=set()
    for pid,arguments in owners.items():
        process=processes[pid];parent=processes.get(process['parent'])
        if (parent and process['parent'] in owners and parent['arguments']==arguments and
                parent.get('executable') and process.get('executable') and
                Path(parent['executable']).parent.name.casefold()=='scripts' and
                resolved(parent['executable'])!=resolved(process['executable'])):
            launchers.add(process['parent'])
    owners={pid:arguments for pid,arguments in owners.items() if pid not in launchers}
    return owners


def check_service_identity(arguments,record,folder,port,frozen,registry):
    expected={'--mode':'contextual' if record['condition']=='contextual' else 'after','--port':str(port)}
    if any(option(arguments,key)!=value for key,value in expected.items()):
        raise ValueError('Live database owner uses a different arm mode or port; do not restart or reuse it.')
    for key,path in (('--arm-dir',folder),('--reference-database',frozen),('--registry',registry)):
        value=option(arguments,key)
        if value is None or resolved(value)!=path.resolve():raise ValueError('Live service reference/database configuration differs from this paired trial.')
    for key,expected_value in (('--task-timeout',900),('--llm-timeout',90)):
        if float(option(arguments,key) or expected_value)!=expected_value:
            raise ValueError('Live service budgets differ from the registered protocol.')


def check_resume(record,case):
    expected=dict(source_project=record.get('project',{}).get('id'),task_description=case['task_description'],max_steps=30,execution_mode='single')
    if not expected['source_project'] or record.get('request')!=expected:
        raise ValueError('Recorded submission request differs from the paired case; do not resume another trial.')
    if record.get('task_id'):
        if runner.api(record['arm'],'/api/tasks/'+record['task_id'])['task_id']!=record['task_id']:
            raise ValueError('Occupied service cannot be associated with the recorded task.')
        return
    key=record.get('submission_key')
    if not key or not record.get('configured') or not record.get('project'):
        raise ValueError('Live service has no recorded task or durable submission intent; inspect it before resuming.')
    UUID(key)
    # runner.run verifies the server's Idempotency-Key capability and reuses this
    # exact key/body. A lost acceptance response never starts another task.


def occupied_port(port):
    with socket.socket() as probe:
        return probe.connect_ex(('127.0.0.1',port))==0


def existing_service(record,case,folder,port,frozen,registry,known):
    owners=service_owners(folder,running_python_processes(),known)
    if len(owners)>1:raise ValueError('Multiple live processes already own this arm database; do not add another.')
    if occupied_port(port):
        if len(owners)!=1 or listening_owners(port)!=set(owners):
            raise ValueError('Occupied ablation port is not owned by the exact recorded database service; stop recovery.')
        check_service_identity(next(iter(owners.values())),record,folder,port,frozen,registry)
        check_resume(record,case)
        return True
    if owners:
        raise ValueError('A live service already owns this arm database but is not listening; wait or inspect it, never start a second owner.')
    return False


def run_live():
    out=runner.OUT
    primary=json.loads((out/'records.json').read_text(encoding='utf-8'))
    if not all(r.get('observed_complete') for r in primary):
        raise ValueError('Finish the primary sequence first; avoid overlapping measured test execution.')
    records_file=out/'retrieval-records.json'
    records=json.loads(records_file.read_text(encoding='utf-8')) if records_file.exists() else []
    registry_path=out/'registry.json'
    registry=json.loads(registry_path.read_text(encoding='utf-8'))
    cases=json.loads((out/'cases/manifest.json').read_text(encoding='utf-8'))['cases']
    case=next(c for c in cases if c['id']=='pytest-10356')
    processes=json.loads((out/'retrieval-services.json').read_text(encoding='utf-8')) if (out/'retrieval-services.json').exists() else []
    initial_processes=running_python_processes()
    plan=[]
    for repetition in (1,2):
        frozen=out/f'after{repetition}-retrieval-reference.db'
        if not frozen.is_file():
            raise ValueError(f'Repetition {repetition} has no successful real training snapshot; do not invent references.')
        digest=hashlib.sha256(frozen.read_bytes()).hexdigest()
        for condition in (('contextual','adaptive') if repetition==1 else ('adaptive','contextual')):
            arm=f'{condition}{repetition}';folder=out/'runs'/arm;folder.mkdir(exist_ok=True)
            port=8015+(repetition-1)*2+(condition=='adaptive');front=5185+(repetition-1)*2+(condition=='adaptive')
            runner.ARMS[arm]=port
            if not (folder/'app.db').exists():
                if service_owners(folder,initial_processes,processes):
                    raise ValueError('An existing service is initializing this database; do not clone into its path.')
                with sqlite3.connect(frozen.as_uri()+'?mode=ro',uri=True) as origin, sqlite3.connect(folder/'app.db') as target:
                    origin.backup(target)
                runner.write(folder/'snapshot-origin.json',dict(path=str(frozen),sha256=digest))
            else:
                assert json.loads((folder/'snapshot-origin.json').read_text(encoding='utf-8'))['sha256']==digest
            if not any(e['database_path']==str(folder/'app.db') for e in registry['sources']):
                registry['sources'].append(dict(database_path=str(folder/'app.db'),runtime_label=f'同库复用·{condition}·第{repetition}轮',
                    comparison_group='检索消融·'+condition,code_revision='4ed926a33dd5c2d923852ae482136857fbaf7bd8',
                    task_url_base=f'http://127.0.0.1:{front}'))
            existing=next((r for r in records if r['key']==arm),None)
            if existing is None:
                existing=dict(key=arm,arm=arm,phase='retrieval-reuse',case_id='pytest-10356',condition=condition,
                              repetition=repetition,frozen_sha256=digest)
                records.append(existing)
            plan.append((existing,folder,port,front))
    runner.write(registry_path,registry);runner.write(records_file,records)
    for record,folder,port,front in plan:
        if record.get('observed_complete'):continue
        frozen=out/f"after{record['repetition']}-retrieval-reference.db"
        if existing_service(record,case,folder,port,frozen,registry_path,processes):
            runner.run(record,case,records,record_file=records_file)
            assert hashlib.sha256(frozen.read_bytes()).hexdigest()==record['frozen_sha256']
            continue
        logs=[(folder/'server.out.log').open('a',encoding='utf-8'),(folder/'server.err.log').open('a',encoding='utf-8')]
        mode='contextual' if record['condition']=='contextual' else 'after'
        flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
        server=subprocess.Popen([sys.executable,'-X','utf8','scripts/real_comparison_service.py','--arm-dir',str(folder),
            '--mode',mode,'--label',f"同库复用·{record['condition']}·第{record['repetition']}轮",'--group','检索消融·'+record['condition'],
            '--reference-database',str(out/f"after{record['repetition']}-retrieval-reference.db"),
            '--registry',str(registry_path),'--public-base-url',f'http://127.0.0.1:{front}','--port',str(port)],
            cwd=ROOT,stdout=logs[0],stderr=logs[1],creationflags=flags)
        process=dict(arm=record['arm'],backend_pid=server.pid,frontend_pid=None,port=port,frontend=front)
        processes.append(process)
        runner.write(out/'retrieval-services.json',processes)
        node=Path('C:/Program Files/nodejs/node.exe')
        browser_log=(folder/'frontend.log').open('a',encoding='utf-8')
        frontend=subprocess.Popen([str(node),'node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',str(front),'--strictPort'],
            cwd=ROOT/'frontend',env=os.environ|{'API_PROXY_TARGET':f'http://127.0.0.1:{port}'},
            stdout=browser_log,stderr=browser_log,creationflags=flags)
        process['frontend_pid']=frontend.pid
        runner.write(out/'retrieval-services.json',processes)
        deadline=time.monotonic()+30
        while True:
            try:runner.api(record['arm'],'/api/projects');break
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(0.5)
        ready_owners=service_owners(folder,running_python_processes(),processes)
        if len(ready_owners)!=1 or listening_owners(port)!=set(ready_owners):
            raise ValueError('Ready port cannot be attributed to the new arm service; no task was submitted.')
        check_service_identity(next(iter(ready_owners.values())),record,folder,port,frozen,registry_path)
        runner.run(record,case,records,record_file=records_file)
        frozen=out/f"after{record['repetition']}-retrieval-reference.db"
        assert hashlib.sha256(frozen.read_bytes()).hexdigest()==record['frozen_sha256']
    runner.write(records_file,records)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    if not args.live:
        print('4 planned real reuse trials: 2 independent snapshots, paired contextual/adaptive; no calls made.');return
    with orchestration_lock(runner.OUT/'retrieval-orchestration.lock'):
        run_live()


if __name__=='__main__':main()
