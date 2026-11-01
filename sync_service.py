"""Source-scoped job planning and status. A UI call never executes network writes."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import time

from knowledge_sync import build_manifest, document_location, resolve_commit
from offline_writer import APP_ID, SCHEMA, Ledger, Worker, canonical, digest, receipt_valid
from source_registry import Registry,require_current_policy
from weknora_adapter import NetworkTransport, RestAdapter

ROOT=Path(__file__).resolve().parent
REGISTRY_PATH=ROOT/'sources.json'


def snapshot_state(source):
    if not source.ledger.exists():
        return {'checkpoint':None,'pending':0,'queuedCount':0,'indexedDocuments':0,
                'errors':[],'jobs':[],'mappings':{},'receipts':[]}
    if source.ledger.is_symlink():
        raise ValueError('INVALID_LEDGER')
    db=sqlite3.connect(source.ledger.as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('PRAGMA application_id').fetchone()[0]!=APP_ID or db.execute('PRAGMA user_version').fetchone()[0]!=SCHEMA:
            raise ValueError('INVALID_LEDGER')
        if db.execute('SELECT value FROM binding WHERE id=1').fetchone()[0]!=canonical(asdict(source.binding)):
            raise ValueError('LEDGER_BINDING_CHANGED')
        indexed_documents=0
        for row in db.execute("SELECT r.value FROM index_receipts r JOIN mappings m ON json_extract(m.value,'$.operation_id')=r.operation_id WHERE m.tombstone=0"):
            receipt=json.loads(row[0])
            if receipt_valid(receipt) and receipt.get('synthetic') is False:
                indexed_documents+=1
        return dict(checkpoint=db.execute('SELECT value FROM checkpoint WHERE id=1').fetchone()[0],
            pending=db.execute("SELECT count(*) FROM operations WHERE state NOT IN ('done','cancelled')").fetchone()[0],
            queuedCount=db.execute("SELECT count(*) FROM operations WHERE state='queued'").fetchone()[0],
            indexedDocuments=indexed_documents,
            errors=[r[0] for r in db.execute('SELECT DISTINCT error FROM operations WHERE error IS NOT NULL')],
            jobs=[dict(r) for r in db.execute('SELECT id,status,source_commit FROM batches ORDER BY rowid DESC LIMIT 10')],
            receipts=[dict(operationId=r['operation_id'],documentId=r['document_id'],receiptHash=json.loads(r['value'])['receiptHash'],
                           synthetic=json.loads(r['value']).get('synthetic',False))
                      for r in db.execute('SELECT * FROM index_receipts ORDER BY rowid DESC LIMIT 10')],
            mappings={r['path']:json.loads(r['value']) for r in db.execute('SELECT * FROM mappings WHERE tombstone=0')})
    finally:
        db.close()


class Service:
    def __init__(self,registry=None,*,clock=time.time,adapter_factory=None,synthetic=False):
        self.registry=registry or Registry.load(REGISTRY_PATH)
        self.clock=clock
        self.adapter_factory=adapter_factory
        self.synthetic=synthetic

    def status(self,source):
        state=snapshot_state(source)
        target=None
        errors=[{'code':code.upper(),'message':'Sync job requires attention.'} for code in state['errors']]
        try:
            target=resolve_commit(source.repo,source.ref)
        except (ValueError,OSError):
            errors.append({'code':'SOURCE_UNAVAILABLE','message':'Registered local Git objects are unavailable.'})
        approved=source.approved(self.clock())
        enabled=approved and source.writer_enabled and source.network_enabled
        # Presence check only here. An enabled native adapter's authenticated
        # capability probe uses the process credential locally without exposing it.
        credential=bool(source.credential_env and source.credential_env in os.environ)
        writer_ready=False
        readiness_failed=False
        if enabled and credential and self.adapter_factory is not None and not self.synthetic:
            try:
                adapter=self.adapter_factory(source)
                writer_ready=adapter.production_ready and not getattr(adapter,'synthetic',False)
                if writer_ready and hasattr(adapter,'runtime_probe'):
                    writer_ready=adapter.runtime_probe() is True
            except Exception:
                writer_ready=False
                readiness_failed=True
        if not approved:
            errors.append({'code':'POLICY_NOT_APPROVED','message':'Exact source and writer policy await Owner approval.'})
        elif not enabled:
            errors.append({'code':'WRITER_DISABLED','message':'Writer remains disabled.'})
        elif not credential:
            errors.append({'code':'WRITER_CREDENTIAL_MISSING','message':'A separate scoped writer credential is required.'})
        elif readiness_failed:
            errors.append({'code':'WRITER_READINESS_UNAVAILABLE','message':'Writer readiness check failed; inspect approved nonsecret diagnostics.'})
        elif not writer_ready and not self.synthetic:
            errors.append({'code':'INDEX_ATTESTATION_REQUIRED','message':'Approved index verification is required before production writes.'})
        freshness=('blocked' if state['errors'] else 'syncing' if state['pending'] else
            'unknown' if source.binding.channel=='official' or target is None else
            'current' if target==state['checkpoint'] else 'stale')
        if not enabled:
            freshness='disabled'
        elif not writer_ready and not self.synthetic:
            freshness='blocked'
        return dict(schemaVersion=1,sourceId=source.source_id,channel=source.binding.channel,
            repoId=source.binding.repo_id,configured=True,approved=approved,enabled=enabled,
            writerReady=writer_ready,targetSHA=target,completedSHA=state['checkpoint'],
            ready=writer_ready,queued=state['queuedCount']>0,queuedCount=state['queuedCount'],
            indexed=bool(target and target==state['checkpoint'] and state['pending']==0 and state['mappings'] and
                         state['indexedDocuments']==len(state['mappings'])),indexedDocuments=state['indexedDocuments'],
            freshness=freshness,remoteFreshness='unknown_cached_ref' if source.binding.channel=='official' else None,
            pending=state['pending'],errors=errors,jobs=state['jobs'],scopeHash=source.scope,
            schedulerEnabled=enabled and source.schedule_enabled and source.interval_seconds>=60,
            intervalSeconds=source.interval_seconds,systemAutostartRegistered=False,indexReceipts=state['receipts'])

    def plan(self,source):
        state=snapshot_state(source)
        manifest=build_manifest(source.repo,source.binding.repo_id,source.binding.channel,
            source.binding.includes,source.ref,state['checkpoint'],source.allow_unmarked,source.excludes,source.gitlinks)
        if manifest['unread_gitlinks']:
            raise ValueError('PINNED_SOURCE_NOT_REGISTERED')
        manifest['approval_scope']=source.scope
        manifest['processing_policy_hash']=digest(dict(processing=source.processing,indexes=source.required_indexes))
        manifest['manifest_sha256']=digest({k:v for k,v in manifest.items() if k!='manifest_sha256'})
        current={d['path'] for d in manifest['documents']}
        removed=set(state['mappings'])-current
        renames={c['path']:c['from'] for c in manifest['changes'] if c['action']=='rename_candidate'}
        renames={new:old for new,old in renames.items() if old in removed and state['mappings'][old]['blob_sha']==next(d['source_blob_sha'] for d in manifest['documents'] if d['path']==new)}
        # Prove actual absence in the immutable target tree, not eligibility-set disappearance.
        for path in removed:
            if document_location(source.repo,manifest['source_commit'],path,source.binding.repo_id,source.gitlinks) is not None:
                raise ValueError('RETENTION_REVIEW_REQUIRED')
        deletions=sorted(removed-set(renames.values()))
        if deletions and (len(deletions)>source.max_deletes or len(deletions)/len(state['mappings'])>source.max_delete_fraction):
            raise ValueError('DELETE_POLICY_EXCEEDED')
        return manifest,deletions,renames

    def enqueue(self,source):
        source.require_writer(self.clock())
        if not self.synthetic:
            require_current_policy(source)
            if not source.credential_env or source.credential_env not in os.environ:
                raise ValueError('WRITER_CREDENTIAL_MISSING')
            if self.adapter_factory is None:
                raise ValueError('INDEX_ATTESTATION_REQUIRED')
            adapter=self.adapter_factory(source)
            if not adapter.production_ready or getattr(adapter,'synthetic',False):
                raise ValueError('INDEX_ATTESTATION_REQUIRED')
            adapter.guard()
        manifest,deletions,renames=self.plan(source)
        ledger=Ledger(source.binding,source.ledger)
        try:
            job=ledger.enqueue(manifest,physical_deletions=deletions,confirmed_renames=renames,
                max_deletes=source.max_deletes,max_delete_fraction=source.max_delete_fraction)
            return dict(jobId=job,jobState=ledger.db.execute('SELECT status FROM batches WHERE id=?',(job,)).fetchone()[0],
                targetSHA=manifest['source_commit'],planHash=manifest['manifest_sha256'],writePerformed=False,
                queuedOnly=True,sourceId=source.source_id,channel=source.binding.channel)
        finally:
            ledger.close()

    def work_once(self,source):
        source.require_writer(self.clock())
        if not self.synthetic:
            require_current_policy(source)
        adapter=(self.adapter_factory(source) if self.adapter_factory else
            RestAdapter(source,NetworkTransport(source)))
        adapter.guard()
        ledger=Ledger(source.binding,source.ledger)
        try:
            return Worker(ledger,adapter,clock=self.clock,expected_scope=source.scope).step()
        finally:
            ledger.close()

    def dispatch(self,operation,request):
        if not isinstance(request,dict) or set(request)-{'sourceId','channel'}:
            raise ValueError('INVALID_REQUEST')
        if operation not in ('status','dry-run','sync-once','scheduler-status'):
            raise ValueError('INVALID_OPERATION')
        if not request and operation in ('status','scheduler-status'):
            sources=[self.status(s) for s in self.registry.sources.values()]
            return dict(configured=bool(sources),approved=bool(sources) and all(s['approved'] for s in sources),
                enabled=any(s['enabled'] for s in sources),writerReady=any(s['writerReady'] for s in sources),
                schedulerEnabled=any(s['schedulerEnabled'] for s in sources),
                systemAutostartRegistered=False,sources=sources,
                sourceId=None,channel=None,repoId=None,targetSHA=None,completedSHA=None,
                freshness='disabled' if not sources else 'unknown',pending=sum(s['pending'] for s in sources),
                errors=[] if sources else [{'code':'POLICY_NOT_APPROVED','message':'No source is enrolled.'}])
        if set(request)!={'sourceId','channel'}:
            raise ValueError('INVALID_REQUEST')
        source=self.registry.get(request['sourceId'],request['channel'])
        if operation in ('status','scheduler-status'):
            return self.status(source)
        if operation=='dry-run':
            manifest,deleted,renamed=self.plan(source)
            return dict(manifest=manifest,physicalDeletions=deleted,confirmedRenames=renamed,
                status=self.status(source),writePerformed=False)
        return self.enqueue(source)
