"""Pure index evidence validation. No database, HTTP, credentials or model access.

The injected provider must observe every approved index and the task terminal state.
This module validates its receipt; it does not manufacture production observations.
"""
import hashlib
import json
import math
import time

from offline_writer import digest, receipt_valid


def text_hash(value):
    if not isinstance(value,str):
        raise ValueError('content_observation_missing')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def reuses_index(op):
    previous=op.get('previous') or {}
    return (op['kind'] in ('rename','provenance') and previous.get('title')==op['desired']['title'] and
            previous.get('processing_policy_hash')==op['desired'].get('processing_policy_hash'))


def publish_version_advanced(op,metadata):
    """A metadata update is not proof that a content-affecting publish was applied."""
    if reuses_index(op):
        return True
    version=metadata.get('version')
    if not op.get('previous'):
        return type(version) is int and version>0
    previous=op.get('previous_receipt')
    baseline=previous.get('documentVersion') if receipt_valid(previous) else None
    return type(version) is int and type(baseline) is int and version>baseline


class IndexVerifier:
    """Provider interface: provider.observe(op, document, chunks) -> metadata-only proof.

    UNKNOWN is a blocked outcome, PENDING is safe read-only polling, READY is positive
    proof only. production_ready also requires a separately approved provider id and
    an explicit nonempty set of required indexes in the source policy.
    """
    def __init__(self,source,provider,*,clock=time.time,max_age_seconds=30):
        self.source,self.provider,self.clock=source,provider,clock
        self.max_age_seconds=max_age_seconds
        self.production_ready=(getattr(provider,'production_ready',False) is True and
            getattr(provider,'provider_id',None)==source.index_verifier and
            bool(source.index_verifier) and bool(source.required_indexes))

    def __call__(self,op,document,chunks):
        try:
            proof=self.provider.observe(op,document,chunks)
            return self.verify(op,document,chunks,proof)
        except Exception:
            return {'state':'unknown','reason':'index_evidence_unavailable'}

    def verify(self,op,document,chunks,proof):
        try:
            return self._verify(op,document,chunks,proof)
        except (ValueError,TypeError,KeyError,AttributeError):
            return {'state':'unknown','reason':'index_evidence_invalid'}

    def _verify(self,op,document,chunks,proof):
        desired=op['desired']
        target=self.source.binding
        if any(desired.get(k)!=v for k,v in dict(tenant_id=target.tenant_id,kb_id=target.kb_id,
            repo_id=target.repo_id,channel=target.channel,owner=target.owner).items()):
            raise ValueError('source_binding_mismatch')
        binding=dict(desired,document_id=op['document_id'])
        provider_id=getattr(self.provider,'provider_id',None)
        if (not isinstance(proof,dict) or type(proof.get('schemaVersion')) is not int or proof['schemaVersion']!=1 or
            not self.source.required_indexes or not provider_id or
            provider_id!=self.source.index_verifier or proof.get('providerId')!=provider_id or
            proof.get('binding')!=binding):
            raise ValueError('identity_not_attested')
        observed=proof.get('observedAt')
        age=self.clock()-observed if type(observed) in (int,float) else math.inf
        if not math.isfinite(age) or not 0<=age<=self.max_age_seconds:
            raise ValueError('stale_or_future_evidence')
        state=proof.get('state')
        if state in ('pending','failed','unknown'):
            return {'state':state,'reason':'index_'+state}
        if state!='ready' or proof.get('taskState')!='done':
            raise ValueError('terminal_state_not_attested')
        indexes=proof.get('indexes')
        if not isinstance(indexes,dict) or set(indexes)!=set(self.source.required_indexes):
            raise ValueError('incomplete_index_coverage')
        if op['kind']=='delete':
            if (document is not None or chunks or not op.get('task_id') or
                proof.get('taskId')!=op['task_id'] or proof.get('documentAbsent') is not True):
                raise ValueError('deletion_task_not_attested')
            for index in indexes.values():
                if not isinstance(index,dict) or index.get('state')!='done' or index.get('remainingChunkIds')!=[]:
                    raise ValueError('index_deletion_not_attested')
            content_sha,version,observed_chunks=None,None,[]
        else:
            if (not isinstance(document,dict) or document.get('id')!=op['document_id'] or
                document.get('knowledge_base_id')!=desired['kb_id'] or document.get('parse_status')!='completed'):
                raise ValueError('document_not_complete')
            metadata=document.get('metadata') or {}
            actual=document.get('custom_metadata') or {}
            if isinstance(actual,str):
                actual=json.loads(actual)
            if not isinstance(actual,dict) or any(actual.get(k)!=v for k,v in desired.items()):
                raise ValueError('source_metadata_not_observed')
            content_sha=text_hash(metadata.get('content'))
            version=metadata.get('version')
            if (type(version) not in (int,str) or type(version) is int and version<=0 or
                version=='' or type(proof.get('documentVersion')) is not type(version) or proof['documentVersion']!=version):
                raise ValueError('document_version_not_attested')
            if proof.get('contentSHA256')!=content_sha or not isinstance(chunks,list) or not chunks:
                raise ValueError('content_not_attested')
            observed_chunks=[]
            for chunk in chunks:
                if (not isinstance(chunk,dict) or not isinstance(chunk.get('id'),str) or not chunk['id'] or
                    chunk.get('knowledge_id')!=op['document_id']):
                    raise ValueError('chunk_identity_not_attested')
                observed_chunks.append(dict(id=chunk['id'],contentSHA256=text_hash(chunk.get('content'))))
            observed_chunks.sort(key=lambda c:c['id'])
            if len({c['id'] for c in observed_chunks})!=len(observed_chunks):
                raise ValueError('duplicate_chunks')
            if type(proof.get('documentChunkCount')) is not int or proof['documentChunkCount']!=len(observed_chunks):
                raise ValueError('document_chunk_count_not_attested')
            if not publish_version_advanced(op,metadata):
                raise ValueError('new_publish_version_not_attested')
            for index in indexes.values():
                if (not isinstance(index,dict) or index.get('state')!='done' or
                    type(index.get('documentVersion')) is not type(version) or index['documentVersion']!=version or
                    type(index.get('totalChunks')) is not int or index['totalChunks']!=len(observed_chunks) or
                    index.get('chunks')!=observed_chunks):
                    raise ValueError('incomplete_or_stale_index')
            if reuses_index(op):
                previous=op.get('previous_receipt')
                if (not receipt_valid(previous) or previous.get('documentId')!=op['document_id'] or
                    previous.get('blobSHA')!=desired['blob_sha'] or previous.get('contentSHA256')!=content_sha or
                    previous.get('documentVersion')!=version or previous.get('chunks')!=observed_chunks or
                    previous.get('indexes')!=sorted(indexes) or
                    getattr(self.provider,'production_ready',False) is True and previous.get('synthetic') is True or
                    proof.get('basisReceiptHash')!=previous['receiptHash']):
                    raise ValueError('unchanged_index_basis_missing')
        receipt=dict(schemaVersion=1,state='ready',providerId=provider_id,
            synthetic=getattr(self.provider,'production_ready',False) is not True,
            observedAt=observed,operationId=op['id'],documentId=op['document_id'],
            parentCommit=desired.get('parent_commit',desired['source_commit']),
            sourceCommit=desired['source_commit'],blobSHA=desired['blob_sha'],
            bindingHash=digest(binding),contentSHA256=content_sha,documentVersion=version,
            chunks=observed_chunks,indexes=sorted(indexes),taskId=op.get('task_id'),
            deleted=op['kind']=='delete',basisReceiptHash=proof.get('basisReceiptHash') if reuses_index(op) else None)
        backend_hash=proof.get('backendInventoryHash')
        if backend_hash is not None:
            if (not isinstance(backend_hash,str) or len(backend_hash)!=64 or
                any(character not in '0123456789abcdef' for character in backend_hash)):
                raise ValueError('backend_inventory_hash_invalid')
            receipt['backendInventoryHash']=backend_hash
        receipt['receiptHash']=digest(receipt)
        return {'state':'ready','receipt':receipt}
