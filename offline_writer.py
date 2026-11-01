"""Durable metadata-only outbox. Network and credentials belong to a gated adapter."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

from knowledge_sync import excluded_path, safe_path, provenance_valid

APP_ID = 0x4B534F46
SCHEMA = 3


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def receipt_valid(receipt):
    return (isinstance(receipt,dict) and receipt.get('state')=='ready' and
            receipt.get('receiptHash')==digest({k:v for k,v in receipt.items() if k!='receiptHash'}))


def sha(value):
    return isinstance(value,str) and bool(re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',value))


@dataclass(frozen=True)
class Binding:
    repo_id: str
    channel: str
    tenant_id: str
    kb_id: str
    owner: str = 'fixture-sync'
    includes: tuple = ('docs',)

    def __post_init__(self):
        if any(not isinstance(v,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}',v)
               for v in (self.repo_id,self.tenant_id,self.kb_id,self.owner)):
            raise ValueError('invalid_binding_identity')
        if self.channel not in ('local','official') or not self.includes:
            raise ValueError('invalid_binding')
        for prefix in self.includes:
            if excluded_path(safe_path(prefix)+'/scope.md'):
                raise ValueError('invalid_include')

    def key(self,path):
        return f'{self.repo_id}:{self.channel}:{path}'

    def contains(self,path):
        safe_path(path)
        return not excluded_path(path) and any(path==p or path.startswith(p+'/') for p in self.includes)


class Retryable(Exception):
    """Request is authoritatively known not to have been applied."""
    def __init__(self,retry_after=0):
        self.retry_after=retry_after


class UnknownOutcome(Exception):
    pass


class AuthorizationDenied(Exception):
    pass


class WriterDisabled(Exception):
    pass


class DisabledAdapter:
    production_ready=False
    def guard(self):
        raise WriterDisabled('production_writer_disabled')


class FakeAdapter:
    """Synthetic metadata/index simulator; never represents a production-ready adapter."""
    production_ready=False
    completion_evidence='synthetic-index'
    def __init__(self):
        self.documents,self.receipts,self.ready={}, {},set()
        self.submissions=[]

    def guard(self):
        pass

    def inspect(self,document_id):
        return dict(self.documents[document_id]) if document_id in self.documents else None

    def find_source(self,source_key):
        return [dict(d) for d in self.documents.values() if d['source_key']==source_key]

    def submit(self,op):
        self.submissions.append(op['id'])
        if op['id'] in self.receipts:
            raise UnknownOutcome('duplicate_submission')
        document_id=op['document_id'] or 'fixture-doc-'+op['id'][:16]
        self.receipts[op['id']]={'document_id':document_id,'task_id':'fixture-task-'+op['id']}
        return dict(self.receipts[op['id']])

    def finalize(self,op):
        if op['kind']!='delete':
            self.documents[op['document_id']]=dict(op['desired'],document_id=op['document_id'])

    def publish(self,op):
        pass

    def reconcile_publish(self,op):
        return {'state':'accepted'} if op['document_id'] in self.documents else {'state':'ambiguous'}

    def reconcile(self,op):
        receipt=self.receipts.get(op['id'])
        return dict(state='accepted',**receipt) if receipt else {'state':'not_applied'}

    def poll(self,op):
        ready=op['id'] in self.ready
        if ready and op['kind']=='delete':
            self.documents.pop(op['document_id'],None)
        return {'state':'ready' if ready else 'pending','document_id':op['document_id'],
                'operation_id':op['id'],'indexed_blob_sha':op['desired'].get('blob_sha'),
                'expected_chunks':2,'indexed_chunks':2 if ready else 0}


class Ledger:
    def __init__(self,binding,path=':memory:'):
        self.binding=binding
        expected=canonical(asdict(binding))
        if path!=':memory:':
            raw=Path(path)
            if raw.is_symlink() or not raw.name.endswith(('.sync.sqlite3','.offline.sqlite3')):
                raise ValueError('explicit_sync_ledger_path_required')
            path=raw.resolve()
            if path.exists():
                probe=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
                try:
                    if (probe.execute('PRAGMA application_id').fetchone()[0]!=APP_ID or
                        probe.execute('PRAGMA user_version').fetchone()[0]!=SCHEMA):
                        raise ValueError('foreign_or_unsupported_ledger')
                    row=probe.execute('SELECT value FROM binding WHERE id=1').fetchone()
                    if not row or row[0]!=expected:
                        raise ValueError('ledger_target_binding_changed')
                finally:
                    probe.close()
        self.db=sqlite3.connect(path,timeout=1,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript(f'''
          PRAGMA application_id={APP_ID}; PRAGMA user_version={SCHEMA};
          CREATE TABLE IF NOT EXISTS binding(id INTEGER PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS checkpoint(id INTEGER PRIMARY KEY,value TEXT);
          CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,source_commit TEXT,
             status TEXT NOT NULL,cancel_requested INTEGER NOT NULL DEFAULT 0,scope_hash TEXT);
          CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,
             ordinal INTEGER,payload TEXT NOT NULL,payload_sha TEXT NOT NULL,state TEXT NOT NULL,
             attempts INTEGER NOT NULL DEFAULT 0,polls INTEGER NOT NULL DEFAULT 0,
             reconciles INTEGER NOT NULL DEFAULT 0,ready_at REAL NOT NULL DEFAULT 0,
             document_id TEXT,task_id TEXT,error TEXT);
          CREATE TABLE IF NOT EXISTS mappings(path TEXT PRIMARY KEY,value TEXT NOT NULL,
             tombstone INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS lease(id INTEGER PRIMARY KEY,holder TEXT,expires REAL);
          CREATE TABLE IF NOT EXISTS index_receipts(operation_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,value TEXT NOT NULL);
        ''')
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO binding VALUES(1,?)',(expected,))
            if self.db.execute('SELECT value FROM binding WHERE id=1').fetchone()[0]!=expected:
                raise ValueError('ledger_target_binding_changed')
            self.db.execute('INSERT OR IGNORE INTO checkpoint VALUES(1,NULL)')

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def close(self):
        self.db.close()

    @property
    def checkpoint(self):
        return self.db.execute('SELECT value FROM checkpoint WHERE id=1').fetchone()[0]

    def mappings(self):
        return {r['path']:json.loads(r['value']) for r in self.db.execute('SELECT * FROM mappings WHERE tombstone=0')}

    def operations(self,batch_id):
        return [dict(r) for r in self.db.execute('SELECT * FROM operations WHERE batch_id=? ORDER BY ordinal',(batch_id,))]

    def enqueue(self,manifest,*,physical_deletions=(),confirmed_renames=(),max_deletes=0,max_delete_fraction=0):
        with self.transaction():
            return self._enqueue(manifest,physical_deletions,confirmed_renames,max_deletes,max_delete_fraction)

    def _enqueue(self,m,physical_deletions,confirmed_renames,max_deletes,max_delete_fraction):
        claimed=m.get('manifest_sha256')
        if claimed!=digest({k:v for k,v in m.items() if k!='manifest_sha256'}):
            raise ValueError('manifest_digest_mismatch')
        b=self.binding
        if (m.get('schema_version')!=1 or m.get('mode')!='dry-run' or m.get('repo_id')!=b.repo_id or
            m.get('channel')!=b.channel or sorted(m.get('includes',[]))!=sorted(b.includes) or
            m.get('kb_write_performed') is not False or m.get('model_calls_performed') is not False or
            m.get('worktree_files_read') is not False or m.get('remote_refresh_performed') is not False or
            not sha(m.get('source_commit'))):
            raise ValueError('manifest_binding_mismatch')
        if b.channel=='official' and m.get('official_ref_freshness')!='unknown_cached_ref':
            raise ValueError('invalid_official_freshness')
        batch_id=digest({'binding':asdict(b),'manifest':claimed})
        row=self.db.execute('SELECT status FROM batches WHERE id=?',(batch_id,)).fetchone()
        if row:
            if row[0]=='cancelled':
                raise ValueError('cancelled_batch_requires_new_plan')
            return batch_id
        if self.db.execute("SELECT 1 FROM batches WHERE status IN ('pending','blocked')").fetchone():
            raise ValueError('prior_batch_unresolved')
        if m.get('baseline_commit')!=self.checkpoint:
            raise ValueError('baseline_checkpoint_mismatch')
        docs={}
        for doc in m['documents']:
            path=doc['path']
            state=doc.get('document_state')=='Accepted' or (doc.get('document_state')=='explicitly_allowed_unmarked' and m.get('allow_unmarked') is True)
            if (not b.contains(path) or path in docs or not state or doc.get('repo_id')!=b.repo_id or
                doc.get('channel')!=b.channel or doc.get('source_key')!=b.key(path) or
                not sha(doc.get('source_commit')) or not provenance_valid(doc,m) or not sha(doc.get('source_blob_sha'))):
                raise ValueError('invalid_document_metadata')
            docs[path]=doc
        old=self.mappings()
        removed,added=set(old)-set(docs),set(docs)-set(old)
        renames=dict(confirmed_renames)
        candidates={(c.get('from'),c.get('path')) for c in m['changes'] if c.get('action')=='rename_candidate'}
        if len(set(renames.values()))!=len(renames):
            raise ValueError('ambiguous_rename')
        for new,previous in renames.items():
            if (new not in added or previous not in removed or (previous,new) not in candidates or
                old[previous]['blob_sha']!=docs[new]['source_blob_sha']):
                raise ValueError('unconfirmed_rename_identity')
        removed-=set(renames.values())
        added-=set(renames)
        if set(physical_deletions)!=removed:
            raise ValueError('physical_deletion_proof_required')
        if (type(max_deletes) is not int or max_deletes<0 or type(max_delete_fraction) not in (int,float) or
            not math.isfinite(max_delete_fraction) or not 0<=max_delete_fraction<=1):
            raise ValueError('invalid_delete_threshold')
        if removed and (len(removed)>max_deletes or len(removed)/len(old)>max_delete_fraction):
            raise ValueError('delete_threshold_exceeded')
        def desired_for(path):
            doc=docs.get(path)
            return dict(owner=b.owner,repo_id=b.repo_id,channel=b.channel,tenant_id=b.tenant_id,
                kb_id=b.kb_id,path=path,source_key=b.key(path),source_commit=doc['source_commit'] if doc else m['source_commit'],
                parent_commit=m['source_commit'],source_repo_id=doc.get('source_repo_id',b.repo_id) if doc else b.repo_id,
                source_path=doc.get('source_path',path) if doc else path,pin_chain=doc.get('pin_chain',[]) if doc else [],
                processing_policy_hash=m.get('processing_policy_hash'),
                blob_sha=doc['source_blob_sha'] if doc else None,title=doc['title'] if doc else None)
        plans=[('rename',new,previous) for new,previous in sorted(renames.items())]
        plans += [('add',p,None) for p in sorted(added)]
        plans += [('modify',p,p) for p in sorted(set(old)&set(docs)) if old[p]['blob_sha']!=docs[p]['source_blob_sha']]
        plans += [('provenance',p,p) for p in sorted(set(old)&set(docs))
                  if old[p]['blob_sha']==docs[p]['source_blob_sha'] and
                  any(old[p].get(k)!=v for k,v in desired_for(p).items())]
        plans += [('delete',p,p) for p in sorted(removed)]
        self.db.execute('INSERT INTO batches(id,source_commit,status,scope_hash) VALUES(?,?,?,?)',(batch_id,m['source_commit'],'pending',m.get('approval_scope')))
        for ordinal,(kind,path,previous) in enumerate(plans):
            desired=desired_for(path)
            prior_receipt=None
            if previous:
                prior=self.db.execute('SELECT value FROM index_receipts WHERE operation_id=?',(old[previous]['operation_id'],)).fetchone()
                prior_receipt=json.loads(prior[0]) if prior else None
            op=dict(batch_id=batch_id,kind=kind,path=path,old_path=previous,previous=old.get(previous),
                desired=desired,document_id=old[previous]['document_id'] if previous else None,
                previous_receipt=prior_receipt)
            op['id']=digest(op)
            desired['operation_id']=op['id']
            payload=canonical(op)
            self.db.execute('INSERT INTO operations(id,batch_id,ordinal,payload,payload_sha,state,document_id) VALUES(?,?,?,?,?,?,?)',
                (op['id'],batch_id,ordinal,payload,hashlib.sha256(payload.encode()).hexdigest(),'queued',op['document_id']))
        self._finish(batch_id)
        return batch_id

    def cancel(self,batch_id):
        with self.transaction():
            row=self.db.execute('SELECT status FROM batches WHERE id=?',(batch_id,)).fetchone()
            if not row or row[0]=='done':
                raise ValueError('completed_or_unknown_batch_cannot_cancel')
            self.db.execute('UPDATE batches SET cancel_requested=1 WHERE id=?',(batch_id,))
            # Unknown or in-flight effects must still reconcile; never erase them.
            self.db.execute("UPDATE operations SET state='cancelled' WHERE batch_id=? AND state IN ('queued','retry')",(batch_id,))
            self._finish(batch_id)

    def _finish(self,batch_id):
        batch=self.db.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
        states=[r[0] for r in self.db.execute('SELECT state FROM operations WHERE batch_id=?',(batch_id,))]
        if all(s in ('done','cancelled') for s in states):
            status='cancelled' if batch['cancel_requested'] else 'done'
            self.db.execute('UPDATE batches SET status=? WHERE id=?',(status,batch_id))
            if status=='done':
                self.db.execute('UPDATE checkpoint SET value=? WHERE id=1',(batch['source_commit'],))


class Worker:
    def __init__(self,ledger,adapter=None,*,clock=time.time,max_attempts=3,max_polls=20,max_reconciles=3,expected_scope=None):
        self.ledger,self.adapter=ledger,adapter or DisabledAdapter()
        self.clock=clock
        self.expected_scope=expected_scope
        self.max_attempts,self.max_polls,self.max_reconciles=max_attempts,max_polls,max_reconciles

    def _fence(self,holder):
        row=self.ledger.db.execute('SELECT * FROM lease WHERE id=1').fetchone()
        if not row or row['holder']!=holder or row['expires']<=self.clock():
            raise WriterDisabled('worker_lease_lost')

    def _update(self,holder,sql,values=()):
        with self.ledger.transaction():
            self._fence(holder)
            self.ledger.db.execute(sql,values)

    def _start_write(self,row,holder,new_state):
        # Cancellation and worker transitions share this lock; stale snapshots cannot undo cancel.
        with self.ledger.transaction():
            self._fence(holder)
            current=self.ledger.db.execute('SELECT state FROM operations WHERE id=?',(row['id'],)).fetchone()
            batch=self.ledger.db.execute('SELECT cancel_requested FROM batches WHERE id=?',(row['batch_id'],)).fetchone()
            if not current or current['state']!=row['state']:
                return False
            if batch['cancel_requested']:
                if row['state']=='awaiting_metadata':
                    self.ledger.db.execute("UPDATE operations SET state='blocked',error='cancelled_draft_requires_operator' WHERE id=?",(row['id'],))
                    self.ledger.db.execute("UPDATE batches SET status='blocked' WHERE id=?",(row['batch_id'],))
                elif row['state'] in ('queued','retry'):
                    self.ledger.db.execute("UPDATE operations SET state='cancelled' WHERE id=?",(row['id'],))
                    self.ledger._finish(row['batch_id'])
                return False
            self.ledger.db.execute('UPDATE operations SET state=?,attempts=attempts+1 WHERE id=? AND state=?',(new_state,row['id'],row['state']))
            return True

    def _retry_or_cancel(self,row,holder,ready_at,block_code=None):
        # Only called after authoritative proof that a write was not applied.
        with self.ledger.transaction():
            self._fence(holder)
            batch=self.ledger.db.execute('SELECT cancel_requested FROM batches WHERE id=?',(row['batch_id'],)).fetchone()
            if batch['cancel_requested']:
                self.ledger.db.execute("UPDATE operations SET state='cancelled',error=NULL WHERE id=?",(row['id'],))
                self.ledger._finish(row['batch_id'])
                return 'cancelled'
            if block_code:
                self.ledger.db.execute("UPDATE operations SET state='blocked',error=? WHERE id=?",(block_code,row['id']))
                self.ledger.db.execute("UPDATE batches SET status='blocked' WHERE id=?",(row['batch_id'],))
                return 'blocked'
            self.ledger.db.execute("UPDATE operations SET state='retry',ready_at=?,error='request_not_applied' WHERE id=?",(ready_at,row['id']))
            return 'retry'

    def step(self,now=None):
        self.adapter.guard()
        now=self.clock() if now is None else now
        db,holder=self.ledger.db,uuid.uuid4().hex
        with self.ledger.transaction():
            db.execute('INSERT INTO lease VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET holder=excluded.holder,expires=excluded.expires WHERE lease.expires<=?',(holder,now+30,now))
            if db.execute('SELECT holder FROM lease WHERE id=1').fetchone()[0]!=holder:
                raise WriterDisabled('worker_lease_busy')
        try:
            batch=db.execute("SELECT * FROM batches WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
            if not batch:
                return 'idle'
            if self.expected_scope is not None and batch['scope_hash']!=self.expected_scope:
                raise WriterDisabled('queued_policy_scope_changed')
            row=db.execute("SELECT * FROM operations WHERE batch_id=? AND state NOT IN ('done','cancelled') ORDER BY ordinal LIMIT 1",(batch['id'],)).fetchone()
            if not row or row['ready_at']>now:
                return 'waiting'
            if hashlib.sha256(row['payload'].encode()).hexdigest()!=row['payload_sha']:
                return self._block(row,'outbox_payload_corrupt',holder)
            op=json.loads(row['payload'])
            op.update(document_id=row['document_id'],task_id=row['task_id'])
            mutating=False
            try:
                if row['state'] in ('submitting','unknown'):
                    result=self.adapter.reconcile(op)
                    self._update(holder,'UPDATE operations SET reconciles=reconciles+1 WHERE id=?',(row['id'],))
                    if result['state']=='accepted':
                        return self._accept(row,result,holder)
                    if result['state']=='not_applied':
                        return self._retry_or_cancel(row,holder,now+2**row['attempts'],
                            'retry_limit' if row['attempts']>=self.max_attempts else None)
                    if result['state']=='ambiguous' or row['reconciles']+1>=self.max_reconciles:
                        return self._block(row,'reconciliation_requires_operator',holder)
                    self._update(holder,"UPDATE operations SET state='unknown',ready_at=? WHERE id=?",(now+2**row['reconciles'],row['id']))
                    return 'unknown'
                if row['state']=='publishing':
                    result=self.adapter.reconcile_publish(op)
                    self._update(holder,'UPDATE operations SET reconciles=reconciles+1 WHERE id=?',(row['id'],))
                    if result.get('state')=='accepted':
                        self._update(holder,"UPDATE operations SET state='awaiting_index',error=NULL WHERE id=?",(row['id'],))
                        return 'awaiting_index'
                    if row['reconciles']+1>=self.max_reconciles:
                        return self._block(row,'publish_outcome_requires_operator',holder)
                    self._update(holder,'UPDATE operations SET ready_at=? WHERE id=?',(now+2,row['id']))
                    return 'publishing'
                if row['state']=='awaiting_metadata':
                    if batch['cancel_requested']:
                        return self._block(row,'cancelled_draft_requires_operator',holder)
                    self.adapter.finalize(op)
                    if not self._start_write(row,holder,'publishing'):
                        return 'cancelled_or_blocked'
                    mutating=True
                    self.adapter.publish(op)
                    self._update(holder,"UPDATE operations SET state='awaiting_index',error=NULL WHERE id=?",(row['id'],))
                    return 'awaiting_index'
                if row['state']=='awaiting_index':
                    result=self.adapter.poll(op)
                    if result.get('state')=='unknown':
                        return self._block(row,'index_evidence_unknown',holder)
                    actual=self.adapter.inspect(op['document_id'])
                    confirmed=result.get('state')=='ready' and result.get('document_id')==op['document_id']
                    if op['kind']=='delete':
                        confirmed=confirmed and actual is None
                    else:
                        confirmed=confirmed and actual is not None and all(actual.get(k)==v for k,v in op['desired'].items())
                        if self.adapter.completion_evidence=='synthetic-index':
                            confirmed=confirmed and result.get('operation_id')==op['id'] and result.get('indexed_blob_sha')==op['desired']['blob_sha'] and result.get('expected_chunks',0)>0 and result.get('indexed_chunks')==result['expected_chunks']
                        else:
                            confirmed=confirmed and result.get('parse_status')=='completed' and result.get('chunks_observed',0)>0
                    if confirmed:
                        receipt=result.get('index_receipt')
                        if receipt is None and (self.adapter.completion_evidence=='synthetic-index' or getattr(self.adapter,'synthetic',False)):
                            receipt=dict(state='ready',synthetic=True,operationId=op['id'],documentId=op['document_id'],
                                blobSHA=op['desired']['blob_sha'],parentCommit=op['desired']['parent_commit'])
                            receipt['receiptHash']=digest(receipt)
                        if (not receipt_valid(receipt) or receipt.get('operationId')!=op['id'] or
                            receipt.get('documentId')!=op['document_id']):
                            return self._block(row,'index_receipt_invalid',holder)
                        with self.ledger.transaction():
                            self._fence(holder)
                            if op['kind']=='rename':
                                db.execute('DELETE FROM mappings WHERE path=?',(op['old_path'],))
                            db.execute('INSERT INTO mappings VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET value=excluded.value,tombstone=excluded.tombstone',(op['path'],canonical(dict(op['desired'],document_id=op['document_id'])),int(op['kind']=='delete')))
                            db.execute("UPDATE operations SET state='done',error=NULL WHERE id=?",(row['id'],))
                            db.execute('INSERT INTO index_receipts VALUES(?,?,?)',(op['id'],op['document_id'],canonical(receipt)))
                            self.ledger._finish(row['batch_id'])
                        return 'done'
                    if result.get('state')=='failed' or row['polls']+1>=self.max_polls:
                        return self._block(row,'index_or_delete_confirmation_failed',holder)
                    self._update(holder,'UPDATE operations SET polls=polls+1,ready_at=? WHERE id=?',(now+2,row['id']))
                    return 'awaiting_index'
                # Ownership reads may fail, but such failures never imply a submitted write.
                self._guard(op)
                if not self._start_write(row,holder,'submitting'):
                    return 'cancelled_or_blocked'
                mutating=True
                return self._accept(row,self.adapter.submit(op),holder)
            except WriterDisabled:
                raise
            except AuthorizationDenied:
                return self._block(row,'authorization_denied',holder)
            except Retryable as exc:
                attempts=db.execute('SELECT attempts FROM operations WHERE id=?',(row['id'],)).fetchone()[0]
                if row['state'] in ('submitting','unknown'):
                    # A failed reconciliation read cannot prove the original write absent.
                    return self._block(row,'reconciliation_read_failed',holder)
                if row['state']=='publishing':
                    return self._block(row,'publish_reconciliation_read_failed',holder)
                if row['state'] in ('awaiting_metadata','awaiting_index'):
                    self._update(holder,'UPDATE operations SET ready_at=? WHERE id=?',(now+2,row['id']))
                    return row['state']
                if attempts>=self.max_attempts or not math.isfinite(exc.retry_after) or exc.retry_after<0:
                    return self._retry_or_cancel(row,holder,now,'retry_limit')
                return self._retry_or_cancel(row,holder,now+max(2**attempts,exc.retry_after))
            except Exception:
                if row['state'] in ('publishing','awaiting_metadata') and mutating:
                    # Publish increments a server version: never blind replay after an uncertain result.
                    self._update(holder,"UPDATE operations SET state='publishing',ready_at=?,error='unknown_publish_result' WHERE id=?",(now+2,row['id']))
                    return 'publishing'
                if mutating or row['state'] in ('unknown','submitting'):
                    self._update(holder,"UPDATE operations SET state='unknown',ready_at=?,error='unknown_adapter_result' WHERE id=?",(now+2,row['id']))
                    return 'unknown'
                return self._block(row,'adapter_read_or_confirmation_failed',holder)
        finally:
            with self.ledger.transaction():
                db.execute('DELETE FROM lease WHERE holder=?',(holder,))

    def _block(self,row,code,holder):
        with self.ledger.transaction():
            self._fence(holder)
            self.ledger.db.execute("UPDATE operations SET state='blocked',error=? WHERE id=?",(code,row['id']))
            self.ledger.db.execute("UPDATE batches SET status='blocked' WHERE id=?",(row['batch_id'],))
        return 'blocked'

    def _guard(self,op):
        if op['kind']=='add':
            if self.adapter.find_source(op['desired']['source_key']):
                raise AuthorizationDenied()
        else:
            actual=self.adapter.inspect(op['document_id'])
            if not actual or any(actual.get(k)!=v for k,v in op['previous'].items()):
                raise AuthorizationDenied()

    def _accept(self,row,receipt,holder):
        if not receipt.get('document_id') or row['document_id'] and row['document_id']!=receipt['document_id']:
            raise UnknownOutcome()
        state='awaiting_index' if json.loads(row['payload'])['kind']=='delete' else 'awaiting_metadata'
        self._update(holder,'UPDATE operations SET state=?,document_id=?,task_id=?,error=NULL WHERE id=?',(state,receipt['document_id'],receipt.get('task_id'),row['id']))
        return state
