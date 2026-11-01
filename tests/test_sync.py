from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_dry_run as fixture
from companion_api import handle
from offline_writer import (Binding,Ledger,Worker,FakeAdapter,UnknownOutcome,Retryable,
                            WriterDisabled,digest,canonical)
from source_registry import Source,Registry,require_current_policy
from sync_service import Service,snapshot_state
from scheduler import Scheduler
from index_verifier import IndexVerifier
from weknora_adapter import RestAdapter,NetworkTransport,immutable_content


class Fixture(unittest.TestCase):
    setUp=fixture.DryRunTests.setUp
    def tearDown(self):
        self.doCleanups() # Close SQLite handles before Windows removes the fixture directory.
        fixture.DryRunTests.tearDown(self)
    git=fixture.DryRunTests.git
    write=fixture.DryRunTests.write
    commit=fixture.DryRunTests.commit
    sha=fixture.DryRunTests.sha
    scan=fixture.DryRunTests.scan

    def source(self,channel='local',**changes):
        source=Source('fixture',self.repo,Binding('fixture',channel,'fixture-tenant','fixture-kb'),
            'HEAD' if channel=='local' else 'refs/remotes/origin/main',
            Path(self.directory.name)/'fixture.sync.sqlite3',writer_enabled=True,network_enabled=True,
            base_url='http://127.0.0.1:9',credential_env='KNOWLEDGE_SYNC_FIXTURE',
            processing=tuple(sorted((k,False) for k in ('summary_enabled','enable_multimodel','graph_enabled',
                'question_generation_enabled','extract_enabled'))))
        source=replace(source,**changes)
        return replace(source,approved_scope=source.scope,approval_expires=1e12)

    def ledger(self):
        ledger=Ledger(self.source().binding)
        self.addCleanup(ledger.close)
        return ledger

    def worker(self,ledger,adapter=None):
        self.now=[10.0]
        adapter=adapter or FakeAdapter()
        return Worker(ledger,adapter,clock=lambda:self.now[0]),adapter

    def drain(self,worker,adapter,limit=40):
        for _ in range(limit):
            adapter.ready.update(adapter.receipts)
            outcome=worker.step()
            self.now[0]+=3
            if outcome in ('idle','blocked'):
                return outcome
        self.fail('fixture worker did not terminate')


