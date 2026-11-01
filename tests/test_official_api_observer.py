import copy
import json
from pathlib import Path
import unittest
from urllib.parse import parse_qs,urlsplit

from index_verifier import IndexVerifier
from official_api_observer import OfficialAPIProvider
from offline_writer import Binding,Ledger,Worker
from source_registry import Source,Registry
from sync_service import Service
from weknora_adapter import RestAdapter
from test_sync import Fixture
from test_pins_index import IndexedRest


class OfficialGETFixture:
    """Known GET envelopes only; no network, credentials or index backend."""
    def __init__(self,document,chunks):
        self.document,self.chunks=copy.deepcopy(document),copy.deepcopy(chunks)
        self.calls=[];self.mutate=None;self.error=None

    def request(self,method,path,body=None):
        if method!='GET' or body is not None:
            raise AssertionError('observer must only issue GET without a body')
        self.calls.append(path)
        if self.error:raise self.error
        url=urlsplit(path)
        if url.path.startswith('/api/v1/knowledge/'):
            response=dict(success=True,data=copy.deepcopy(self.document)) if self.document is not None else None
        elif url.path.startswith('/api/v1/chunks/'):
            query=parse_qs(url.query);page=int(query['page'][0]);size=int(query['page_size'][0])
            response=dict(success=True,data=copy.deepcopy(self.chunks[(page-1)*size:page*size]),
                          total=len(self.chunks),page=page,page_size=size)
        else:raise AssertionError('unverified endpoint')
        if self.mutate:self.mutate(path,response)
        return response


