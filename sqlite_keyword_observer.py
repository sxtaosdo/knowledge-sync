"""Bounded, read-only SQLite keyword attestation for an offline canary.

No default DB path, migrations, credentials, sockets, or production registration.
The native service must eventually persist generation/ownership receipts and a
vocab projection. The stock service does neither. READY here is real FTS evidence
from a synthetic database; IndexVerifier labels its receipt synthetic.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from index_verifier import reuses_index, text_hash
from offline_writer import digest
from go_unicode15 import contains, trim_space

TOKENIZER_ID='weknora-3e8b0bfc-go-unicode15-unicode61-v2'
FTS_DDL="""CREATE VIRTUAL TABLE {name} USING fts5(
 content,source_id,chunk_id,knowledge_id,knowledge_base_id,
 content='',contentless_delete=1,tokenize='unicode61')"""
VOCAB_DDL="CREATE VIRTUAL TABLE lite_embeddings_fts_vocab USING fts5vocab(lite_embeddings_fts,'instance')"
PUNCTUATION='，。？！：；（）【】《》、—“”‘’…·→←'


def tokenize_basic_han(value):
    """Pinned Go algorithm using its complete frozen Unicode 15.0.0 tables."""
    if not isinstance(value,str):
        raise ValueError('text_missing')
    parts,han,word=[],[],[]
    def flush():
        if han:
            parts.extend([''.join(han)] if len(han)==1 else
                         [''.join(han[i:i+2]) for i in range(len(han)-1)])
            han.clear()
        if word:
            parts.append(''.join(word));word.clear()
    for char in value:
        if contains('Han',char):
            if word: parts.append(''.join(word));word.clear()
            han.append(char)
        elif any(contains(name,char) for name in ('White_Space','P','S')):
            flush()
        else:
            if han: flush()
            word.append(char)
    flush()
    return ' '.join(parts)


def index_content(title,chunk):
    # Upstream knowledge_index_content.go + chunker/splitter.go, Go TrimSpace.
    body=trim_space(chunk['content'])
    header=chunk['context_header'] or ''
    content=header+'\n\n'+body if header else body
    title=trim_space(title)
    return title+'\n'+content if title else content


def postings(db,vocab,row_id):
    return [tuple(row) for row in db.execute(
        f'SELECT term,col,offset FROM {vocab} WHERE doc=? ORDER BY term,col,offset',(row_id,))]


def expected_postings(row):
    with closing(sqlite3.connect(':memory:')) as scratch:
        scratch.execute(FTS_DDL.format(name='expected_fts'))
        scratch.execute("CREATE VIRTUAL TABLE expected_vocab USING fts5vocab(expected_fts,'instance')")
        scratch.execute('INSERT INTO expected_fts(rowid,content,source_id,chunk_id,knowledge_id,knowledge_base_id) VALUES(?,?,?,?,?,?)',
            (row['id'],tokenize_basic_han(row['content']),row['source_id'],row['chunk_id'],
             row['knowledge_id'],row['knowledge_base_id']))
        return postings(scratch,'expected_vocab',row['id'])


class SQLiteKeywordProvider:
    provider_id='sqlite-keyword-canary-observer'
    production_ready=False
    max_rows=128
    max_owned_rows=512

    def __init__(self,source,db_path,*,clock=time.time):
        self.source,self.db_path,self.clock=source,Path(db_path),clock

    def _scope(self,op):
        desired,binding=op['desired'],self.source.binding
        expected=dict(tenant_id=binding.tenant_id,kb_id=binding.kb_id,
            repo_id=binding.repo_id,channel=binding.channel,owner=binding.owner)
        if (any(desired.get(key)!=value for key,value in expected.items()) or
            not binding.contains(desired['path']) or
            any(desired['path']==p or desired['path'].startswith(p+'/') for p in self.source.excludes) or
            self.source.index_verifier!=self.provider_id or self.source.required_indexes!=('keyword',) or
            not self.source.approved(self.clock())):
            raise ValueError('source_scope_invalid')
        disabled={key:False for key in ('summary_enabled','enable_multimodel','graph_enabled',
                                      'question_generation_enabled','extract_enabled')}
        if dict(self.source.processing)!=disabled:
            raise ValueError('keyword_only_processing_required')
        if (not isinstance(op['id'],str) or not op['id'] or
            not isinstance(op['document_id'],str) or not op['document_id']):
            raise ValueError('operation_identity_missing')

    @staticmethod
    def _schema(db):
        def normalized(value): return ''.join(value.lower().split())
        for name,expected in [('lite_embeddings_fts',FTS_DDL.format(name='lite_embeddings_fts')),
                              ('lite_embeddings_fts_vocab',VOCAB_DDL)]:
            row=db.execute('SELECT type,sql FROM sqlite_master WHERE name=?',(name,)).fetchone()
            if not row or row['type']!='table' or normalized(row['sql'])!=normalized(expected):
                raise ValueError('unsupported_fts_schema')

    def _receipt(self,db,op):
        row=db.execute('SELECT * FROM sync_keyword_receipts WHERE operation_id=?',(op['id'],)).fetchone()
        if not row:
            return None
        desired=op['desired']
        payload=json.loads(row['payload'])
        if (str(row['tenant_id'])!=desired['tenant_id'] or row['knowledge_base_id']!=desired['kb_id'] or
            row['knowledge_id']!=op['document_id'] or payload.get('schemaVersion')!=1 or
            payload.get('binding')!=dict(desired,document_id=op['document_id']) or
            payload.get('kind')!=op['kind'] or payload.get('tokenizerId')!=TOKENIZER_ID or
            row['task_id']!=payload.get('taskId')):
            raise ValueError('receipt_binding_invalid')
        owned=payload.get('ownedRowIds')
        if (not isinstance(owned,list) or not owned or len(owned)>self.max_owned_rows or
            any(type(value) is not int or value<=0 for value in owned) or len(set(owned))!=len(owned)):
            raise ValueError('ownership_inventory_invalid')
        history=db.execute('SELECT tenant_id,knowledge_base_id,payload FROM sync_keyword_receipts WHERE knowledge_id=?',
                           (op['document_id'],)).fetchmany(self.max_owned_rows+1)
        if len(history)>self.max_owned_rows: raise ValueError('receipt_history_limit')
        for old in history:
            if str(old['tenant_id'])!=desired['tenant_id'] or old['knowledge_base_id']!=desired['kb_id']:
                raise ValueError('receipt_history_scope_invalid')
            if not set(json.loads(old['payload'])['ownedRowIds'])<=set(owned):
                raise ValueError('ownership_history_incomplete')
        return row,payload

    @staticmethod
    def _absent_rows(db,row_ids):
        for row_id in row_ids:
            if (db.execute('SELECT 1 FROM lite_embeddings WHERE id=?',(row_id,)).fetchone() or
                db.execute('SELECT 1 FROM lite_embeddings_fts WHERE rowid=?',(row_id,)).fetchone()):
                raise ValueError('retired_index_row_remains')

    def _observe(self,db,op,document,chunks,proof):
        self._schema(db)
        receipt=self._receipt(db,op)
        if receipt is None:
            return dict(proof,state='pending',reason='durable_generation_receipt_missing')
        terminal,payload=receipt
        if terminal['state'] in ('queued','running','failed'):
            return dict(proof,state='failed' if terminal['state']=='failed' else 'pending',reason='native_task_not_done')
        if terminal['state']!='done': raise ValueError('task_terminal_invalid')
        identity,desired=op['document_id'],op['desired']
        stored=db.execute('SELECT * FROM knowledges WHERE id=? AND deleted_at IS NULL',(identity,)).fetchone()
        chunk_rows=db.execute('SELECT * FROM chunks WHERE knowledge_id=? AND deleted_at IS NULL ORDER BY id',(identity,)).fetchmany(self.max_rows+1)
        rows=db.execute('SELECT * FROM lite_embeddings WHERE knowledge_id=? ORDER BY id',(identity,)).fetchmany(self.max_rows+1)
        if len(chunk_rows)>self.max_rows or len(rows)>self.max_rows:
            raise ValueError('canary_inventory_limit')
        # Metadata columns are indexed even though FTS is contentless. This also
        # finds stale/orphan FTS rows after a metadata-row deletion.
        query='knowledge_id : "'+identity.replace('"','""')+'"'
        fts_ids={r[0] for r in db.execute('SELECT rowid FROM lite_embeddings_fts WHERE lite_embeddings_fts MATCH ?',
                                         (query,)).fetchmany(self.max_rows+1)}
        if fts_ids!={r['id'] for r in rows}: raise ValueError('orphan_or_missing_fts_rows')
        if op['kind']=='delete':
            if (document is not None or chunks or stored is not None or chunk_rows or rows or
                not op.get('task_id') or terminal['task_id']!=op['task_id']):
                raise ValueError('delete_terminal_or_absence_invalid')
            self._absent_rows(db,payload['ownedRowIds'])
            return dict(proof,state='ready',taskState='done',taskId=terminal['task_id'],documentAbsent=True,
                indexes={'keyword':dict(state='done',remainingChunkIds=[])},backendInventoryHash=digest(payload['ownedRowIds']))
        if (not stored or str(stored['tenant_id'])!=desired['tenant_id'] or
            stored['knowledge_base_id']!=desired['kb_id'] or stored['type']!='manual' or
            stored['parse_status']!='completed' or stored['pending_subtasks_count']!=0 or
            stored['title']!=desired['title'] or not isinstance(document,dict)):
            raise ValueError('document_snapshot_invalid')
        metadata=json.loads(stored['metadata']);custom=json.loads(stored['custom_metadata'])
        version=metadata.get('version')
        if (type(version) is not int or version<=0 or terminal['generation']!=version or
            payload.get('documentVersion')!=version or metadata.get('status')!='publish' or
            document.get('id')!=identity or document.get('knowledge_base_id')!=desired['kb_id'] or
            document.get('title')!=stored['title'] or document.get('parse_status')!='completed' or
            document.get('metadata')!=metadata or
            any(custom.get(k)!=v for k,v in desired.items()) or document.get('custom_metadata')!=custom):
            raise ValueError('document_generation_invalid')
        selected=[]
        for chunk in chunk_rows:
            if str(chunk['tenant_id'])!=desired['tenant_id'] or chunk['knowledge_base_id']!=desired['kb_id']:
                raise ValueError('chunk_scope_invalid')
            if chunk['chunk_type']=='parent': continue
            if chunk['chunk_type']!='text' or chunk['is_enabled']!=1 or chunk['index_status']!='ready':
                raise ValueError('unsupported_canary_chunk')
            selected.append(chunk)
        signature=lambda values: sorted((c['id'],c['knowledge_id'],text_hash(c['content'])) for c in values)
        if not selected or not isinstance(chunks,list) or signature(selected)!=signature(chunks):
            raise ValueError('caller_chunk_inventory_changed')
        if len(rows)!=len(selected): raise ValueError('index_inventory_incomplete')
        by_chunk={c['id']:c for c in selected};inventory=[]
        for row in rows:
            chunk=by_chunk.pop(row['chunk_id'],None)
            if (chunk is None or row['source_id']!=row['chunk_id'] or row['source_type']!=0 or
                row['knowledge_base_id']!=desired['kb_id'] or row['dimension']!=0 or row['is_enabled']!=1 or
                row['content']!=index_content(stored['title'],chunk)):
                raise ValueError('index_mapping_or_input_invalid')
            actual=postings(db,'lite_embeddings_fts_vocab',row['id'])
            if actual!=expected_postings(row): raise ValueError('fts_postings_mismatch')
            inventory.append(dict(rowId=row['id'],sourceId=row['source_id'],sourceType=row['source_type'],
                chunkId=row['chunk_id'],chunkRevision=chunk['content_revision'],
                inputSHA256=text_hash(row['content']),postingsSHA256=digest(actual)))
        if payload.get('indexRows')!=inventory or not set(r['id'] for r in rows)<=set(payload['ownedRowIds']):
            raise ValueError('generation_inventory_invalid')
        self._absent_rows(db,set(payload['ownedRowIds'])-{r['id'] for r in rows})
        if reuses_index(op) and payload.get('basisReceiptHash')!=(op.get('previous_receipt') or {}).get('receiptHash'):
            raise ValueError('basis_receipt_invalid')
        observed=sorted([dict(id=c['id'],contentSHA256=text_hash(c['content'])) for c in selected],key=lambda c:c['id'])
        return dict(proof,state='ready',taskState='done',documentVersion=version,
            documentChunkCount=len(selected),contentSHA256=text_hash(metadata.get('content')),
            basisReceiptHash=payload.get('basisReceiptHash'),backendInventoryHash=digest(inventory),
            indexes={'keyword':dict(state='done',documentVersion=version,totalChunks=len(selected),chunks=observed)})

    def observe(self,op,document,chunks):
        proof=dict(schemaVersion=1,providerId=self.provider_id,
            binding=dict(op['desired'],document_id=op['document_id']),observedAt=self.clock(),state='unknown')
        try:
            self._scope(op)
            if not self.db_path.is_absolute() or self.db_path.is_symlink() or not self.db_path.is_file():
                raise ValueError('explicit_readonly_database_required')
            with closing(sqlite3.connect(self.db_path.as_uri()+'?mode=ro',uri=True)) as db:
                db.row_factory=sqlite3.Row
                db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
                return self._observe(db,op,document,chunks,proof)
        except Exception:
            return dict(proof,state='unknown',reason='sqlite_keyword_attestation_unavailable')