class OutboxTests(Fixture):
    def test_cancel_during_authoritative_reconciliation_reaches_cancelled_terminal(self):
        ledger=self.ledger(); job=ledger.enqueue(self.scan())
        class CancelDuringReconcile(FakeAdapter):
            def submit(self,op):
                self.submissions.append(op['id'])
                raise UnknownOutcome()
            def reconcile(self,op):
                ledger.cancel(job)
                return {'state':'not_applied'}
        worker,adapter=self.worker(ledger,CancelDuringReconcile())
        self.assertEqual(worker.step(),'unknown'); self.now[0]+=3
        self.assertEqual(worker.step(),'cancelled')
        self.assertEqual(ledger.operations(job)[0]['state'],'cancelled')
        self.assertEqual(ledger.db.execute('SELECT status FROM batches WHERE id=?',(job,)).fetchone()[0],'cancelled')
        self.assertEqual(worker.step(),'idle')
        self.assertEqual(len(adapter.submissions),1)
        self.assertIsNone(ledger.checkpoint)

    def test_cancel_during_known_submit_rejection_does_not_leave_retry_pending(self):
        ledger=self.ledger(); job=ledger.enqueue(self.scan())
        class CancelDuringSubmit(FakeAdapter):
            def submit(self,op):
                self.submissions.append(op['id'])
                ledger.cancel(job)
                raise Retryable()
        worker,adapter=self.worker(ledger,CancelDuringSubmit())
        self.assertEqual(worker.step(),'cancelled')
        self.assertEqual(ledger.operations(job)[0]['state'],'cancelled')
        self.assertEqual(worker.step(),'idle')
        self.assertEqual(len(adapter.submissions),1)
        self.assertIsNone(ledger.checkpoint)

    def test_cancel_during_ownership_read_cannot_restore_cancelled_job(self):
        ledger=self.ledger(); job=ledger.enqueue(self.scan())
        class CancelDuringRead(FakeAdapter):
            def find_source(self,key):
                ledger.cancel(job)
                return []
        worker,adapter=self.worker(ledger,CancelDuringRead())
        self.assertEqual(worker.step(),'cancelled_or_blocked')
        self.assertEqual(adapter.submissions,[])
        self.assertEqual(ledger.operations(job)[0]['state'],'cancelled')
        self.assertEqual(ledger.db.execute('SELECT status FROM batches WHERE id=?',(job,)).fetchone()[0],'cancelled')
        self.assertIsNone(ledger.checkpoint)

    def test_cancel_during_metadata_update_prevents_publish(self):
        ledger=self.ledger(); job=ledger.enqueue(self.scan())
        class CancelDuringMetadata(FakeAdapter):
            published=False
            def finalize(self,op):
                super().finalize(op)
                ledger.cancel(job)
            def publish(self,op):
                self.published=True
        worker,adapter=self.worker(ledger,CancelDuringMetadata())
        self.assertEqual(worker.step(),'awaiting_metadata')
        self.assertEqual(worker.step(),'cancelled_or_blocked')
        self.assertFalse(adapter.published)
        self.assertEqual(ledger.operations(job)[0]['error'],'cancelled_draft_requires_operator')
        self.assertEqual(ledger.db.execute('SELECT status FROM batches WHERE id=?',(job,)).fetchone()[0],'blocked')
        self.assertIsNone(ledger.checkpoint)

    def test_checkpoint_waits_for_positive_confirmation_and_idempotent_batch(self):
        ledger=self.ledger()
        manifest=self.scan()
        job=ledger.enqueue(manifest)
        self.assertEqual(job,ledger.enqueue(manifest))
        worker,adapter=self.worker(ledger)
        self.assertEqual(worker.step(),'awaiting_metadata')
        self.assertIsNone(ledger.checkpoint)
        self.assertEqual(worker.step(),'awaiting_index')
        self.assertEqual(worker.step(),'awaiting_index')
        self.assertIsNone(ledger.checkpoint)
        self.now[0]+=3
        adapter.ready.update(adapter.receipts)
        self.assertEqual(worker.step(),'done')
        self.assertEqual(ledger.checkpoint,self.first)
        self.assertEqual(len(adapter.submissions),1)

    def test_unknown_creation_reconciles_without_blind_resend(self):
        class LostReply(FakeAdapter):
            def submit(self,op):
                super().submit(op)
                raise UnknownOutcome()
        ledger=self.ledger()
        ledger.enqueue(self.scan())
        worker,adapter=self.worker(ledger,LostReply())
        self.assertEqual(worker.step(),'unknown')
        self.now[0]+=3
        self.assertEqual(worker.step(),'awaiting_metadata')
        self.assertEqual(self.drain(worker,adapter),'idle')
        self.assertEqual(len(adapter.submissions),1)
        self.assertEqual(ledger.checkpoint,self.first)

    def test_ambiguous_creation_blocks_without_resend(self):
        class Ambiguous(FakeAdapter):
            def submit(self,op):
                self.submissions.append(op['id'])
                raise UnknownOutcome()
            def reconcile(self,op):
                return {'state':'ambiguous'}
        ledger=self.ledger(); ledger.enqueue(self.scan())
        worker,adapter=self.worker(ledger,Ambiguous())
        self.assertEqual(worker.step(),'unknown'); self.now[0]+=3
        self.assertEqual(worker.step(),'blocked')
        self.assertEqual(len(adapter.submissions),1)
        self.assertIsNone(ledger.checkpoint)

    def test_known_rejection_retries_bounded(self):
        class Rejected(FakeAdapter):
            def submit(self,op):
                self.submissions.append(op['id'])
                raise Retryable(1)
        ledger=self.ledger(); ledger.enqueue(self.scan())
        worker,adapter=self.worker(ledger,Rejected())
        for expected in ('retry','retry','blocked'):
            self.assertEqual(worker.step(),expected); self.now[0]+=20
        self.assertEqual(len(adapter.submissions),3)

    def test_reconciliation_rate_limit_never_retries_original_write(self):
        class ReconcileError(FakeAdapter):
            def submit(self,op):
                self.submissions.append(op['id']); raise UnknownOutcome()
            def reconcile(self,op):
                raise Retryable()
        ledger=self.ledger(); ledger.enqueue(self.scan())
        worker,adapter=self.worker(ledger,ReconcileError())
        self.assertEqual(worker.step(),'unknown'); self.now[0]+=3
        self.assertEqual(worker.step(),'blocked')
        self.assertEqual(len(adapter.submissions),1)

    def test_read_failure_does_not_create_unknown_write(self):
        class ReadFailure(FakeAdapter):
            def find_source(self,key):
                raise OSError('synthetic-private-error')
        ledger=self.ledger(); job=ledger.enqueue(self.scan())
        worker,adapter=self.worker(ledger,ReadFailure())
        self.assertEqual(worker.step(),'blocked')
        self.assertEqual(ledger.operations(job)[0]['attempts'],0)
        self.assertEqual(adapter.submissions,[])

    def test_rename_keeps_identity_and_delete_requires_threshold(self):
        ledger=self.ledger(); ledger.enqueue(self.scan())
        worker,adapter=self.worker(ledger); self.drain(worker,adapter)
        original=ledger.mappings()['docs/a.md']['document_id']
        self.git('mv','docs/a.md','docs/new.md'); self.commit()
        manifest=self.scan(baseline=self.first)
        ledger.enqueue(manifest,confirmed_renames={'docs/new.md':'docs/a.md'})
        self.drain(worker,adapter)
        self.assertEqual(ledger.mappings()['docs/new.md']['document_id'],original)
        self.git('rm','docs/new.md'); self.commit()
        manifest=self.scan(baseline=ledger.checkpoint)
        with self.assertRaisesRegex(ValueError,'delete_threshold'):
            ledger.enqueue(manifest,physical_deletions=['docs/new.md'])
        job=ledger.enqueue(manifest,physical_deletions=['docs/new.md'],max_deletes=1,max_delete_fraction=1)
        self.assertEqual(self.drain(worker,adapter),'idle')
        self.assertEqual(ledger.mappings(),{})
        self.assertIsNone(adapter.inspect(original))
        self.assertEqual(ledger.operations(job)[0]['state'],'done')

    def test_foreign_document_not_modified(self):
        ledger=self.ledger(); ledger.enqueue(self.scan())
        worker,adapter=self.worker(ledger); self.drain(worker,adapter)
        doc=ledger.mappings()['docs/a.md']['document_id']
        adapter.documents[doc]['owner']='other-owner'
        self.write('docs/a.md','# Changed\n\n状态: Accepted\nnew'); self.commit()
        ledger.enqueue(self.scan(baseline=ledger.checkpoint))
        self.assertEqual(worker.step(),'blocked')
        self.assertEqual(len(adapter.submissions),1)

    def test_cancellation_does_not_publish_created_draft_or_advance_checkpoint(self):
        ledger=self.ledger(); job=ledger.enqueue(self.scan())
        worker,adapter=self.worker(ledger)
        self.assertEqual(worker.step(),'awaiting_metadata')
        ledger.cancel(job)
        self.assertEqual(worker.step(),'blocked')
        self.assertIsNone(ledger.checkpoint)
        self.assertEqual(adapter.documents,{})

    def test_lease_busy_and_expiry_preserve_unknown_for_reconciliation(self):
        ledger=self.ledger(); job=ledger.enqueue(self.scan())
        worker,adapter=self.worker(ledger)
        ledger.db.execute('INSERT INTO lease VALUES(1,?,?)',('other',100))
        with self.assertRaisesRegex(WriterDisabled,'lease_busy'):
            worker.step()
        ledger.db.execute('DELETE FROM lease')
        original=adapter.submit
        def delayed(op):
            result=original(op); self.now[0]+=31; return result
        adapter.submit=delayed
        with self.assertRaisesRegex(WriterDisabled,'lease_lost'):
            worker.step()
        self.assertEqual(ledger.operations(job)[0]['state'],'submitting')
        adapter.submit=original
        self.assertEqual(worker.step(),'awaiting_metadata')
        self.assertEqual(len(adapter.submissions),1)

    def test_binding_rejection_does_not_modify_existing_ledger(self):
        path=Path(self.directory.name)/'fixture.sync.sqlite3'
        ledger=Ledger(self.source().binding,path); ledger.close()
        before=hashlib.sha256(path.read_bytes()).digest()
        with self.assertRaisesRegex(ValueError,'binding_changed'):
            Ledger(replace(self.source().binding,kb_id='other-kb'),path)
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).digest())

    def test_crash_recovery_and_policy_fence(self):
        source=self.source(); ledger=Ledger(source.binding,source.ledger)
        manifest=self.scan(); manifest['approval_scope']=source.scope
        manifest['manifest_sha256']=digest({k:v for k,v in manifest.items() if k!='manifest_sha256'})
        ledger.enqueue(manifest)
        worker,adapter=self.worker(ledger); self.assertEqual(worker.step(),'awaiting_metadata')
        ledger.close(); ledger=Ledger(source.binding,source.ledger); self.addCleanup(ledger.close)
        worker=Worker(ledger,adapter,clock=lambda:self.now[0],expected_scope='changed')
        with self.assertRaisesRegex(WriterDisabled,'scope_changed'):
            worker.step()
        worker.expected_scope=source.scope
        self.assertEqual(self.drain(worker,adapter),'idle')

    def test_manifest_corruption_and_schema_are_rejected(self):
        ledger=self.ledger(); manifest=self.scan()
        manifest['documents'][0]['path']='docs/secrets/a.md'
        with self.assertRaisesRegex(ValueError,'digest_mismatch'):
            ledger.enqueue(manifest)
        manifest['manifest_sha256']=digest({k:v for k,v in manifest.items() if k!='manifest_sha256'})
        with self.assertRaisesRegex(ValueError,'metadata'):
            ledger.enqueue(manifest)
        manifest=self.scan(); manifest['schema_version']=2
        manifest['manifest_sha256']=digest({k:v for k,v in manifest.items() if k!='manifest_sha256'})
        with self.assertRaisesRegex(ValueError,'binding_mismatch'):
            ledger.enqueue(manifest)

    def test_disabled_adapter_cannot_process_jobs(self):
        ledger=self.ledger(); ledger.enqueue(self.scan())
        with self.assertRaises(WriterDisabled):
            Worker(ledger).step()