class OfficialObservationTests(unittest.TestCase):
    def setUp(self):
        self.source=Source('fixture',Path('D:/synthetic-unused'),Binding('fixture','local','1','fixture-kb'),
                           'HEAD',Path('D:/synthetic-unused/ledger.sqlite3'),
                           index_verifier=OfficialAPIProvider.provider_id,required_indexes=('keyword',),
                           processing=tuple(sorted((key,False) for key in ('summary_enabled','enable_multimodel',
                               'graph_enabled','question_generation_enabled','extract_enabled'))))
        self.desired=dict(tenant_id='1',kb_id='fixture-kb',repo_id='fixture',channel='local',owner='fixture-sync',
                          path='docs/a.md',source_commit='a'*40,blob_sha='b'*40,title='Synthetic')
        self.op=dict(id='synthetic-operation',kind='add',document_id='synthetic-document',desired=self.desired)
        self.document=dict(id=self.op['document_id'],knowledge_base_id='fixture-kb',tenant_id=1,
                           custom_metadata=copy.deepcopy(self.desired),parse_status='completed',
                           metadata=dict(version=1,content='synthetic body'))
        self.chunks=[self.chunk(0)]

    def chunk(self,number):
        return dict(id='synthetic-chunk-'+str(number),knowledge_id=self.op['document_id'],
                    knowledge_base_id='fixture-kb',tenant_id=1,chunk_type='text',index_status='ready',content='synthetic '+str(number))

    def pipeline(self,chunks=None):
        transport=OfficialGETFixture(self.document,self.chunks if chunks is None else chunks)
        provider=OfficialAPIProvider(self.source,transport,clock=lambda:10)
        verifier=IndexVerifier(self.source,provider,clock=lambda:10)
        return transport,provider,verifier

    def test_completed_and_chunk_ready_never_enable_production_or_checkpoint_proof(self):
        transport,provider,verifier=self.pipeline()
        proof=provider.observe(self.op,self.document,self.chunks)
        self.assertEqual(proof['state'],'unknown');self.assertEqual(proof['observation']['textChunkCount'],1)
        self.assertNotIn('indexes',proof);self.assertNotIn('taskState',proof);self.assertNotIn('receipt',proof)
        self.assertFalse(provider.production_ready);self.assertFalse(verifier.production_ready)
        self.assertFalse(RestAdapter(self.source,transport,index_confirm=verifier).production_ready)
        self.assertEqual(verifier(self.op,self.document,self.chunks)['state'],'unknown')
        self.assertNotIn('synthetic body',json.dumps(proof));self.assertNotIn('synthetic 0',json.dumps(proof))
        self.assertEqual(set(transport.calls),{'/api/v1/knowledge/synthetic-document',
                                             '/api/v1/chunks/synthetic-document?page=1&page_size=100'})

    def test_multiple_pages_are_observed_with_stable_server_total(self):
        chunks=[self.chunk(n) for n in range(101)];transport,provider,_=self.pipeline(chunks)
        proof=provider.observe(self.op,self.document,chunks)
        self.assertEqual(proof['state'],'unknown');self.assertEqual(proof['observation']['textChunkCount'],101)
        self.assertIn('/api/v1/chunks/synthetic-document?page=2&page_size=100',transport.calls)

    def test_invalid_totals_pages_types_and_identity_never_attest(self):
        for field,value in [('total',True),('total',-1),('total',0),('total',3201),('page',True),('page',2),
                            ('page_size',99),('page_size',True),('success',False),('data',{})]:
            with self.subTest(field=field,value=value):
                transport,provider,_=self.pipeline()
                transport.mutate=lambda path,response,f=field,v=value:response.update({f:v}) if '/chunks/' in path else None
                proof=provider.observe(self.op,self.document,self.chunks)
                self.assertEqual(proof['state'],'unknown');self.assertNotIn('observation',proof)

    def test_total_change_or_missing_page_blocks_partial_snapshot(self):
        chunks=[self.chunk(n) for n in range(101)]
        for changes in [dict(total=100),dict(data=[])]:
            transport,provider,_=self.pipeline(chunks)
            transport.mutate=lambda path,response,v=changes:response.update(v) if 'page=2&' in path else None
            proof=provider.observe(self.op,self.document,chunks)
            self.assertEqual(proof['state'],'unknown');self.assertNotIn('observation',proof)

    def test_duplicate_foreign_or_nontext_chunk_rejects_entire_snapshot(self):
        for changes in [dict(id=self.chunks[0]['id']),dict(knowledge_id='foreign'),dict(knowledge_base_id='foreign'),
                        dict(tenant_id=2),dict(tenant_id=True),dict(chunk_type='image_caption'),
                        dict(index_status='invented'),dict(content=None)]:
            chunks=[self.chunk(0),dict(self.chunk(1),**changes)];_,provider,_=self.pipeline(chunks)
            proof=provider.observe(self.op,self.document,chunks)
            self.assertEqual(proof['state'],'unknown');self.assertNotIn('observation',proof)

    def test_document_version_race_and_caller_chunk_race_are_unknown(self):
        transport,provider,_=self.pipeline()
        def change_after_chunks(path,response):
            if '/chunks/' in path:transport.document['metadata']['version']=2
        transport.mutate=change_after_chunks
        proof=provider.observe(self.op,self.document,self.chunks)
        self.assertEqual(proof['state'],'unknown');self.assertNotIn('observation',proof)
        _,provider,_=self.pipeline();wrong=copy.deepcopy(self.chunks);wrong[0]['content']='changed'
        self.assertEqual(provider.observe(self.op,self.document,wrong)['state'],'unknown')

    def test_scope_rejection_occurs_before_any_request(self):
        for changes in [dict(kb_id='foreign'),dict(tenant_id='2'),dict(channel='official'),dict(path='private/a.md'),
                        dict(path='docs/.env'),dict(path='../docs/a.md')]:
            transport,provider,_=self.pipeline();op=dict(self.op,desired=dict(self.desired,**changes))
            self.assertEqual(provider.observe(op,self.document,self.chunks)['state'],'unknown')
            self.assertEqual(transport.calls,[])

    def test_foreign_document_source_metadata_and_boolean_version_are_unknown(self):
        for changes in [dict(knowledge_base_id='foreign'),dict(tenant_id=2),dict(tenant_id=True),dict(id='foreign'),
                        dict(custom_metadata={}),dict(metadata=dict(version=True,content='synthetic body'))]:
            transport,provider,_=self.pipeline();transport.document.update(changes)
            proof=provider.observe(self.op,transport.document,self.chunks)
            self.assertEqual(proof['state'],'unknown');self.assertNotIn('observation',proof)

    def test_document_and_chunk_processing_and_failure_are_lower_states_only(self):
        for parse,state in [('pending','pending'),('processing','pending'),('finalizing','pending'),('failed','failed')]:
            self.document['parse_status']=parse;transport,provider,_=self.pipeline()
            self.assertEqual(provider.observe(self.op,self.document,[])['state'],state)
            self.assertFalse(any('/chunks/' in p for p in transport.calls))
        self.document['parse_status']='completed'
        for status,state in [('processing','pending'),('failed','failed')]:
            chunks=[dict(self.chunk(0),index_status=status)];_,provider,_=self.pipeline(chunks)
            self.assertEqual(provider.observe(self.op,self.document,chunks)['state'],state)

    def test_404_does_not_invent_deletion_task_or_index_absence(self):
        transport,provider,verifier=self.pipeline();transport.document=None
        op=dict(self.op,kind='delete',task_id='synthetic-delete-task')
        proof=provider.observe(op,None,[])
        self.assertEqual(proof['state'],'unknown');self.assertNotIn('documentAbsent',proof)
        self.assertNotIn('taskId',proof);self.assertNotIn('indexes',proof)
        self.assertEqual(verifier(op,None,[])['state'],'unknown');self.assertEqual(len(transport.calls),2)

    def test_transport_errors_never_leak_response_secret_or_retry_requests(self):
        transport,provider,_=self.pipeline();transport.error=RuntimeError('synthetic secret must be redacted')
        proof=provider.observe(self.op,self.document,self.chunks)
        self.assertEqual(proof['state'],'unknown');self.assertEqual(len(transport.calls),1)
        self.assertNotIn('synthetic secret',json.dumps(proof))


