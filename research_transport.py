"""Task-local stdio MCP: JSON files and Python argv, never shell command text."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

INSTRUCTIONS = ('Use these tools for all files and processes. There is no shell. '
    'Send paths, text and argument arrays as JSON; never embed PowerShell/bash commands. '
    'Use training once, then wait on its returned job_id; training_status only reads receipts. '
    'All ML fitting must use training. Use python only for task-local inspection scripts. '
    'Do not install packages, use other agents, or modify frozen inputs.')

def schema(properties, required=()):
    return dict(type='object', properties=properties, required=list(required), additionalProperties=False)

STRING = {'type':'string'}
STRINGS = {'type':'array','items':STRING}
TOOLS = [
    dict(name='files', description='Read text/JSON, stat, list, search, or write candidate/scripts without shell quoting.',
         inputSchema=schema({'action':{'type':'string','enum':['read','stat','list','search','write']},
            'path':STRING,'text':STRING,'pattern':STRING,'start_line':{'type':'integer','minimum':1},
            'max_lines':{'type':'integer','minimum':1,'maximum':1000}}, ['action','path'])),
    dict(name='python',description='Run a workspace/scripts/*.py inspection script, with each argument a separate JSON string. No -c or shell.',
         inputSchema=schema({'script':STRING,'args':STRINGS,'runtime':{'type':'string','enum':['main']}}, ['script'])),
    dict(name='training',description='Start the exact frozen training.command once. Repeated requests while running return the same job_id.',inputSchema=schema({})),
    dict(name='training_status',description='Read the original training receipt with the exact frozen status_command; never fits.',inputSchema=schema({})),
    dict(name='wait',description='Wait up to20 seconds for an existing job; never launches another process.',
         inputSchema=schema({'job_id':STRING,'seconds':{'type':'number','minimum':0,'maximum':20}}, ['job_id'])),
]

class Transport:
    def __init__(self, packet_path):
        from loop_storage import readonly_paths
        self.input = Path(packet_path).resolve()
        self.task = self.input.parent
        self.workspace = self.task/'workspace'
        self.packet = json.loads(self.input.read_text(encoding='utf-8'))
        self.review = self.packet.get('role') == 'review'
        if self.review and (self.packet.get('review') or {}).get('protocol') != 'expert-files-v1':
            raise ValueError('Legacy expert inputs cannot start a new transport')
        self.readonly = {p.resolve() for p in readonly_paths(self.task,self.packet)}
        self.history_catalog = None
        index = self.workspace/'history/index.json'
        if index.is_file():
            view = json.loads(index.read_text(encoding='utf-8'))
            if isinstance(view,dict) and view.get('format')=='history-references-v1':
                # readonly_paths already verified its selected records and path.
                self.history_catalog=(self.task/view['catalog']).resolve()
        self.output = self.workspace/'output/processes'
        self.output.mkdir(parents=True,exist_ok=True)
        self.journal = self.workspace/'output/process-events.jsonl'
        self.jobs = {}
        self.training_job = None

    def available_tools(self):
        return [item for item in TOOLS if not self.review or item['name']=='files']

    def require_research(self):
        if self.review:
            raise ValueError('Expert role has file tools only; process and training entry points are unavailable')

    def record(self, **row):
        with self.journal.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(dict(utc=time.time(),**row),ensure_ascii=False)+'\n')
            stream.flush()

    def path(self, name):
        from loop_storage import safe
        p = (self.task/name).resolve()
        if p.is_relative_to(self.task):
            safe(self.task, self.task/name)
        if not p.is_relative_to(self.task) and p not in self.readonly and p != self.history_catalog:
            raise ValueError('Path is outside the frozen task input mapping')
        return p

    def files(self, action, path, text=None, pattern=None, start_line=1, max_lines=200):
        p = self.path(path)
        start_line, max_lines = max(1,int(start_line)), min(1000,max(1,int(max_lines)))
        if action=='write':
            if self.review:
                allowed = {self.task/self.packet['review'][key] for key in ('research_file','plan_file')}
                if p not in allowed or not isinstance(text,str):
                    raise ValueError('Expert may write only its staged research.md and plan.json')
                if (self.task/'publication/manifest.json').exists():
                    raise ValueError('Expert outputs are frozen after publication begins')
                p.parent.mkdir(parents=True,exist_ok=True)
                temporary=p.with_name(p.name+'.transport-tmp')
                temporary.write_bytes(text.encode('utf-8'))
                os.replace(temporary,p)
                return dict(path=str(p),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            candidate = self.workspace/'train.py'
            if (p in self.readonly or not (p==candidate or p.is_relative_to(self.workspace/'scripts'))
                    or p.suffix.lower()!='.py' or not isinstance(text,str)):
                raise ValueError('Write only workspace/train.py or workspace/scripts/*.py')
            if p==candidate:
                if self.training_job and self.jobs[self.training_job]['process'].poll() is None:
                    raise ValueError('Candidate is frozen while original training is running')
                receipt = self.workspace/'training/receipt.json'
                if receipt.exists():
                    raise ValueError('Candidate is frozen after its terminal training receipt')
            p.parent.mkdir(parents=True,exist_ok=True)
            temporary = p.with_name(p.name+'.transport-tmp')
            temporary.write_bytes(text.encode('utf-8'))
            os.replace(temporary,p)
            return dict(path=str(p),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        if self.review:
            readable = self.readonly | {self.history_catalog} | {self.task/self.packet['review'][key] for key in ('research_file','plan_file')}
            if p not in readable and not (action=='list' and any(x is not None and p in x.parents for x in readable)):
                raise ValueError('Expert may read only exposed evidence and its own staged files')
        if action=='stat':
            return dict(path=str(p),exists=p.exists(),is_directory=p.is_dir(),bytes=p.stat().st_size if p.is_file() else None)
        if action=='list':
            return dict(entries=[dict(name=x.name,is_directory=x.is_dir(),bytes=x.stat().st_size if x.is_file() else None)
                for x in sorted(p.iterdir())][:1000])
        lines = p.read_text(encoding='utf-8-sig').splitlines()
        if action=='search':
            if not isinstance(pattern,str):
                raise ValueError('search requires a literal pattern')
            return dict(matches=[dict(line=i+1,text=line) for i,line in enumerate(lines) if pattern in line][:max_lines])
        if action!='read':
            raise ValueError('Unknown file operation')
        selected = lines[start_line-1:start_line-1+max_lines]
        return dict(path=str(p),total_lines=len(lines),start_line=start_line,text='\n'.join(selected)[:60000])

    def start(self, argv, kind):
        self.require_research()
        if not isinstance(argv,list) or not argv or not all(isinstance(x,str) for x in argv):
            raise ValueError('argv must be an array of strings')
        identifier = uuid.uuid4().hex[:16]
        out,err = self.output/(identifier+'.stdout'),self.output/(identifier+'.stderr')
        env = {**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1',
               'TEMP':str(self.workspace/'output/tmp'),'TMP':str(self.workspace/'output/tmp')}
        (self.workspace/'output/tmp').mkdir(exist_ok=True)
        with out.open('wb') as stdout,err.open('wb') as stderr:
            process = subprocess.Popen(argv,cwd=self.task,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                shell=False,env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.jobs[identifier] = dict(process=process,stdout=out,stderr=err,kind=kind,reported=False)
        self.record(event='started',job_id=identifier,pid=process.pid,argv=argv,kind=kind)
        return identifier

    def wait(self, job_id, seconds=0):
        self.require_research()
        job = self.jobs[job_id]
        try:
            job['process'].wait(timeout=min(20,max(0,float(seconds))))
        except subprocess.TimeoutExpired:
            pass
        code = job['process'].poll()
        if code is not None and not job['reported']:
            self.record(event='completed',job_id=job_id,exit_code=code,kind=job['kind'])
            job['reported']=True
        def tail(p):
            with p.open('rb') as stream:
                stream.seek(max(0,p.stat().st_size-40000))
                return stream.read().decode('utf-8',errors='replace')
        return dict(cwd=str(self.task),path_base='task',job_id=job_id,pid=job['process'].pid,status='running' if code is None else 'completed',
            exit_code=code,stdout=tail(job['stdout']),stderr=tail(job['stderr']))

    def python(self, script, args=None, runtime='main'):
        self.require_research()
        p = self.path(script)
        if not p.is_relative_to(self.workspace/'scripts') or p.suffix.lower()!='.py':
            raise ValueError('Inspection scripts must be saved under workspace/scripts/*.py; no inline code or shell')
        argv = args or []
        if not isinstance(argv,list) or not all(isinstance(x,str) for x in argv):
            raise ValueError('args must be a JSON array of strings')
        python = self.packet['training']['command'][0]
        if runtime!='main':
            raise ValueError('Unknown runtime')
        runner = Path(__file__).with_name('inspection_worker.py')
        return self.wait(self.start([python,'-B',str(runner),str(p),*argv],'inspection'),0)

    def training(self):
        self.require_research()
        if self.training_job:
            old = self.wait(self.training_job,0)
            if old['status']=='running':
                return old
        receipt = self.workspace/'training/receipt.json'
        if self.training_job and not receipt.exists():
            raise RuntimeError('Original training ended without a receipt; never replay')
        if receipt.exists():
            saved=json.loads(receipt.read_text(encoding='utf-8'))
            source=hashlib.sha256((self.workspace/'train.py').read_bytes()).hexdigest()
            if saved.get('status')=='success' or saved.get('source_sha256')==source:
                return dict(status='receipt_available',receipt=saved,training_relaunched=False)
        # Fail closed if this MCP instance lost ownership of a previous process.
        journal=[json.loads(s) for s in self.journal.read_text(encoding='utf-8').splitlines()] if self.journal.exists() else []
        started={x['job_id'] for x in journal if x.get('event')=='started' and x.get('kind')=='training'}
        ended={x['job_id'] for x in journal if x.get('event')=='completed'}
        if started-ended:
            raise RuntimeError('Original training has unresolved process identity; never replay')
        self.training_job=self.start(self.packet['training']['command'],'training')
        return self.wait(self.training_job,0)

    def training_status(self):
        self.require_research()
        return self.wait(self.start(self.packet['training']['status_command'],'status'),20)

    def close(self):
        for identifier,job in self.jobs.items():
            if job['process'].poll() is None:
                if os.name=='nt':
                    subprocess.run(['taskkill.exe','/PID',str(job['process'].pid),'/T','/F'],
                        capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    job['process'].terminate()
                self.record(event='interrupted',job_id=identifier,pid=job['process'].pid,kind=job['kind'])
            else:
                self.wait(identifier,0)

def serve(transport):
    for raw in sys.stdin:
        request=json.loads(raw)
        method=request.get('method')
        if 'id' not in request:
            continue
        response=dict(jsonrpc='2.0',id=request['id'])
        if method=='initialize':
            transport.record(event='protocol',method='initialize')
            response['result']=dict(protocolVersion=request.get('params',{}).get('protocolVersion','2024-11-05'),
                capabilities={'tools':{}},serverInfo={'name':'simple-loop-transport','version':'1.0'},
                instructions=('Expert role: read exposed evidence and write only the staged research.md and plan.json with files. '
                    'No processes, training, network, or runtime control.' if transport.review else INSTRUCTIONS))
        elif method=='tools/list':
            transport.record(event='protocol',method='tools/list')
            response['result']={'tools':transport.available_tools()}
        elif method=='ping':
            response['result']={}
        elif method=='tools/call':
            params=request['params']
            previous_jobs=set(transport.jobs)
            try:
                name=params['name']
                if name not in {t['name'] for t in transport.available_tools()}:
                    raise ValueError('Unknown tool')
                value=getattr(transport,name)(**params.get('arguments',{}))
                if isinstance(value,dict):
                    value.setdefault('cwd',str(transport.task))
                    value.setdefault('path_base','input.json directory')
                response['result']={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],
                    'isError':value.get('exit_code') not in (None,0)}
            except Exception as exc:
                transport.record(event='rejected',tool=params.get('name'),
                    action=(params.get('arguments') or {}).get('action'),
                    process_started=bool(set(transport.jobs)-previous_jobs),error=f'{type(exc).__name__}: {exc}')
                response['result']={'content':[{'type':'text','text':json.dumps(dict(status='rejected',
                    process_started=bool(set(transport.jobs)-previous_jobs),
                    error=f'{type(exc).__name__}: {exc}'),ensure_ascii=False)}],'isError':True}
        else:
            response['error']={'code':-32601,'message':'Method not found'}
        print(json.dumps(response,ensure_ascii=False),flush=True)

if __name__=='__main__':
    args=argparse.ArgumentParser()
    args.add_argument('--input',required=True)
    args.add_argument('--project-root',required=True)
    options=args.parse_args()
    sys.path.insert(0,options.project_root)
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    instance=Transport(options.input)
    try:
        serve(instance)
    finally:
        instance.close()
