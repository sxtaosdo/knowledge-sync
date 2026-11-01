"""Actual SQLite/FTS5 canary evidence and outbox completion, with zero Git/HTTP."""
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from index_verifier import IndexVerifier,text_hash
from offline_writer import Binding,Ledger,Worker,canonical,digest
from source_registry import Source
from sqlite_keyword_observer import (SQLiteKeywordProvider,FTS_DDL,VOCAB_DDL,
    TOKENIZER_ID,tokenize_basic_han,index_content,postings)
from test_pins_index import IndexedRest
from weknora_adapter import RestAdapter


class SyntheticStore:
    """Proposed native producer contract, confined to this temporary fixture.

    Stock WeKnora has no sync_keyword_receipts/vocab projection. The fixture
    writes its real FTS rows and success record atomically, propagating errors.
    """
    def __init__(self,path):
        self.path=path
        self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
        self.db.executescript('''
CREATE TABLE knowledges(id TEXT PRIMARY KEY,tenant_id INTEGER,knowledge_base_id TEXT,
 title TEXT,type TEXT,parse_status TEXT,pending_subtasks_count INTEGER,metadata TEXT,custom_metadata TEXT,deleted_at TEXT);
CREATE TABLE chunks(id TEXT PRIMARY KEY,tenant_id INTEGER,knowledge_id TEXT,knowledge_base_id TEXT,
 content TEXT,context_header TEXT,content_revision INTEGER,index_status TEXT,is_enabled INTEGER,
 chunk_type TEXT,deleted_at TEXT);
CREATE TABLE lite_embeddings(id INTEGER PRIMARY KEY AUTOINCREMENT,source_id TEXT,source_type INTEGER,
 chunk_id TEXT,knowledge_id TEXT,knowledge_base_id TEXT,content TEXT,dimension INTEGER,is_enabled INTEGER,
 UNIQUE(source_id,source_type));
''')
        self.db.execute(FTS_DDL.format(name='lite_embeddings_fts'))
        self.db.executescript(Path(__file__).with_name('sqlite_keyword_receipts.sql').read_text(encoding='utf-8'))
        self.db.commit()

    def close(self): self.db.close()

    def complete(self,op,document,chunks,*,fail_before_receipt=False):
        desired=op['desired'];identity=op['document_id']
        owned=set()
        for old in self.db.execute('SELECT payload FROM sync_keyword_receipts WHERE knowledge_id=?',(identity,)):
            owned.update(json.loads(old[0])['ownedRowIds'])
        with self.db:
            old_rows=self.db.execute('SELECT id FROM lite_embeddings WHERE knowledge_id=?',(identity,)).fetchall()
            owned.update(row[0] for row in old_rows)
            for row in old_rows: self.db.execute('DELETE FROM lite_embeddings_fts WHERE rowid=?',(row[0],))
            self.db.execute('DELETE FROM lite_embeddings WHERE knowledge_id=?',(identity,))
            self.db.execute('DELETE FROM chunks WHERE knowledge_id=?',(identity,))
            self.db.execute('DELETE FROM knowledges WHERE id=?',(identity,))
            inventory=[];version=None
            if document is not None:
                version=document['metadata']['version']
                self.db.execute('INSERT INTO knowledges VALUES(?,?,?,?,?,?,?,?,?,NULL)',
                    (identity,int(desired['tenant_id']),desired['kb_id'],document['title'],'manual','completed',0,
                     canonical(document['metadata']),canonical(document['custom_metadata'])))
                for chunk in chunks:
                    self.db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,NULL)',
                        (chunk['id'],int(desired['tenant_id']),identity,desired['kb_id'],chunk['content'],
                         chunk.get('context_header',''),chunk.get('content_revision',0),'ready',1,'text'))
                    stored=self.db.execute('SELECT * FROM chunks WHERE id=?',(chunk['id'],)).fetchone()
                    content=index_content(document['title'],stored)
                    cursor=self.db.execute('INSERT INTO lite_embeddings(source_id,source_type,chunk_id,knowledge_id,knowledge_base_id,content,dimension,is_enabled) VALUES(?,?,?,?,?,?,0,1)',
                        (chunk['id'],0,chunk['id'],identity,desired['kb_id'],content))
                    row_id=cursor.lastrowid;owned.add(row_id)
                    self.db.execute('INSERT INTO lite_embeddings_fts(rowid,content,source_id,chunk_id,knowledge_id,knowledge_base_id) VALUES(?,?,?,?,?,?)',
                        (row_id,tokenize_basic_han(content),chunk['id'],chunk['id'],identity,desired['kb_id']))
                    inventory.append(dict(rowId=row_id,sourceId=chunk['id'],sourceType=0,chunkId=chunk['id'],
                        chunkRevision=stored['content_revision'],inputSHA256=text_hash(content),
                        postingsSHA256=digest(postings(self.db,'lite_embeddings_fts_vocab',row_id))))
            if fail_before_receipt: raise RuntimeError('synthetic crash before native completion receipt')
            payload=dict(schemaVersion=1,binding=dict(desired,document_id=identity),kind=op['kind'],
                tokenizerId=TOKENIZER_ID,taskId=op.get('task_id'),documentVersion=version,
                ownedRowIds=sorted(owned),indexRows=inventory,
                basisReceiptHash=(op.get('previous_receipt') or {}).get('receiptHash'))
            self.db.execute('INSERT OR REPLACE INTO sync_keyword_receipts VALUES(?,?,?,?,?,?,?,?)',
                (op['id'],int(desired['tenant_id']),desired['kb_id'],identity,'done',op.get('task_id'),version,canonical(payload)))


class SQLiteKeywordTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='keyword-canary-',dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=[100.0]
        self.source=Source('canary',self.root/'unused-repo',Binding('canary','local','1','canary-kb'),
            'HEAD',self.root/'canary.sync.sqlite3',index_verifier=SQLiteKeywordProvider.provider_id,
            required_indexes=('keyword',),writer_enabled=True,network_enabled=True,
            processing=tuple(sorted((key,False) for key in ('summary_enabled','enable_multimodel',
                'graph_enabled','question_generation_enabled','extract_enabled'))))
        self.source=replace(self.source,approved_scope=self.source.scope,approval_expires=1e12)
        self.store=SyntheticStore(self.root/'canary.sqlite3');self.addCleanup(self.store.close)
        self.provider=SQLiteKeywordProvider(self.source,self.store.path,clock=lambda:self.now[0])
        self.verifier=IndexVerifier(self.source,self.provider,clock=lambda:self.now[0])
        self.desired=dict(owner='fixture-sync',repo_id='canary',channel='local',tenant_id='1',kb_id='canary-kb',
            path='docs/a.md',source_key='canary:local:docs/a.md',source_commit='1'*40,parent_commit='1'*40,
            blob_sha='2'*40,title='中文 Canary',processing_policy_hash='policy')
        self.op=dict(id='op-add',kind='add',desired=self.desired,document_id='canary-doc',task_id=None)
        self.document=dict(id='canary-doc',knowledge_base_id='canary-kb',title=self.desired['title'],
            parse_status='completed',metadata=dict(content='中文，离线关键词 Canary',version=1,status='publish'),
            custom_metadata=copy.deepcopy(self.desired))
        self.chunks=[dict(id='canary-chunk',knowledge_id='canary-doc',content=self.document['metadata']['content'],
                          context_header='章节 Context',content_revision=1)]

    def ready(self):
        self.store.complete(self.op,self.document,self.chunks)
        return self.verifier(self.op,self.document,self.chunks)

    def alter_receipt(self,change):
        row=self.store.db.execute('SELECT payload FROM sync_keyword_receipts WHERE operation_id=?',(self.op['id'],)).fetchone()
        payload=json.loads(row[0]);change(payload)
        self.store.db.execute('UPDATE sync_keyword_receipts SET payload=? WHERE operation_id=?',(canonical(payload),self.op['id']))
        self.store.db.commit()

    def test_real_contentless_postings_produce_synthetic_ready_and_readonly_bytes(self):
        self.assertEqual(self.ready()['state'],'ready')
        before=hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        self.assertTrue(self.verifier(self.op,self.document,self.chunks)['receipt']['synthetic'])
        self.assertEqual(len(self.verifier(self.op,self.document,self.chunks)['receipt']['backendInventoryHash']),64)
        self.assertFalse(self.provider.production_ready);self.assertFalse(self.verifier.production_ready)
        self.assertEqual(before,hashlib.sha256(self.store.path.read_bytes()).hexdigest())
        self.assertIsNone(self.store.db.execute('SELECT content FROM lite_embeddings_fts').fetchone()[0])

    def test_missing_native_receipt_stays_pending(self):
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'pending')

    def test_missing_fts_row_is_unknown_even_if_parse_and_chunk_are_ready(self):
        self.ready();self.store.db.execute('DELETE FROM lite_embeddings_fts');self.store.db.commit()
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')

    def test_wrong_fts_text_with_correct_mapping_is_unknown(self):
        self.ready();row_id=self.store.db.execute('SELECT id FROM lite_embeddings').fetchone()[0]
        self.store.db.execute('DELETE FROM lite_embeddings_fts')
        self.store.db.execute('INSERT INTO lite_embeddings_fts(rowid,content,source_id,chunk_id,knowledge_id,knowledge_base_id) VALUES(?,?,?,?,?,?)',
            (row_id,'stale wrong content','canary-chunk','canary-chunk','canary-doc','canary-kb'))
        self.store.db.commit()
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')

    def test_orphan_fts_row_is_unknown_without_metadata_row(self):
        self.ready();self.store.db.execute('INSERT INTO lite_embeddings_fts(rowid,content,knowledge_id) VALUES(?,?,?)',
                                          (900,'stale orphan','canary-doc'));self.store.db.commit()
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')

    def test_header_title_or_revision_change_without_reindex_is_unknown(self):
        for field,value in [('context_header','changed header'),('content_revision',2)]:
            with self.subTest(field=field):
                self.ready();self.store.db.execute(f'UPDATE chunks SET {field}=?',(value,));self.store.db.commit()
                self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')
        self.ready();self.store.db.execute('UPDATE knowledges SET title=?',('changed title',));self.store.db.commit()
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')

    def test_old_generation_receipt_and_foreign_scope_are_unknown(self):
        self.ready();self.store.db.execute('UPDATE sync_keyword_receipts SET generation=0');self.store.db.commit()
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')
        self.ready();self.store.db.execute('UPDATE chunks SET tenant_id=2');self.store.db.commit()
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')

    def test_incomplete_caller_or_duplicate_index_mapping_is_unknown(self):
        self.ready();self.assertEqual(self.verifier(self.op,self.document,[])['state'],'unknown')
        self.store.db.execute('INSERT INTO lite_embeddings(source_id,source_type,chunk_id,knowledge_id,knowledge_base_id,content,dimension,is_enabled) SELECT source_id||\'-extra\',0,chunk_id,knowledge_id,knowledge_base_id,content,0,1 FROM lite_embeddings')
        self.store.db.commit();self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')

    def test_receipt_hash_inventory_and_schema_must_match(self):
        self.ready();self.alter_receipt(lambda payload:payload['indexRows'][0].update(inputSHA256='0'*64))
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')
        self.store.db.execute('DROP TABLE lite_embeddings_fts_vocab');self.store.db.commit()
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')

    def test_failed_and_running_native_tasks_do_not_become_ready(self):
        self.ready()
        for state,wanted in [('running','pending'),('queued','pending'),('failed','failed')]:
            self.store.db.execute('UPDATE sync_keyword_receipts SET state=?',(state,));self.store.db.commit()
            self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],wanted)

    def test_sql_failure_rolls_back_index_metadata_and_success_receipt(self):
        with self.assertRaises(RuntimeError): self.store.complete(self.op,self.document,self.chunks,fail_before_receipt=True)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lite_embeddings').fetchone()[0],0)
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'pending')
        self.assertEqual(self.ready()['state'],'ready')

    def test_delete_needs_exact_task_and_complete_native_absence(self):
        self.ready()
        deletion=dict(self.op,id='op-delete',kind='delete',task_id='delete-task')
        self.store.complete(deletion,None,[])
        self.assertEqual(self.verifier(deletion,None,[])['state'],'ready')
        self.assertEqual(self.verifier(dict(deletion,task_id='wrong-task'),None,[])['state'],'unknown')
        self.store.db.execute('INSERT INTO lite_embeddings_fts(rowid,content,knowledge_id) VALUES(?,?,?)',
            (1,'orphan after delete','canary-doc'));self.store.db.commit()
        self.assertEqual(self.verifier(deletion,None,[])['state'],'unknown')

    def test_changed_scope_or_vector_policy_or_unapproved_source_is_unknown(self):
        self.ready()
        for source in [replace(self.source,required_indexes=('keyword','vector')),
                       replace(self.source,approved_scope=''),replace(self.source,excludes=('docs',))]:
            provider=SQLiteKeywordProvider(source,self.store.path,clock=lambda:self.now[0])
            self.assertEqual(provider.observe(self.op,self.document,self.chunks)['state'],'unknown')

    def test_full_go_unicode_tables_cover_extended_han_and_delimiters(self):
        self.assertEqual(tokenize_basic_han('中文测试 Canary_42'), '中文 文测 测试 Canary 42')
        self.assertEqual(tokenize_basic_han('\u3400\U00020000 rare Han'), '\u3400\U00020000 rare Han')
        self.assertEqual(tokenize_basic_han('emoji\U0001f600 NB\u00a0space'), 'emoji NB space')
        with self.assertRaises(ValueError): tokenize_basic_han('invalid\ud800')

    def test_metadata_column_postings_are_checked_too(self):
        self.ready();row=self.store.db.execute('SELECT * FROM lite_embeddings').fetchone()
        self.store.db.execute('DELETE FROM lite_embeddings_fts')
        self.store.db.execute('INSERT INTO lite_embeddings_fts(rowid,content,source_id,chunk_id,knowledge_id,knowledge_base_id) VALUES(?,?,?,?,?,?)',
            (row['id'],tokenize_basic_han(row['content']),'wrong-source',row['chunk_id'],row['knowledge_id'],row['knowledge_base_id']))
        self.store.db.commit();self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')

    def test_update_requires_new_version_and_retains_complete_ownership_history(self):
        previous=self.ready()['receipt'];old_id=self.store.db.execute('SELECT id FROM lite_embeddings').fetchone()[0]
        self.op.update(id='op-modify',kind='modify',previous=dict(self.desired),previous_receipt=previous)
        self.document['metadata'].update(content='changed Canary',version=2)
        self.chunks[0].update(content='changed Canary',content_revision=2)
        self.store.complete(self.op,self.document,self.chunks)
        result=self.verifier(self.op,self.document,self.chunks)
        self.assertEqual(result['state'],'ready');self.assertEqual(result['receipt']['documentVersion'],2)
        self.assertNotEqual(result['receipt']['backendInventoryHash'],previous['backendInventoryHash'])
        self.alter_receipt(lambda payload:payload['ownedRowIds'].remove(old_id))
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')

    def test_extra_chunk_and_vector_dimension_or_derived_policy_are_unknown(self):
        self.ready();self.store.db.execute('UPDATE lite_embeddings SET dimension=8');self.store.db.commit()
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')
        self.ready();self.store.db.execute('INSERT INTO chunks SELECT id||\'-extra\',tenant_id,knowledge_id,knowledge_base_id,content,context_header,content_revision,index_status,is_enabled,chunk_type,deleted_at FROM chunks')
        self.store.db.commit();self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')
        source=replace(self.source,processing=(('summary_enabled',True),))
        source=replace(source,approved_scope=source.scope)
        self.assertEqual(SQLiteKeywordProvider(source,self.store.path,clock=lambda:self.now[0]).observe(self.op,self.document,self.chunks)['state'],'unknown')

    def test_invalid_backend_hash_cannot_be_persisted_by_verifier(self):
        self.ready();proof=self.provider.observe(self.op,self.document,self.chunks)
        proof['backendInventoryHash']='not-a-sha256'
        self.assertEqual(self.verifier.verify(self.op,self.document,self.chunks,proof)['state'],'unknown')

    def test_unknown_path_never_creates_database_or_migrates_stock_schema(self):
        absent=self.root/'absent.sqlite3'
        self.assertEqual(SQLiteKeywordProvider(self.source,absent,clock=lambda:self.now[0]).observe(self.op,self.document,self.chunks)['state'],'unknown')
        self.assertFalse(absent.exists())
        self.store.db.execute('DROP TABLE sync_keyword_receipts');self.store.db.commit()
        before=self.store.path.read_bytes()
        self.assertEqual(self.provider.observe(self.op,self.document,self.chunks)['state'],'unknown')
        self.assertEqual(before,self.store.path.read_bytes())

    def test_actual_fts_sql_error_rolls_back_metadata_and_success_marker(self):
        self.store.db.execute('DROP TABLE lite_embeddings_fts');self.store.db.commit()
        with self.assertRaises(sqlite3.OperationalError): self.store.complete(self.op,self.document,self.chunks)
        for table in ['knowledges','chunks','lite_embeddings','sync_keyword_receipts']:
            self.assertEqual(self.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)
        self.store.db.execute(FTS_DDL.format(name='lite_embeddings_fts'));self.store.db.commit()
        self.assertEqual(self.ready()['state'],'ready')

    def test_legacy_receipt_format_and_metadata_only_basis_stay_compatible(self):
        previous=self.ready()['receipt']
        proof=self.provider.observe(self.op,self.document,self.chunks);proof.pop('backendInventoryHash')
        legacy=self.verifier.verify(self.op,self.document,self.chunks,proof)['receipt']
        self.assertNotIn('backendInventoryHash',legacy)
        self.op.update(id='op-provenance',kind='provenance',previous=dict(self.desired),previous_receipt=previous)
        self.desired.update(source_commit='3'*40,parent_commit='3'*40)
        self.document['custom_metadata']=copy.deepcopy(self.desired)
        self.store.complete(self.op,self.document,self.chunks)
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'ready')
        self.alter_receipt(lambda payload:payload.update(basisReceiptHash='0'*64))
        self.assertEqual(self.verifier(self.op,self.document,self.chunks)['state'],'unknown')

    def manifest(self,commit='1'*40,baseline=None,paths=('docs/a.md','docs/b.md')):
        docs=[dict(path=path,title='Canary '+path[-4],repo_id='canary',channel='local',source_key=self.source.binding.key(path),
                   source_commit=commit,source_blob_sha='2'*40,document_state='Accepted') for path in paths]
        manifest=dict(schema_version=1,mode='dry-run',repo_id='canary',channel='local',includes=['docs'],
            source_commit=commit,baseline_commit=baseline,documents=docs,changes=[],kb_write_performed=False,
            model_calls_performed=False,worktree_files_read=False,remote_refresh_performed=False,
            approval_scope=self.source.scope,processing_policy_hash='policy')
        manifest['manifest_sha256']=digest(manifest)
        return manifest

    def pipeline(self):
        ledger=Ledger(self.source.binding,self.source.ledger);self.addCleanup(ledger.close)
        rest=IndexedRest(self.source.binding.kb_id)
        adapter=RestAdapter(self.source,rest,synthetic=True,index_confirm=self.verifier,
                            content_loader=lambda source,op:'offline Canary '+op['desired']['path'])
        worker=Worker(ledger,adapter,clock=lambda:self.now[0],expected_scope=self.source.scope)
        return ledger,rest,adapter,worker

    def operation(self,ledger,job):
        row=next(row for row in ledger.operations(job) if row['state'] not in ('done','cancelled'))
        op=json.loads(row['payload']);op.update(document_id=row['document_id'],task_id=row['task_id'])
        return op

    def step(self,worker):
        result=worker.step();self.now[0]+=3;return result

    def test_whole_batch_checkpoint_waits_for_each_real_fts_receipt_and_survives_reopen(self):
        ledger,rest,adapter,worker=self.pipeline();job=ledger.enqueue(self.manifest())
        for position in range(2):
            self.assertEqual(self.step(worker),'awaiting_metadata');self.assertEqual(self.step(worker),'awaiting_index')
            self.assertEqual(self.step(worker),'awaiting_index');self.assertIsNone(ledger.checkpoint)
            op=self.operation(ledger,job);self.store.complete(op,rest.docs[op['document_id']],rest.chunks(op['document_id']))
            self.assertEqual(self.step(worker),'done')
            self.assertEqual(ledger.checkpoint,None if position==0 else '1'*40)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM index_receipts').fetchone()[0],2)
        ledger.close();reopened=Ledger(self.source.binding,self.source.ledger);self.addCleanup(reopened.close)
        self.assertEqual(reopened.checkpoint,'1'*40)
        self.assertEqual(reopened.db.execute('SELECT count(*) FROM index_receipts').fetchone()[0],2)

    def test_corrupt_actual_fts_blocks_outbox_without_advancing_checkpoint(self):
        ledger,rest,adapter,worker=self.pipeline();job=ledger.enqueue(self.manifest(paths=('docs/a.md',)))
        self.step(worker);self.step(worker);op=self.operation(ledger,job)
        self.store.complete(op,rest.docs[op['document_id']],rest.chunks(op['document_id']))
        self.store.db.execute('DELETE FROM lite_embeddings_fts');self.store.db.commit()
        self.assertEqual(self.step(worker),'blocked');self.assertIsNone(ledger.checkpoint)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM index_receipts').fetchone()[0],0)

    def test_client_restart_awaiting_index_polls_without_duplicate_submit(self):
        ledger,rest,adapter,worker=self.pipeline();job=ledger.enqueue(self.manifest(paths=('docs/a.md',)))
        self.step(worker);self.step(worker);op=self.operation(ledger,job)
        ledger.close();reopened=Ledger(self.source.binding,self.source.ledger);self.addCleanup(reopened.close)
        resumed=Worker(reopened,adapter,clock=lambda:self.now[0],expected_scope=self.source.scope)
        self.assertEqual(self.step(resumed),'awaiting_index')
        self.assertEqual(sum(method=='POST' for method,_,_ in rest.calls),1)
        self.store.complete(op,rest.docs[op['document_id']],rest.chunks(op['document_id']))
        self.assertEqual(self.step(resumed),'done');self.assertEqual(reopened.checkpoint,'1'*40)

    def test_delete_checkpoint_requires_persisted_native_terminal_after_client_restart(self):
        ledger,rest,adapter,worker=self.pipeline();job=ledger.enqueue(self.manifest(paths=('docs/a.md',)))
        self.step(worker);self.step(worker);op=self.operation(ledger,job)
        self.store.complete(op,rest.docs[op['document_id']],rest.chunks(op['document_id']));self.step(worker)
        deletion=ledger.enqueue(self.manifest(commit='3'*40,baseline='1'*40,paths=()),
            physical_deletions=('docs/a.md',),max_deletes=1,max_delete_fraction=1)
        self.assertEqual(self.step(worker),'awaiting_index');delete_op=self.operation(ledger,deletion)
        ledger.close();reopened=Ledger(self.source.binding,self.source.ledger);self.addCleanup(reopened.close)
        resumed=Worker(reopened,adapter,clock=lambda:self.now[0],expected_scope=self.source.scope)
        self.assertEqual(self.step(resumed),'awaiting_index');self.assertEqual(reopened.checkpoint,'1'*40)
        self.store.complete(delete_op,None,[])
        self.assertEqual(self.step(resumed),'done');self.assertEqual(reopened.checkpoint,'3'*40)
        self.assertEqual(sum(method=='DELETE' for method,_,_ in rest.calls),1)


if __name__=='__main__': unittest.main()