class OfficialObservationOutboxTests(Fixture):
    def test_api_completed_and_ready_chunks_cannot_finish_outbox(self):
        source=self.source(index_verifier=OfficialAPIProvider.provider_id,required_indexes=('keyword',))
        class WithOfficialGETMetadata(IndexedRest):
            def request(self,method,path,body=None):
                if method=='GET' and '/knowledge/' in path:
                    for doc in self.docs.values():doc['tenant_id']=source.binding.tenant_id
                response=super().request(method,path,body)
                if method=='GET' and '/chunks/' in path:
                    query=parse_qs(urlsplit(path).query)
                    response.update(page=int(query['page'][0]),page_size=int(query['page_size'][0]))
                    for chunk in response['data']:
                        chunk.update(tenant_id=source.binding.tenant_id,knowledge_base_id=source.binding.kb_id,
                                     index_status='ready',chunk_type='text')
                return response
        transport=WithOfficialGETMetadata(source.binding.kb_id)
        provider=OfficialAPIProvider(source,transport,clock=lambda:10)
        adapter=RestAdapter(source,transport,synthetic=True,index_confirm=IndexVerifier(source,provider,clock=lambda:10))
        service=Service(Registry([source]),synthetic=True);job=service.enqueue(source)['jobId']
        ledger=Ledger(source.binding,source.ledger);self.addCleanup(ledger.close)
        worker=Worker(ledger,adapter,clock=lambda:10,expected_scope=source.scope)
        self.assertEqual(worker.step(),'awaiting_metadata');self.assertEqual(worker.step(),'awaiting_index')
        self.assertEqual(worker.step(),'blocked');self.assertIsNone(ledger.checkpoint)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM index_receipts').fetchone()[0],0)
        self.assertEqual(ledger.operations(job)[0]['error'],'index_evidence_unknown')
