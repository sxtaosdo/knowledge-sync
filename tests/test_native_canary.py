"""First-round native wire validation; mocks never produce production receipts."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from index_verifier import text_hash
from native_canary_adapter import NativeCanaryAdapter, PATHS, PROVIDER_ID
from offline_writer import Binding, Ledger, Worker, digest, UnknownOutcome, WriterDisabled
from source_registry import Source
from sync_service import Service
from weknora_adapter import PROCESS_KEYS


def manifest(source, commit='1'*40):
    value = dict(schema_version=1, mode='dry-run', repo_id='shidai3', channel=source.binding.channel,
        source_commit=commit, baseline_commit=None, includes=list(PATHS), changes=[],
        worktree_files_read=False, remote_refresh_performed=False, kb_write_performed=False,
        model_calls_performed=False, approval_scope=source.scope, processing_policy_hash='fixture-policy',
        official_ref_freshness='unknown_cached_ref' if source.binding.channel=='official' else None,
        documents=[dict(path=path,title='Synthetic '+path,repo_id='shidai3',channel=source.binding.channel,
            source_key=source.binding.key(path),source_commit=commit,source_blob_sha='2'*40,
            document_state='Accepted') for path in PATHS])
    value['manifest_sha256']=digest(value)
    return value


def source(root, channel='local'):
    value=Source('shidai3-canary',root/'unused',Binding('shidai3',channel,'1',
        '00000000-0000-0000-0000-000000000001' if channel=='local' else '00000000-0000-0000-0000-000000000002',
        includes=PATHS),'HEAD' if channel=='local' else 'refs/remotes/origin/main',root/f'{channel}.sync.sqlite3',
        writer_enabled=True,network_enabled=True,index_verifier=PROVIDER_ID,required_indexes=('keyword',),
        processing=tuple(sorted((key,False) for key in PROCESS_KEYS)))
    return replace(value,approved_scope=value.scope,approval_expires=1e12)


def content_loader(source,op):
    return 'Synthetic keyword 中文 '+op['desired']['path']


class FakeNative:
    """A response validator fixture. Real FTS is tested by the native Go fixture."""
    def __init__(self,source):
        self.source=source;self.operations={};self.docs={};self.writes=0;self.lose=False;self.corrupt=False
    def request(self,method,path,body=None):
        prefix='/api/v1/knowledge-bases/'+self.source.binding.kb_id
        if path.endswith('/capabilities'):
            return dict(success=True,data=dict(enabled=True,providerId=PROVIDER_ID,tenantId=1,
                kbId=self.source.binding.kb_id,channel=self.source.binding.channel,expiresAt=1e12,
                documentPaths=list(PATHS),keywordOnly=True,deleteAllowed=False,firstAddsOnly=True))
        if method=='POST':
            self.writes+=1
            key=body['operationId'];doc='doc-'+key;task='task-'+key
            result=dict(operationId=key,documentId=doc,taskId=task,state='queued')
            self.operations[key]=dict(operation=result,document=None,chunks=[],
                proof=dict(schemaVersion=1,providerId=PROVIDER_ID,state='pending',observedAt=100,
                           binding=dict(body['desired'],document_id=doc)))
            self.operations[key]['request']=copy.deepcopy(body)
            if self.lose: self.lose=False;raise UnknownOutcome()
            return dict(success=True,data=result)
        if '/sync-canary/operations/' in path:
            data=copy.deepcopy(self.operations.get(path.rsplit('/',1)[1]))
            if data is None:return None
            if self.corrupt:data['proof']['binding']['blob_sha']='f'*40
            return dict(success=True,data=data)
        if path.startswith('/api/v1/knowledge/'):
            doc=self.docs.get(path.rsplit('/',1)[1]);return dict(success=True,data=doc) if doc else None
        if path.startswith(prefix+'/knowledge?'):
            return dict(success=True,data=list(self.docs.values()),total=len(self.docs))
        raise AssertionError((method,path))
    def complete(self,key):
        data=self.operations[key];request=data['request'];doc=data['operation']['documentId'];task=data['operation']['taskId']
        body=request['content'];chunks=[dict(id='chunk-'+key,knowledge_id=doc,content=body)]
        document=dict(id=doc,knowledge_base_id=self.source.binding.kb_id,title=request['desired']['title'],
            parse_status='completed',metadata=dict(content=body,version=1,status='publish'),custom_metadata=request['desired'])
        self.docs[doc]=document;data.update(document=document,chunks=chunks);data['operation']['state']='done'
        hashes=[dict(id=chunks[0]['id'],contentSHA256=text_hash(body))]
        data['proof'].update(state='ready',taskState='done',taskId=task,documentVersion=1,
            contentSHA256=text_hash(body),documentChunkCount=1,backendInventoryHash='a'*64,
            indexes=dict(keyword=dict(state='done',documentVersion=1,totalChunks=1,chunks=hashes)))


class NativeClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='native-client-',dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.source=source(self.root)
        self.transport=FakeNative(self.source);self.now=100
        self.adapter=NativeCanaryAdapter(self.source,self.transport,synthetic=True,content_loader=content_loader,clock=lambda:100)
        self.ledger=Ledger(self.source.binding,self.source.ledger);self.addCleanup(self.ledger.close)
        self.job=self.ledger.enqueue(manifest(self.source));self.worker=Worker(self.ledger,self.adapter,clock=lambda:self.now,expected_scope=self.source.scope)
    def step(self):
        result=self.worker.step();self.now+=3;return result
    def current(self):
        row=next(r for r in self.ledger.operations(self.job) if r['state']!='done')
        op=json.loads(row['payload']);op.update(document_id=row['document_id'],task_id=row['task_id']);return op
    def test_two_receipts_and_reopened_checkpoint(self):
        for n in range(2):
            self.assertEqual(self.step(),'awaiting_metadata');self.assertEqual(self.step(),'awaiting_index')
            self.assertEqual(self.step(),'awaiting_index');self.assertIsNone(self.ledger.checkpoint)
            self.transport.complete(self.current()['id']);self.assertEqual(self.step(),'done')
            self.assertEqual(self.ledger.checkpoint,None if n==0 else '1'*40)
        self.assertEqual(self.transport.writes,2)
        rows=self.ledger.db.execute('SELECT value FROM index_receipts').fetchall()
        self.assertTrue(all(json.loads(r[0])['synthetic'] for r in rows))
        self.ledger.close();reopened=Ledger(self.source.binding,self.source.ledger);self.addCleanup(reopened.close)
        self.assertEqual(reopened.checkpoint,'1'*40)
    def test_lost_post_reply_recovers_durable_operation_without_second_post(self):
        self.transport.lose=True;self.assertEqual(self.step(),'unknown')
        self.assertEqual(self.step(),'awaiting_metadata');self.assertEqual(self.step(),'awaiting_index')
        self.transport.complete(self.current()['id']);self.assertEqual(self.step(),'done')
        self.assertEqual(self.transport.writes,1)
    def test_wrong_binding_blocks_without_completed_sha(self):
        self.step();self.step();self.transport.corrupt=True
        self.assertEqual(self.step(),'blocked');self.assertIsNone(self.ledger.checkpoint)
    def test_schedule_or_expanded_source_cannot_instantiate_native_writer(self):
        for value in [replace(self.source,schedule_enabled=True),replace(self.source,max_deletes=1),
                      replace(self.source,required_indexes=('keyword','vector'))]:
            with self.assertRaises(WriterDisabled):NativeCanaryAdapter(value,self.transport,synthetic=True)
    def test_corrupt_index_proof_is_unknown(self):
        self.step();self.step();op=self.current();self.transport.complete(op['id'])
        self.transport.operations[op['id']]['proof']['indexes']['keyword']['totalChunks']=2
        self.assertEqual(self.step(),'blocked');self.assertIsNone(self.ledger.checkpoint)
    def test_status_unavailable_runtime_probe_never_reports_ready(self):
        value=replace(self.source,credential_env='KNOWLEDGE_SYNC_LOCAL_CANARY_KEY')
        value=replace(value,approved_scope=value.scope)
        class Unavailable:
            production_ready=True
            synthetic=False
            def runtime_probe(self):raise WriterDisabled('fixture-unavailable')
        with patch.dict('os.environ',{value.credential_env:'synthetic-fixture-only'}),patch('sync_service.resolve_commit',return_value='1'*40):
            status=Service(adapter_factory=lambda s:Unavailable(),clock=lambda:100).status(value)
        self.assertFalse(status['writerReady']);self.assertFalse(status['ready']);self.assertFalse(status['indexed'])
        self.assertTrue(status['queued']);self.assertEqual(status['queuedCount'],2)
        self.assertEqual([error['code'] for error in status['errors']],['WRITER_READINESS_UNAVAILABLE'])
        self.assertEqual(status['freshness'],'blocked')
        self.assertNotIn('fixture-unavailable',json.dumps(status))

    def test_status_adapter_failure_reports_readiness_without_raw_error(self):
        value=replace(self.source,credential_env='KNOWLEDGE_SYNC_LOCAL_CANARY_KEY')
        value=replace(value,approved_scope=value.scope)
        def unavailable(source):raise RuntimeError('synthetic-sensitive-fixture')
        with patch.dict('os.environ',{value.credential_env:'synthetic-fixture-only'}),patch('sync_service.resolve_commit',return_value='1'*40):
            status=Service(adapter_factory=unavailable,clock=lambda:100).status(value)
        self.assertEqual([error['code'] for error in status['errors']],['WRITER_READINESS_UNAVAILABLE'])
        self.assertFalse(status['writerReady']);self.assertFalse(status['ready'])
        self.assertEqual(status['freshness'],'blocked')
        self.assertNotIn('synthetic-sensitive-fixture',json.dumps(status))

    def test_status_missing_attestation_keeps_existing_error_and_skips_probe(self):
        value=replace(self.source,credential_env='KNOWLEDGE_SYNC_LOCAL_CANARY_KEY')
        value=replace(value,approved_scope=value.scope)
        class MissingAttestation:
            production_ready=False
            synthetic=False
            def runtime_probe(self):raise AssertionError('unattested adapter must not probe')
        for factory in (None,lambda source:MissingAttestation()):
            with self.subTest(has_adapter=factory is not None):
                with patch.dict('os.environ',{value.credential_env:'synthetic-fixture-only'}),patch('sync_service.resolve_commit',return_value='1'*40):
                    status=Service(adapter_factory=factory,clock=lambda:100).status(value)
                self.assertEqual([error['code'] for error in status['errors']],['INDEX_ATTESTATION_REQUIRED'])
                self.assertFalse(status['writerReady']);self.assertFalse(status['ready'])
                self.assertEqual(status['freshness'],'blocked')



if __name__=='__main__':unittest.main()