class ServiceTests(Fixture):
    def write_registry(self,source,**changes):
        item=dict(sourceId=source.source_id,repo=str(source.repo),repoId=source.binding.repo_id,
            channel=source.binding.channel,ref=source.ref,includes=list(source.binding.includes),
            excludes=list(source.excludes),tenantId=source.binding.tenant_id,kbId=source.binding.kb_id,
            owner=source.binding.owner,ledger=str(source.ledger),allowUnmarked=source.allow_unmarked,
            maxDeletes=source.max_deletes,maxDeleteFraction=source.max_delete_fraction,
            writerEnabled=source.writer_enabled,approvedScope=source.approved_scope,
            approvalExpires=source.approval_expires,networkEnabled=source.network_enabled,
            baseUrl=source.base_url,credentialEnv=source.credential_env,processing=dict(source.processing),
            scheduleEnabled=source.schedule_enabled,intervalSeconds=source.interval_seconds,
            gitlinks=[dict(path=p.path,repo=str(p.repo),repoId=p.repo_id) for p in source.gitlinks],
            indexVerifier=source.index_verifier,requiredIndexes=list(source.required_indexes))
        item.update(changes)
        path=Path(self.directory.name)/'registered-fixture.json'
        path.write_text(json.dumps({'schemaVersion':1,'sources':[item]}),encoding='utf-8')
        return path

    def test_registry_file_revocation_and_scope_change_reject_cached_source(self):
        source=self.source()
        path=self.write_registry(source)
        self.assertEqual(require_current_policy(source,path).scope,source.scope)
        for changes,code in [({'approvedScope':''},'POLICY_NOT_APPROVED'),
            ({'writerEnabled':False},'WRITER_DISABLED'),({'networkEnabled':False},'NETWORK_DISABLED'),
            ({'approvalExpires':0},'POLICY_NOT_APPROVED'),({'includes':['other-docs']},'POLICY_SCOPE_CHANGED')]:
            with self.subTest(changes=changes):
                self.write_registry(source,**changes)
                with self.assertRaisesRegex(ValueError,code):
                    require_current_policy(source,path)

    def test_production_transport_reloads_policy_before_credentials_and_network(self):
        source=self.source(); path=self.write_registry(source)
        transport=NetworkTransport(source)
        self.write_registry(source,writerEnabled=False)
        class CredentialsMustNotBeRead:
            def get(self,key):
                raise AssertionError('credential access after revocation')
        def connection_must_not_open(*args,**kwargs):
            raise AssertionError('network access after revocation')
        with patch('weknora_adapter.require_current_policy',lambda s:require_current_policy(s,path)), \
             patch('weknora_adapter.os',SimpleNamespace(environ=CredentialsMustNotBeRead())), \
             patch('weknora_adapter.http',SimpleNamespace(client=SimpleNamespace(HTTPConnection=connection_must_not_open))):
            with self.assertRaisesRegex(ValueError,'WRITER_DISABLED'):
                transport.request('POST','/fixture',{'fixture':True})

    def test_rest_adapter_reloads_policy_before_request(self):
        source=self.source(index_verifier='fixture-index',required_indexes=('keyword',)); path=self.write_registry(source)
        transport=MockRest(source.binding.kb_id)
        verifier=IndexVerifier(source,SimpleNamespace(production_ready=True,provider_id='fixture-index'))
        adapter=RestAdapter(source,transport,index_confirm=verifier)
        with patch('weknora_adapter.require_current_policy',lambda s:require_current_policy(s,path)), \
             patch('weknora_adapter.os',SimpleNamespace(environ={source.credential_env:'synthetic-only'})):
            adapter.find_source(source.binding.key('docs/a.md'))
            count=len(transport.calls)
            self.write_registry(source,networkEnabled=False)
            with self.assertRaisesRegex(ValueError,'NETWORK_DISABLED'):
                adapter.find_source(source.binding.key('docs/a.md'))
            self.assertEqual(len(transport.calls),count)

    def test_resident_worker_reloads_registry_before_each_tick(self):
        import sync_worker
        source=self.source(); path=self.write_registry(source)
        # The fixture only invokes --once; fake service asserts registry reload before scheduler tick.
        service=self.service(source)
        service.registry=Registry()
        class CheckingScheduler:
            def __init__(self,actual_service):
                self.service=actual_service
            def tick(self):
                self_source=self.service.registry.get(source.source_id,source.binding.channel)
                if self_source.scope!=source.scope:
                    raise AssertionError('worker did not reload the registered policy')
                return []
        from io import StringIO
        with patch('sync_worker.Service',return_value=service),patch('sync_worker.REGISTRY_PATH',path), \
             patch('sync_worker.Scheduler',CheckingScheduler),patch.object(sys,'argv',['sync_worker.py','--once']), \
             patch.object(sys,'stdout',StringIO()):
            self.assertEqual(sync_worker.main(),0)

    def service(self,source=None):
        return Service(Registry([source or self.source()]),clock=lambda:10,synthetic=True)

    def test_job_submission_is_only_durable_enqueue(self):
        source=self.source(); service=self.service(source)
        response=handle('sync-once',json.dumps({'sourceId':'fixture','channel':'local'}),service)
        self.assertTrue(response['ok']); self.assertTrue(response['data']['queuedOnly'])
        self.assertFalse(response['data']['writePerformed'])
        self.assertEqual(snapshot_state(source)['pending'],1)
        self.assertIsNone(snapshot_state(source)['checkpoint'])
        state=service.status(source)
        self.assertFalse(state['writerReady'])
        self.assertEqual(state['freshness'],'syncing')

    def test_unapproved_source_and_frontend_injections_fail_before_ledger_creation(self):
        source=replace(self.source(),approved_scope='')
        service=self.service(source)
        for raw,code in [('{"sourceId":"fixture","channel":"local"}','POLICY_NOT_APPROVED'),
            ('{"sourceId":"fixture","channel":"local","repo":"D:/private"}','INVALID_REQUEST'),
            ('{"sourceId":"fixture","channel":"local","approved":true}','INVALID_REQUEST'),
            ('{"sourceId":"fixture","sourceId":"other","channel":"local"}','INVALID_REQUEST')]:
            response=handle('sync-once',raw,service)
            self.assertFalse(response['ok']); self.assertEqual(response['error']['code'],code)
        self.assertFalse(source.ledger.exists())

    def test_status_is_readonly_and_empty_registry_reports_off(self):
        source=self.source(); service=self.service(source)
        state=service.status(source)
        self.assertFalse(state['writerReady']); self.assertFalse(source.ledger.exists())
        empty=handle('status','{}',Service(Registry()))
        self.assertTrue(empty['ok']); self.assertFalse(empty['data']['configured'])
        self.assertFalse(empty['data']['enabled']); self.assertFalse(empty['data']['schedulerEnabled'])

    def test_official_status_never_claims_remote_latest(self):
        source=self.source('official'); service=self.service(source)
        self.assertEqual(service.status(source)['freshness'],'unknown')
        self.assertEqual(service.status(source)['remoteFreshness'],'unknown_cached_ref')

    def test_filtered_or_draft_document_is_not_treated_as_physical_deletion(self):
        source=self.source(max_deletes=10,max_delete_fraction=1)
        service=self.service(source); service.enqueue(source)
        ledger=Ledger(source.binding,source.ledger); self.addCleanup(ledger.close)
        worker,adapter=self.worker(ledger); self.drain(worker,adapter)
        self.write('docs/a.md','# Draft now\n\n状态: Draft\n'); self.commit()
        with self.assertRaisesRegex(ValueError,'RETENTION_REVIEW_REQUIRED'):
            service.plan(source)
        self.assertEqual(len(adapter.documents),1)

    def test_explicit_exclude_is_not_read_and_changes_scope(self):
        source=self.source(excludes=('docs/a.md',))
        manifest,deleted,renamed=self.service(source).plan(source)
        self.assertEqual(manifest['documents'],[])
        self.assertEqual(manifest['excluded'][0]['reason'],'explicitly_excluded_scope')
        self.assertNotEqual(source.scope,self.source().scope)

    def test_scheduler_disabled_by_default_and_expiry_rechecked(self):
        source=self.source(); service=self.service(source)
        scheduler=Scheduler(service,clock=lambda:10)
        self.assertEqual(scheduler.tick(),[]); self.assertFalse(source.ledger.exists())
        source=self.source(schedule_enabled=True,interval_seconds=60)
        service=self.service(source); scheduler=Scheduler(service,clock=lambda:10)
        self.assertTrue(scheduler.tick()[0]['ok']); self.assertEqual(scheduler.tick(),[])
        expired=replace(source,approval_expires=20)
        service.registry=Registry([expired]); scheduler.clock=lambda:100
        self.assertFalse(scheduler.tick()[0]['ok'])

    def test_cli_stdout_is_one_envelope_and_no_source_details_in_error(self):
        result=subprocess.run([sys.executable,'-X','utf8','-B',str(Path(__file__).parents[1]/'companion_api.py'),
            '--operation','sync-once'],input=b'{"sourceId":"fixture","channel":"local","token":"fixture-private"}',
            capture_output=True,check=True)
        response=json.loads(result.stdout)
        self.assertFalse(response['ok']); self.assertEqual(response['error']['code'],'INVALID_REQUEST')
        self.assertNotIn(b'fixture-private',result.stdout); self.assertEqual(result.stderr,b'')

    def test_registry_rejects_unknown_paths_refs_targets_and_secret_fields(self):
        source=self.source()
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):
            Registry([source,replace(source,source_id='other')])
        path=Path(self.directory.name)/'registry.json'
        path.write_text(json.dumps({'schemaVersion':1,'sources':[{'apiKey':'fixture-private'}]}))
        with self.assertRaisesRegex(ValueError,'INVALID_REGISTRY'):
            Registry.load(path)


