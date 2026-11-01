"""Read-only observations from verified document/chunk REST routes.

Official REST exposes database chunk metadata, not retrieval-store inventory or
knowledge deletion task completion. This candidate deliberately cannot attest
READY or enable a production writer. Transport is injected; no credentials,
connections or service access are constructed here.
"""
import json
import time
from urllib.parse import quote

from index_verifier import text_hash
from offline_writer import digest


class OfficialAPIProvider:
    provider_id='official-api-metadata-observer'
    production_ready=False
    page_size=100
    max_pages=32

    def __init__(self,source,transport,*,clock=time.time):
        self.source,self.transport,self.clock=source,transport,clock

    def _scope(self,op):
        desired=op['desired']
        binding=self.source.binding
        expected=dict(tenant_id=binding.tenant_id,kb_id=binding.kb_id,repo_id=binding.repo_id,
                      channel=binding.channel,owner=binding.owner)
        if any(desired.get(k)!=v for k,v in expected.items()):
            raise ValueError('source_binding_mismatch')
        path=desired['path']
        if (not binding.contains(path) or
            any(path==p or path.startswith(p+'/') for p in self.source.excludes)):
            raise ValueError('source_path_outside_scope')
        identity=op['document_id']
        if not isinstance(identity,str) or not identity or len(identity)>256 or any(ord(c)<32 for c in identity):
            raise ValueError('invalid_document_identity')
        return quote(identity,safe='')

    def _tenant(self,row):
        value=row.get('tenant_id')
        if type(value) not in (int,str) or str(value)!=self.source.binding.tenant_id:
            raise ValueError('foreign_tenant_observation')

    def _document(self,identity,op):
        response=self.transport.request('GET','/api/v1/knowledge/'+identity)
        if response is None:
            return None # Transport's explicit GET 404 convention, not an index receipt.
        if not isinstance(response,dict) or response.get('success') is not True or not isinstance(response.get('data'),dict):
            raise ValueError('invalid_document_response')
        document=response['data']
        if document.get('id')!=op['document_id'] or document.get('knowledge_base_id')!=self.source.binding.kb_id:
            raise ValueError('foreign_document_observation')
        self._tenant(document)
        metadata=document.get('custom_metadata')
        if isinstance(metadata,str):
            metadata=json.loads(metadata)
        if not isinstance(metadata,dict) or any(metadata.get(k)!=v for k,v in op['desired'].items()):
            raise ValueError('source_metadata_mismatch')
        return document

    def _chunks(self,identity,op):
        rows,total,seen=[],None,set()
        for page in range(1,self.max_pages+1):
            response=self.transport.request('GET',f'/api/v1/chunks/{identity}?page={page}&page_size={self.page_size}')
            if (not isinstance(response,dict) or response.get('success') is not True or
                not isinstance(response.get('data'),list) or type(response.get('total')) is not int or response['total']<0 or
                type(response.get('page')) is not int or response['page']!=page or
                type(response.get('page_size')) is not int or response['page_size']!=self.page_size):
                raise ValueError('invalid_chunk_pagination')
            if total is None:
                total=response['total']
            if total!=response['total'] or total>self.max_pages*self.page_size:
                raise ValueError('unstable_or_excessive_chunk_total')
            values=response['data']
            if len(values)>self.page_size or len(rows)+len(values)>total:
                raise ValueError('chunk_total_overflow')
            for chunk in values:
                if (not isinstance(chunk,dict) or not isinstance(chunk.get('id'),str) or not chunk['id'] or
                    chunk['id'] in seen or chunk.get('knowledge_id')!=op['document_id'] or
                    chunk.get('knowledge_base_id')!=self.source.binding.kb_id or chunk.get('chunk_type')!='text' or
                    chunk.get('index_status') not in ('ready','processing','failed')):
                    raise ValueError('invalid_chunk_identity_or_state')
                self._tenant(chunk)
                text_hash(chunk.get('content'))
                seen.add(chunk['id']);rows.append(chunk)
            if len(rows)==total:
                return rows,total
            if not values:
                raise ValueError('incomplete_chunk_pagination')
        raise ValueError('chunk_pagination_limit')

    def _snapshot(self,op):
        identity=self._scope(op)
        before=self._document(identity,op)
        if before is None:
            return None,[],None
        if op['kind']=='delete' or before.get('parse_status')!='completed':
            return before,[],None
        rows,total=self._chunks(identity,op)
        after=self._document(identity,op)
        if after is None or digest(before)!=digest(after):
            raise ValueError('document_changed_during_observation')
        return after,rows,total

    @staticmethod
    def _chunk_signature(chunks):
        return sorted((c['id'],c['knowledge_id'],text_hash(c['content'])) for c in chunks)

    def observe(self,op,document,chunks):
        # Return metadata only. Do not turn a parse flag or DB chunk status into
        # proof of actual index inventory or a deletion task's terminal result.
        proof=dict(schemaVersion=1,providerId=self.provider_id,
                   binding=dict(op['desired'],document_id=op['document_id']),
                   observedAt=self.clock(),state='unknown')
        try:
            observed,rows,total=self._snapshot(op)
            if digest(observed)!=digest(document) or self._chunk_signature(rows)!=self._chunk_signature(chunks):
                raise ValueError('caller_observation_changed')
            if observed is None:
                proof['reason']='document_absence_has_no_index_or_task_attestation'
                return proof
            parse=observed.get('parse_status')
            if parse=='failed':
                proof.update(state='failed',reason='document_parse_failed')
            elif parse in ('pending','processing','finalizing') or op['kind']=='delete':
                proof.update(state='pending',reason='document_processing_pending')
            elif parse=='completed':
                metadata=observed.get('metadata') or {}
                version=metadata.get('version')
                if type(version) is not int or version<=0 or total==0:
                    raise ValueError('document_version_or_chunks_missing')
                statuses={c['index_status'] for c in rows}
                state='failed' if 'failed' in statuses else 'pending' if 'processing' in statuses else 'unknown'
                proof.update(state=state,reason='authoritative_index_inventory_and_task_unavailable',
                    observation=dict(documentVersion=version,contentSHA256=text_hash(metadata.get('content')),
                        textChunkCount=total,chunkSetHash=digest(self._chunk_signature(rows)),chunkStates=sorted(statuses)))
            else:
                proof['reason']='unrecognized_parse_state'
        except Exception:
            proof.update(state='unknown',reason='official_api_observation_unavailable')
        return proof
