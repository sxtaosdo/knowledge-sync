"""Go native SQLite -> Python outbox E2E, without sockets, Git, models or keys."""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import uuid

from index_verifier import text_hash
from native_canary_adapter import NativeCanaryAdapter
from offline_writer import Ledger, Worker, UnknownOutcome, receipt_valid
from sync_service import snapshot_state
from test_native_canary import source, manifest, content_loader


class StdioTransport:
    def __init__(self, executable, root):
        env={key:value for key,value in os.environ.items() if key.upper() in
             ('SYSTEMROOT','WINDIR','PATH','COMSPEC','PATHEXT')}
        env.update(TEMP=str(root),TMP=str(root))
        env['PATH']=r'D:\services\weknora\tools\msys64\ucrt64\bin;'+env.get('PATH','')
        self.proc=subprocess.Popen([str(executable)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True,encoding='utf-8',env=env,cwd=root)
        self.lines=queue.Queue();self.writes=0;self.lose=False
        def reader():
            for line in self.proc.stdout:self.lines.put(line)
        threading.Thread(target=reader,daemon=True).start()
    def request(self,method,path='',body=None):
        self.proc.stdin.write(json.dumps(dict(method=method,path=path,body=body),ensure_ascii=False)+'\n')
        self.proc.stdin.flush()
        try:value=json.loads(self.lines.get(timeout=10))
        except queue.Empty:
            self.proc.kill();raise RuntimeError('synthetic_fixture_timeout') from None
        if value.get('status')==404:return None
        if value.get('success') is not True:raise RuntimeError('synthetic_fixture_rejected:'+str(value.get('error')))
        if method=='POST' and path.endswith('/sync-canary/operations'):
            self.writes+=1
            if self.lose:self.lose=False;raise UnknownOutcome('synthetic_lost_post_reply')
        return value
    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=5)
        self.proc.stdout.close();self.proc.stderr.close()


def run(executable):
    with tempfile.TemporaryDirectory(prefix='native-e2e-',dir=Path(__file__).parent) as temporary:
        root=Path(temporary);sources=[source(root,'local'),source(root,'official')]
        plans=[];targets=[]
        for s,commit in zip(sources,('1'*40,'3'*40)):
            ledger=Ledger(s.binding,s.ledger);job=ledger.enqueue(manifest(s,commit))
            ops=[json.loads(row['payload']) for row in ledger.operations(job)]
            targets.append(dict(tenantId=1,kbId=s.binding.kb_id,
                name='shidai3-dev-canary' if s.binding.channel=='local' else 'shidai3-official-canary',
                channel=s.binding.channel,documents=[dict(contentSHA256=text_hash(content_loader(s,op)),desired=op['desired']) for op in ops]))
            plans.append((s,ledger,job,commit))
        transport=StdioTransport(executable,root)
        receipt=[]
        try:
            transport.request('INIT',body=dict(policy=dict(schemaVersion=1,instanceId=str(uuid.uuid4()),
                expiresAt=int(time.time())+3600,targets=targets)))
            for number,(s,ledger,job,commit) in enumerate(plans):
                adapter=NativeCanaryAdapter(s,transport,synthetic=True,content_loader=content_loader)
                clock=[time.time()];worker=Worker(ledger,adapter,clock=lambda:clock[0],expected_scope=s.scope)
                if number==0:transport.lose=True
                transitions=[]
                for _ in range(24):
                    transitions.append(worker.step());clock[0]+=3
                    if ledger.checkpoint==commit:break
                    time.sleep(.02)
                assert ledger.checkpoint==commit,transitions
                rows=[json.loads(row[0]) for row in ledger.db.execute('SELECT value FROM index_receipts')]
                assert len(rows)==2 and all(receipt_valid(r) and r['synthetic'] is True and r.get('backendInventoryHash') for r in rows)
                ledger.close()
                reopened=Ledger(s.binding,s.ledger)
                assert reopened.checkpoint==commit
                reopened.close()
                state=snapshot_state(s)
                assert state['pending']==0 and state['queuedCount']==0 and state['indexedDocuments']==0
                result=transport.request('POST','/api/v1/knowledge-bases/'+s.binding.kb_id+'/hybrid-search',
                    dict(query='Synthetic'))['data']
                assert result['total']==2 and result['modelCalls']==0 and result['keywordOnly'] is True
                receipt.append(dict(channel=s.binding.channel,completedSHA=commit,reloadedCompletedSHA=state['checkpoint'],
                    receiptCount=2,allReceiptsSynthetic=True,ftsKeywordMatches=2,transitions=transitions,
                    pending=0,queuedCount=0,indexedDocuments=0,
                    receiptHashes=[r['receiptHash'] for r in rows],backendInventoryHashes=[r['backendInventoryHash'] for r in rows]))
            assert transport.writes==4,transport.writes
        finally:
            for _,ledger,_,_ in plans:ledger.close()
            transport.close()
        return dict(schemaVersion=1,ok=True,fixtureExecutableSHA256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            synthetic=True,actualNativeSQLiteFTS=True,productionUsable=False,sourceTracks=receipt,
            postOperations=4,lostPostReplyRecoveredWithoutDuplicate=True,sockets=0,modelCalls=0,
            credentialsAccessed=False,realServiceDBAccessed=False,realDocumentsImported=False,
            schedulerRegistered=False,completedAt=time.time())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    evidence=run(Path(args.fixture).resolve())
    Path(args.output).write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(evidence))