class MockRest:
    def __init__(self,kb):
        self.kb,self.docs,self.calls=kb,{},[]
        self.drop_create=False; self.drop_publish=False

    def request(self,method,path,body=None):
        self.calls.append((method,path,body))
        if method=='POST':
            doc=dict(id='fixture-document',knowledge_base_id=self.kb,type='manual',
                title=body['title'],metadata={'content':body['content'],'status':body['status']},
                custom_metadata={},parse_status='draft')
            self.docs[doc['id']]=doc
            if self.drop_create:
                raise UnknownOutcome()
            return {'success':True,'data':dict(doc)}
        if path.startswith('/api/v1/knowledge-bases/'):
            return {'success':True,'data':list(self.docs.values()),'total':len(self.docs)}
        if path.startswith('/api/v1/chunks/'):
            document_id=path.split('/')[-1].split('?')[0]
            rows=[{'id':'fixture-chunk','knowledge_id':document_id,'content':'fixture'}] if document_id in self.docs else []
            return {'success':True,'data':rows,'total':len(rows)}
        document_id=path.split('/')[-1]
        if method=='GET':
            return {'success':True,'data':self.docs[document_id]} if document_id in self.docs else None
        if method=='DELETE':
            self.docs.pop(document_id,None)
            return {'success':True,'data':{'task_id':'fixture-delete'}}
        doc=self.docs[document_id]
        if path.startswith('/api/v1/knowledge/manual/'):
            doc.update(metadata={'content':body['content'],'status':'publish'},parse_status='completed')
            if self.drop_publish:
                raise UnknownOutcome()
        else:
            doc.update(body)
        return {'success':True,'data':doc}


