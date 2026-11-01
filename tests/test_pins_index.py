"""Synthetic fixed-pin and metadata-only index-evidence end-to-end tests."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest

import knowledge_sync as sync
from index_verifier import IndexVerifier,text_hash,reuses_index
from offline_writer import Binding,Ledger,Worker,digest,UnknownOutcome
from scheduler import Scheduler
from source_registry import Registry
from sync_service import Service,snapshot_state
from test_sync import Fixture
import test_sync as existing
from weknora_adapter import RestAdapter,immutable_content


class PinFixture(Fixture):
    def cache(self,name,title='Pinned child'):
        repo=Path(self.directory.name)/name
        repo.mkdir()
        for args in [('init','-q','--initial-branch=main'),('config','user.name','Synthetic cache'),
                     ('config','user.email','cache@localhost')]:
            self.cache_git(repo,*args)
        self.cache_write(repo,'docs/a.md',f'# {title}\n\n状态: Accepted\n\npinned synthetic body\n')
        self.cache_commit(repo)
        return repo

    def cache_git(self,repo,*args):
        return subprocess.run(['git','-C',str(repo),*args],capture_output=True,check=True).stdout

    def cache_write(self,repo,path,content):
        target=repo/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content,encoding='utf-8')

    def cache_commit(self,repo):
        self.cache_git(repo,'add','--','.')
        self.cache_git(repo,'commit','-q','-m','Synthetic cache revision')
        return self.cache_git(repo,'rev-parse','HEAD').decode().strip()

    def pin(self,path,repo,commit=None,parent=None):
        sha=commit or self.cache_git(repo,'rev-parse','HEAD').decode().strip()
        self.cache_git(parent or self.repo,'update-index','--add','--cacheinfo',f'160000,{sha},{path}')
        self.cache_git(parent or self.repo,'commit','-q','-m','Synthetic parent pin')
        return sha

    def pin_source(self,repo,channel='local',**options):
        return self.source(channel,binding=Binding('fixture',channel,'fixture-tenant',f'fixture-{channel}',includes=('child/docs',)),
            gitlinks=(sync.PinnedRepository('child',repo,'child-fixture'),),**options)


class PinnedTreeTests(PinFixture):
    def test_dual_tracks_read_parent_pins_not_dirty_cache_or_child_head(self):
        cache=self.cache('cache-child'); first_child=self.pin('child',cache)
        parent=self.sha();self.git('update-ref','refs/remotes/origin/main',parent)
        self.cache_write(cache,'docs/a.md','# New child\n\n状态: Accepted\nnew committed body')
        second_child=self.cache_commit(cache)
        self.cache_write(cache,'docs/a.md','dirty private body')
        self.cache_write(cache,'docs/untracked.md','untracked private body')
        local=self.pin_source(cache);official=self.pin_source(cache,'official')
        for source in [local,official]:
            manifest=Service(Registry([source]),synthetic=True).plan(source)[0]
            doc=manifest['documents'][0]
            self.assertEqual(doc['source_commit'],first_child)
            self.assertEqual(doc['parent_commit'],parent)
            self.assertEqual(doc['source_repo_id'],'child-fixture')
            ledger=Ledger(source.binding);self.addCleanup(ledger.close)
            job=ledger.enqueue(manifest)
            operation=json.loads(ledger.operations(job)[0]['payload'])
            self.assertIn('pinned synthetic body',immutable_content(source,operation))
            self.assertNotIn('private body',immutable_content(source,operation))
        self.pin('child',cache,second_child)
        local_doc=Service(Registry([local]),synthetic=True).plan(local)[0]['documents'][0]
        official_doc=Service(Registry([official]),synthetic=True).plan(official)[0]['documents'][0]
        self.assertEqual(local_doc['source_commit'],second_child)
        self.assertEqual(official_doc['source_commit'],first_child)
        self.assertNotEqual(local_doc['source_key'],official_doc['source_key'])

    def test_nested_include_discovers_ancestor_gitlinks_and_fixed_leaf(self):
        child=self.cache('cache-child');leaf=self.cache('cache-leaf','Nested leaf')
        leaf_sha=self.pin('inner',leaf,parent=child)
        child_sha=self.pin('child',child)
        source=self.source(binding=Binding('fixture','local','fixture-tenant','fixture-kb',includes=('child/inner/docs',)),
            gitlinks=(sync.PinnedRepository('child',child,'child'),sync.PinnedRepository('child/inner',leaf,'leaf')))
        manifest=Service(Registry([source]),synthetic=True).plan(source)[0]
        doc=manifest['documents'][0]
        self.assertEqual(doc['path'],'child/inner/docs/a.md')
        self.assertEqual([p['pinned_commit'] for p in doc['pin_chain']],[child_sha,leaf_sha])
        self.assertEqual(doc['source_path'],'docs/a.md')
        location=sync.document_location(self.repo,self.sha(),doc['path'],'fixture',source.gitlinks)
        self.assertEqual(location['source_commit'],leaf_sha)
        self.assertTrue(sync.provenance_valid(doc,manifest))

    def test_unregistered_pin_is_metadata_only_and_service_plan_rejects(self):
        cache=self.cache('cache-child');self.pin('child',cache)
        source=replace(self.pin_source(cache),gitlinks=())
        source=replace(source,approved_scope=source.scope)
        manifest=sync.build_manifest(self.repo,'fixture','local',['child/docs'])
        self.assertEqual(manifest['unread_gitlinks'],['child'])
        self.assertEqual(manifest['documents'],[])
        with self.assertRaisesRegex(ValueError,'PINNED_SOURCE_NOT_REGISTERED'):
            Service(Registry([source]),synthetic=True).plan(source)
        self.assertFalse(source.ledger.exists())

    def test_missing_child_commit_fails_offline_instead_of_using_head(self):
        cache=self.cache('cache-child');self.pin('child',cache,'1'*40)
        source=self.pin_source(cache)
        with self.assertRaisesRegex(ValueError,'git_read_failed_offline'):
            Service(Registry([source]),synthetic=True).plan(source)
        self.assertFalse(source.ledger.exists())

    def test_cache_without_branch_head_can_read_pinned_objects(self):
        cache=self.cache('cache-child');pin=self.pin('child',cache)
        self.cache_git(cache,'update-ref','-d','refs/heads/main')
        source=self.pin_source(cache)
        self.assertEqual(Service(Registry([source]),synthetic=True).plan(source)[0]['documents'][0]['source_commit'],pin)

    def test_child_exclusions_and_symlink_modes_are_never_imported(self):
        cache=self.cache('cache-child')
        for path,state in [('docs/secrets/private.md','Accepted'),('docs/draft.md','Draft'),('docs/excluded.md','Accepted')]:
            self.cache_write(cache,path,f'# Synthetic\n\n状态: {state}\n')
        self.cache_commit(cache)
        object_id=subprocess.run(['git','-C',str(cache),'hash-object','-w','--stdin'],input=b'outside-private',capture_output=True,check=True).stdout.decode().strip()
        self.cache_git(cache,'update-index','--add','--cacheinfo',f'120000,{object_id},docs/link.md')
        self.cache_git(cache,'commit','-q','-m','Synthetic symlink')
        self.pin('child',cache)
        source=self.pin_source(cache,excludes=('child/docs/excluded.md',))
        manifest=Service(Registry([source]),synthetic=True).plan(source)[0]
        self.assertEqual([d['path'] for d in manifest['documents']],['child/docs/a.md'])
        self.assertEqual(len(manifest['excluded']),4)

    def test_child_absence_proof_uses_target_pin_not_cache_head(self):
        cache=self.cache('cache-child');self.pin('child',cache)
        source=self.pin_source(cache,max_deletes=1,max_delete_fraction=1)
        service=Service(Registry([source]),synthetic=True);service.enqueue(source)
        ledger=Ledger(source.binding,source.ledger);self.addCleanup(ledger.close)
        worker,adapter=self.worker(ledger);self.assertEqual(self.drain(worker,adapter),'idle')
        old=ledger.checkpoint
        self.cache_git(cache,'rm','docs/a.md');missing=self.cache_commit(cache)
        self.assertEqual(service.plan(source)[1],[])
        self.pin('child',cache,missing)
        self.assertEqual(service.plan(source)[1],['child/docs/a.md'])
        self.assertEqual(ledger.checkpoint,old)

    def test_missing_target_pin_cannot_delete_existing_mapping_or_checkpoint(self):
        cache=self.cache('cache-child');self.pin('child',cache)
        source=self.pin_source(cache,max_deletes=1,max_delete_fraction=1)
        service=Service(Registry([source]),synthetic=True);service.enqueue(source)
        ledger=Ledger(source.binding,source.ledger);self.addCleanup(ledger.close)
        worker,adapter=self.worker(ledger);self.drain(worker,adapter)
        old=ledger.checkpoint;mapping=ledger.mappings()
        self.pin('child',cache,'3'*40)
        with self.assertRaises(ValueError):service.plan(source)
        self.assertEqual(ledger.checkpoint,old);self.assertEqual(ledger.mappings(),mapping)
        self.assertEqual(len(adapter.documents),1)

    def test_provenance_only_child_update_is_not_a_zero_operation_checkpoint(self):
        cache=self.cache('cache-child');self.pin('child',cache)
        source=self.pin_source(cache);service=Service(Registry([source]),synthetic=True)
        service.enqueue(source);ledger=Ledger(source.binding,source.ledger);self.addCleanup(ledger.close)
        worker,adapter=self.worker(ledger);self.drain(worker,adapter);old=ledger.checkpoint
        self.cache_write(cache,'not-in-scope.txt','unrelated synthetic commit')
        next_child=self.cache_commit(cache);self.pin('child',cache,next_child)
        job=service.enqueue(source)['jobId']
        operation=json.loads(ledger.operations(job)[0]['payload'])
        self.assertEqual(operation['kind'],'provenance')
        self.assertEqual(operation['desired']['source_commit'],next_child)
        self.assertIsNotNone(operation['previous_receipt'])
        self.assertEqual(ledger.checkpoint,old)
        self.drain(worker,adapter)
        self.assertEqual(ledger.checkpoint,self.sha())
        self.assertEqual(ledger.mappings()['child/docs/a.md']['parent_commit'],self.sha())

    def test_forged_child_sha_and_chain_are_rejected_by_outbox(self):
        cache=self.cache('cache-child');self.pin('child',cache)
        source=self.pin_source(cache);manifest=Service(Registry([source]),synthetic=True).plan(source)[0]
        manifest['documents'][0]['source_commit']='2'*40
        manifest['manifest_sha256']=digest({k:v for k,v in manifest.items() if k!='manifest_sha256'})
        ledger=Ledger(source.binding);self.addCleanup(ledger.close)
        with self.assertRaisesRegex(ValueError,'invalid_document_metadata'):
            ledger.enqueue(manifest)

    def test_registry_pins_are_explicit_and_participate_in_approval_scope(self):
        cache=self.cache('cache-child');source=self.pin_source(cache)
        path=existing.ServiceTests.write_registry(self,source)
        self.assertEqual(Registry.load(path).get('fixture','local').scope,source.scope)
        self.assertNotEqual(replace(source,gitlinks=()).scope,source.scope)
        data=json.loads(path.read_text());data['sources'][0]['gitlinks'][0]['path']='../private'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):Registry.load(path)


class IndexedRest:
    """In-memory versioned document API; no sockets or databases."""
    def __init__(self,kb):
        self.kb,self.docs,self.calls=kb,{},[]
        self.drop_publish_before=False;self.drop_publish_after=False

    def chunks(self,document_id):
        if document_id not in self.docs:return []
        content=self.docs[document_id]['metadata']['content']
        return [dict(id=document_id+'-chunk',knowledge_id=document_id,content=content)]

    def request(self,method,path,body=None):
        self.calls.append((method,path,body))
        if method=='POST':
            identity='synthetic-doc-'+str(len(self.docs)+1)
            doc=dict(id=identity,knowledge_base_id=self.kb,type='manual',title=body['title'],
                metadata=dict(content=body['content'],status='draft',version=0),custom_metadata={},parse_status='draft')
            self.docs[identity]=doc
            return dict(success=True,data=copy.deepcopy(doc))
        if path.startswith('/api/v1/knowledge-bases/'):
            return dict(success=True,data=list(self.docs.values()),total=len(self.docs))
        identity=path.split('/')[-1].split('?')[0]
        if path.startswith('/api/v1/chunks/'):
            rows=self.chunks(identity);return dict(success=True,data=rows,total=len(rows))
        if method=='GET':return dict(success=True,data=copy.deepcopy(self.docs[identity])) if identity in self.docs else None
        if method=='DELETE':
            self.docs.pop(identity,None)
            return dict(success=True,data=dict(task_id='synthetic-delete-'+identity))
        doc=self.docs[identity]
        if '/knowledge/manual/' in path:
            if self.drop_publish_before:raise UnknownOutcome('synthetic publish not applied')
            doc['metadata']=dict(content=body['content'],status='publish',version=doc['metadata']['version']+1)
            doc['parse_status']='completed'
            if self.drop_publish_after:raise UnknownOutcome('synthetic publish applied reply lost')
        else:doc.update(copy.deepcopy(body))
        return dict(success=True,data=copy.deepcopy(doc))


class EvidenceProvider:
    production_ready=False
    provider_id='fixture-index'
    def __init__(self,source,clock):
        self.source,self.clock=source,clock
        self.state='ready';self.tamper=None

    def observe(self,op,document,chunks):
        proof=dict(schemaVersion=1,providerId=self.provider_id,state=self.state,observedAt=self.clock(),
            binding=dict(op['desired'],document_id=op['document_id']),taskState='done')
        if document is None:
            proof.update(documentAbsent=True,taskId=op.get('task_id'),
                indexes={name:dict(state='done',remainingChunkIds=[]) for name in self.source.required_indexes})
        else:
            version=document['metadata']['version']
            observed=sorted([dict(id=c['id'],contentSHA256=text_hash(c['content'])) for c in chunks],key=lambda c:c['id'])
            proof.update(documentVersion=version,documentChunkCount=len(chunks),contentSHA256=text_hash(document['metadata']['content']),
                indexes={name:dict(state='done',documentVersion=version,totalChunks=len(chunks),chunks=observed)
                         for name in self.source.required_indexes})
            if reuses_index(op) and op.get('previous_receipt'):
                proof['basisReceiptHash']=op['previous_receipt']['receiptHash']
        if self.tamper:self.tamper(proof)
        return proof


class IndexEvidenceTests(Fixture):
    def setup_pipeline(self,source=None):
        source=source or self.source(index_verifier='fixture-index',required_indexes=('keyword','vector'),max_deletes=10,max_delete_fraction=1)
        self.now=[10.0]
        transport=IndexedRest(source.binding.kb_id)
        provider=EvidenceProvider(source,lambda:self.now[0]);verifier=IndexVerifier(source,provider,clock=lambda:self.now[0])
        adapter=RestAdapter(source,transport,synthetic=True,index_confirm=verifier)
        service=Service(Registry([source]),clock=lambda:self.now[0],synthetic=True)
        job=service.enqueue(source)['jobId'];ledger=Ledger(source.binding,source.ledger);self.addCleanup(ledger.close)
        worker=Worker(ledger,adapter,clock=lambda:self.now[0],expected_scope=source.scope)
        return source,service,transport,provider,verifier,ledger,worker,job

    def complete(self,worker,limit=30):
        for _ in range(limit):
            result=worker.step();self.now[0]+=3
            if result in ('idle','blocked'):return result
        self.fail('synthetic pipeline did not terminate')

    def test_entire_batch_waits_for_all_positive_index_receipts(self):
        self.write('docs/b.md','# Second\n\n状态: Accepted\nsecond synthetic body');self.commit()
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline()
        provider.state='pending'
        self.assertEqual(worker.step(),'awaiting_metadata');self.assertEqual(worker.step(),'awaiting_index')
        self.assertEqual(worker.step(),'awaiting_index');self.assertIsNone(ledger.checkpoint)
        provider.state='ready';self.now[0]+=3
        self.assertEqual(worker.step(),'done');self.assertIsNone(ledger.checkpoint)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM index_receipts').fetchone()[0],1)
        self.assertEqual(self.complete(worker),'idle')
        self.assertEqual(ledger.checkpoint,self.sha())
        self.assertEqual(len(service.status(source)['indexReceipts']),2)
        self.assertFalse(verifier.production_ready)

    def test_unknown_evidence_blocks_and_never_resubmits_publish(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline()
        worker.step();worker.step();provider.state='unknown'
        self.assertEqual(worker.step(),'blocked');calls=len(rest.calls)
        self.now[0]+=100;self.assertEqual(worker.step(),'idle')
        self.assertEqual(len(rest.calls),calls);self.assertIsNone(ledger.checkpoint)
        self.assertEqual(ledger.operations(job)[0]['error'],'index_evidence_unknown')

    def test_wrong_scope_version_chunk_coverage_and_stale_evidence_are_unknown(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline()
        worker.step();worker.step()
        row=ledger.operations(job)[0];op=json.loads(row['payload']);op['document_id']=row['document_id']
        doc=rest.docs[op['document_id']];chunks=rest.chunks(op['document_id'])
        valid=provider.observe(op,doc,chunks)
        mutations=[lambda p:p['binding'].update(kb_id='foreign'),lambda p:p.update(observedAt=-100),
            lambda p:p.update(observedAt=1e20),lambda p:p.update(documentVersion=999),
            lambda p:p['indexes'].pop('vector'),lambda p:p['indexes']['keyword'].update(totalChunks=2),
            lambda p:p['indexes']['keyword'].update(chunks=[]),lambda p:p['indexes']['vector'].update(documentVersion=0),
            lambda p:p.update(taskState='processing'),lambda p:p.update(contentSHA256='wrong'),
            lambda p:p.update(documentChunkCount=2),lambda p:p.update(documentVersion=True),
            lambda p:p['indexes']['keyword'].update(totalChunks=True)]
        self.assertEqual(verifier.verify(op,doc,chunks,valid)['state'],'ready')
        for mutate in mutations:
            proof=copy.deepcopy(valid);mutate(proof)
            self.assertEqual(verifier.verify(op,doc,chunks,proof)['state'],'unknown')
        self.assertEqual(verifier.verify(op,doc,chunks+chunks,valid)['state'],'unknown')
        changed=copy.deepcopy(doc);changed['custom_metadata']['source_commit']='0'*40
        self.assertEqual(verifier.verify(op,changed,chunks,valid)['state'],'unknown')

    def test_deleted_document_absence_waits_for_index_and_exact_task_proof(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline();self.complete(worker)
        before=ledger.checkpoint;self.git('rm','docs/a.md');self.commit()
        job=service.enqueue(source)['jobId'];provider.state='pending'
        self.assertEqual(worker.step(),'awaiting_index');self.assertEqual(rest.docs,{})
        self.assertEqual(worker.step(),'awaiting_index');self.assertEqual(ledger.checkpoint,before)
        provider.state='ready';self.now[0]+=3
        self.assertEqual(worker.step(),'done');self.assertEqual(ledger.checkpoint,self.sha())
        receipt=json.loads(ledger.db.execute('SELECT value FROM index_receipts ORDER BY rowid DESC LIMIT 1').fetchone()[0])
        self.assertTrue(receipt['deleted']);self.assertIsNotNone(receipt['taskId'])

    def test_wrong_delete_task_receipt_blocks_without_delete_replay(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline();self.complete(worker)
        before=ledger.checkpoint;self.git('rm','docs/a.md');self.commit();service.enqueue(source)
        worker.step();provider.tamper=lambda p:p.update(taskId='wrong-task')
        self.assertEqual(worker.step(),'blocked');self.assertEqual(ledger.checkpoint,before)
        self.assertEqual(sum(method=='DELETE' for method,_,_ in rest.calls),1)

    def test_metadata_only_provenance_uses_new_receipt_bound_to_prior_index(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline();self.complete(worker)
        prior=json.loads(ledger.db.execute('SELECT value FROM index_receipts').fetchone()[0])
        prior_publish=sum(method=='PUT' and '/manual/' in path for method,path,_ in rest.calls)
        self.write('outside.txt','unrelated commit');self.commit();new_job=service.enqueue(source)['jobId']
        self.assertEqual(json.loads(ledger.operations(new_job)[0]['payload'])['kind'],'provenance')
        self.assertEqual(self.complete(worker),'idle')
        latest=json.loads(ledger.db.execute('SELECT value FROM index_receipts ORDER BY rowid DESC LIMIT 1').fetchone()[0])
        self.assertEqual(latest['basisReceiptHash'],prior['receiptHash'])
        self.assertNotEqual(latest['operationId'],prior['operationId'])
        self.assertEqual(latest['parentCommit'],self.sha())
        self.assertEqual(sum(method=='PUT' and '/manual/' in path for method,path,_ in rest.calls),prior_publish)

    def test_reopened_ledger_resumes_pending_index_and_keeps_durable_receipt(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline()
        worker.step();worker.step();provider.state='pending';worker.step()
        ledger.close()
        reopened=Ledger(source.binding,source.ledger);self.addCleanup(reopened.close)
        self.assertIsNone(reopened.checkpoint)
        adapter=RestAdapter(source,rest,synthetic=True,index_confirm=verifier)
        worker=Worker(reopened,adapter,clock=lambda:self.now[0],expected_scope=source.scope)
        provider.state='ready';self.now[0]+=3
        self.assertEqual(worker.step(),'done')
        self.assertEqual(reopened.checkpoint,self.sha())
        self.assertEqual(len(snapshot_state(source)['receipts']),1)

    def test_rename_reuses_index_but_processing_policy_change_republishes(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline();self.complete(worker)
        self.git('mv','docs/a.md','docs/renamed.md');self.commit();service.enqueue(source)
        self.assertEqual(self.complete(worker),'idle')
        self.assertEqual(len(rest.docs),1)
        self.assertEqual(sum(method=='PUT' and '/manual/' in path for method,path,_ in rest.calls),1)
        source=replace(source,processing=tuple((k,True if k=='summary_enabled' else v) for k,v in source.processing))
        source=replace(source,approved_scope=source.scope);service.registry=Registry([source])
        adapter=RestAdapter(source,rest,synthetic=True,index_confirm=IndexVerifier(source,provider,clock=lambda:self.now[0]))
        worker=Worker(ledger,adapter,clock=lambda:self.now[0],expected_scope=source.scope)
        service.enqueue(source);self.assertEqual(self.complete(worker),'idle')
        self.assertEqual(sum(method=='PUT' and '/manual/' in path for method,path,_ in rest.calls),2)

    def test_changed_fallback_title_republishes_instead_of_reusing_prior_index(self):
        self.write('docs/a.md','状态: Accepted\n\nNo heading synthetic body');self.commit()
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline();self.complete(worker)
        self.git('mv','docs/a.md','docs/new-name.md');self.commit();service.enqueue(source)
        self.assertEqual(self.complete(worker),'idle')
        self.assertEqual(sum(method=='PUT' and '/manual/' in path for method,path,_ in rest.calls),2)
        self.assertEqual(next(iter(rest.docs.values()))['metadata']['version'],2)

    def test_missing_reuse_basis_is_unknown_and_keeps_previous_completed_sha(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline();self.complete(worker)
        prior=ledger.checkpoint
        self.git('mv','docs/a.md','docs/renamed.md');self.commit();service.enqueue(source)
        worker.step();worker.step();provider.tamper=lambda p:p.pop('basisReceiptHash',None)
        self.assertEqual(worker.step(),'blocked');self.assertEqual(ledger.checkpoint,prior)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM index_receipts').fetchone()[0],1)

    def changed_policy_worker(self,source,service,rest,provider,ledger):
        source=replace(source,processing=tuple((k,True if k=='summary_enabled' else v) for k,v in source.processing))
        source=replace(source,approved_scope=source.scope);service.registry=Registry([source])
        provider.source=source
        verifier=IndexVerifier(source,provider,clock=lambda:self.now[0])
        adapter=RestAdapter(source,rest,synthetic=True,index_confirm=verifier)
        worker=Worker(ledger,adapter,clock=lambda:self.now[0],expected_scope=source.scope)
        self.write('outside.txt','new parent revision');self.commit()
        job=service.enqueue(source)['jobId']
        return source,adapter,worker,job

    def test_unknown_unapplied_processing_publish_never_accepts_previous_index(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline();self.complete(worker)
        previous=ledger.checkpoint
        source,adapter,worker,job=self.changed_policy_worker(source,service,rest,provider,ledger)
        rest.drop_publish_before=True
        self.assertEqual(worker.step(),'awaiting_metadata');self.assertEqual(worker.step(),'publishing')
        row=ledger.operations(job)[0];op=json.loads(row['payload']);op['document_id']=row['document_id']
        doc=rest.docs[op['document_id']]
        self.assertEqual(adapter.reconcile_publish(op)['state'],'ambiguous')
        self.assertEqual(adapter.index_confirm(op,doc,rest.chunks(op['document_id']))['state'],'unknown')
        self.assertEqual(self.complete(worker),'blocked')
        self.assertEqual(ledger.checkpoint,previous)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM index_receipts').fetchone()[0],1)
        self.assertEqual(sum(method=='PUT' and '/manual/' in path for method,path,_ in rest.calls),2)

    def test_lost_applied_processing_publish_reconciles_new_version_without_replay(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline();self.complete(worker)
        source,adapter,worker,job=self.changed_policy_worker(source,service,rest,provider,ledger)
        rest.drop_publish_after=True
        self.assertEqual(worker.step(),'awaiting_metadata');self.assertEqual(worker.step(),'publishing')
        self.assertEqual(self.complete(worker),'idle')
        self.assertEqual(ledger.checkpoint,self.sha())
        self.assertEqual(sum(method=='PUT' and '/manual/' in path for method,path,_ in rest.calls),2)
        receipt=json.loads(ledger.db.execute('SELECT value FROM index_receipts ORDER BY rowid DESC LIMIT 1').fetchone()[0])
        self.assertEqual(receipt['documentVersion'],2)

    def test_unknown_unapplied_title_publish_blocks_without_reusing_old_index(self):
        self.write('docs/a.md','状态: Accepted\n\nNo heading synthetic body');self.commit()
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline();self.complete(worker)
        prior=ledger.checkpoint
        self.git('mv','docs/a.md','docs/new-name.md');self.commit();service.enqueue(source)
        rest.drop_publish_before=True;worker.step();self.assertEqual(worker.step(),'publishing')
        self.assertEqual(self.complete(worker),'blocked');self.assertEqual(ledger.checkpoint,prior)
        self.assertEqual(sum(method=='PUT' and '/manual/' in path for method,path,_ in rest.calls),2)

    def test_receipt_storage_failure_rolls_back_done_mapping_and_checkpoint(self):
        source,service,rest,provider,verifier,ledger,worker,job=self.setup_pipeline()
        worker.step();worker.step()
        ledger.db.execute("CREATE TRIGGER synthetic_reject_receipt BEFORE INSERT ON index_receipts BEGIN SELECT RAISE(ABORT,'synthetic interruption'); END")
        self.assertEqual(worker.step(),'blocked')
        self.assertIsNone(ledger.checkpoint);self.assertEqual(ledger.mappings(),{})
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM index_receipts').fetchone()[0],0)

    def test_callable_or_unapproved_provider_never_marks_production_ready(self):
        source=self.source(index_verifier='fixture-index',required_indexes=('keyword',));rest=IndexedRest(source.binding.kb_id)
        self.assertFalse(RestAdapter(source,rest,index_confirm=lambda *args:True).production_ready)
        self.assertFalse(RestAdapter(source,rest,index_confirm=IndexVerifier(source,SimpleNamespace(provider_id='other',production_ready=True))).production_ready)
        self.assertFalse(RestAdapter(source,rest,index_confirm=IndexVerifier(source,SimpleNamespace(provider_id='fixture-index',production_ready=False))).production_ready)


class PinnedIndexIntegrationTests(PinFixture):
    def test_scheduled_dual_track_pin_content_waits_for_separate_index_completion(self):
        cache=self.cache('cache-child');first_child=self.pin('child',cache)
        official_parent=self.sha();self.git('update-ref','refs/remotes/origin/main',official_parent)
        self.cache_write(cache,'docs/a.md','# New local\n\n状态: Accepted\nnew local body')
        second_child=self.cache_commit(cache);self.pin('child',cache,second_child)
        local_parent=self.sha();self.now=[100.0]
        sources=[self.pin_source(cache,channel,index_verifier='fixture-index',required_indexes=('keyword',),
            ledger=Path(self.directory.name)/(channel+'.sync.sqlite3'),schedule_enabled=True,interval_seconds=60)
            for channel in ('local','official')]
        service=Service(Registry(sources),clock=lambda:self.now[0],synthetic=True)
        scheduler=Scheduler(service,clock=lambda:self.now[0])
        queued=scheduler.tick();self.assertEqual(len(queued),2)
        self.assertTrue(all(row['ok'] and row['data']['queuedOnly'] for row in queued))
        self.assertEqual(scheduler.tick(),[])
        for source,child_sha,parent_sha in zip(sources,[second_child,first_child],[local_parent,official_parent]):
            rest=IndexedRest(source.binding.kb_id);provider=EvidenceProvider(source,lambda:self.now[0])
            verifier=IndexVerifier(source,provider,clock=lambda:self.now[0])
            adapter=RestAdapter(source,rest,synthetic=True,index_confirm=verifier)
            ledger=Ledger(source.binding,source.ledger);self.addCleanup(ledger.close)
            worker=Worker(ledger,adapter,clock=lambda:self.now[0],expected_scope=source.scope)
            self.assertEqual(worker.step(),'awaiting_metadata');self.assertEqual(worker.step(),'awaiting_index')
            provider.state='pending';self.assertEqual(worker.step(),'awaiting_index')
            self.assertIsNone(ledger.checkpoint);self.assertEqual(service.status(source)['pending'],1)
            metadata=next(iter(rest.docs.values()))['custom_metadata']
            self.assertEqual(metadata['source_commit'],child_sha);self.assertEqual(metadata['parent_commit'],parent_sha)
            provider.state='ready';self.now[0]+=3;self.assertEqual(worker.step(),'done')
            self.assertEqual(ledger.checkpoint,parent_sha)
            self.assertEqual(len(service.status(source)['indexReceipts']),1)
        self.assertEqual(service.status(sources[1])['remoteFreshness'],'unknown_cached_ref')


if __name__=='__main__':unittest.main()