class AdapterTests(Fixture):
    def setup_adapter(self,**options):
        source=self.source(); transport=MockRest(source.binding.kb_id)
        adapter=RestAdapter(source,transport,synthetic=True,index_confirm=options.get('index_confirm',lambda *a:True))
        ledger=self.ledger(); ledger.enqueue(self.scan())
        self.now=[10.0]
        return source,transport,adapter,ledger,Worker(ledger,adapter,clock=lambda:self.now[0])

    def test_draft_metadata_publish_order_and_observed_completion(self):
        source,transport,adapter,ledger,worker=self.setup_adapter()
        self.assertEqual(worker.step(),'awaiting_metadata')
        self.assertEqual(worker.step(),'awaiting_index')
        self.assertEqual(worker.step(),'done')
        writes=[(m,p,b) for m,p,b in transport.calls if m in ('POST','PUT')]
        self.assertEqual([b.get('status') for _,_,b in writes],['draft',None,'publish'])
        self.assertEqual(writes[1][2]['custom_metadata']['source_key'],'fixture:local:docs/a.md')
        self.assertEqual(ledger.checkpoint,self.first)
        self.assertFalse(adapter.production_ready)

    def test_lost_draft_create_is_never_blindly_recreated(self):
        source,transport,adapter,ledger,worker=self.setup_adapter(); transport.drop_create=True
        self.assertEqual(worker.step(),'unknown'); self.now[0]+=3
        self.assertEqual(worker.step(),'blocked')
        self.assertEqual(sum(m=='POST' for m,_,_ in transport.calls),1)
        self.assertIsNone(ledger.checkpoint)

    def test_lost_publish_is_reconciled_by_same_id_and_content_without_repeat(self):
        source,transport,adapter,ledger,worker=self.setup_adapter(); transport.drop_publish=True
        self.assertEqual(worker.step(),'awaiting_metadata')
        self.assertEqual(worker.step(),'publishing'); self.now[0]+=3
        self.assertEqual(worker.step(),'awaiting_index')
        self.assertEqual(worker.step(),'done')
        self.assertEqual(sum(m=='PUT' and '/manual/' in p for m,p,_ in transport.calls),1)

    def test_completed_document_without_index_attestation_does_not_advance(self):
        source,transport,adapter,ledger,worker=self.setup_adapter(index_confirm=None)
        self.assertEqual(worker.step(),'awaiting_metadata'); self.assertEqual(worker.step(),'awaiting_index')
        self.assertEqual(worker.step(),'awaiting_index'); self.assertIsNone(ledger.checkpoint)

    def test_missing_policy_blocks_before_http_or_credential_read(self):
        source=replace(self.source(),approved_scope='')
        with self.assertRaisesRegex(ValueError,'POLICY_NOT_APPROVED'):
            NetworkTransport(source)
        transport=MockRest(source.binding.kb_id)
        adapter=RestAdapter(source,transport,synthetic=True)
        with self.assertRaisesRegex(ValueError,'POLICY_NOT_APPROVED'):
            adapter.inspect('fixture')
        self.assertEqual(transport.calls,[])

    def test_immutable_loader_ignores_dirty_worktree(self):
        source,transport,adapter,ledger,worker=self.setup_adapter()
        row=ledger.db.execute('SELECT payload FROM operations').fetchone()
        operation=json.loads(row[0])
        self.write('docs/a.md','private dirty body')
        self.assertIn('fixture body',immutable_content(source,operation))
        self.assertNotIn('private dirty body',immutable_content(source,operation))


if __name__=='__main__':
    unittest.main()
