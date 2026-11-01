"""Fixed v0.8.2 REST contract. No network is constructed unless approved and enabled."""
import http.client
import json
import os
from urllib.parse import quote, urlsplit

from knowledge_sync import git, document_location, document_metadata, MAX_DOCUMENT_BYTES
from offline_writer import AuthorizationDenied, Retryable, UnknownOutcome, WriterDisabled
from source_registry import require_current_policy
from index_verifier import IndexVerifier, reuses_index, publish_version_advanced

PROCESS_KEYS={'summary_enabled','enable_multimodel','graph_enabled','question_generation_enabled','extract_enabled'}


def process_config(policy):
    if set(policy)!=PROCESS_KEYS or any(type(v) is not bool for v in policy.values()):
        raise WriterDisabled('explicit_processing_policy_required')
    return dict(summary_enabled=policy['summary_enabled'],enable_multimodel=policy['enable_multimodel'],
        graph_enabled=policy['graph_enabled'],question_generation_config={'enabled':policy['question_generation_enabled']},
        extract_config={'enabled':policy['extract_enabled']})


class NetworkTransport:
    """No proxy, redirects, TLS bypass, credential files or raw diagnostics."""
    def __init__(self,source):
        source.require_writer()
        address=urlsplit(source.base_url)
        if (address.username or address.password or address.query or address.fragment or
            address.path not in ('','/') or not address.hostname or
            not (address.scheme=='https' or address.scheme=='http' and address.hostname=='127.0.0.1')):
            raise WriterDisabled('approved_secure_endpoint_required')
        self.source,self.address=source,address

    def request(self,method,path,body=None):
        require_current_policy(self.source)
        key=os.environ.get(self.source.credential_env)
        if not key:
            raise WriterDisabled('writer_credential_unavailable')
        connection=(http.client.HTTPSConnection if self.address.scheme=='https' else http.client.HTTPConnection)(
            self.address.hostname,self.address.port,timeout=8)
        try:
            connection.request(method,path,body=json.dumps(body,ensure_ascii=False).encode('utf-8') if body is not None else None,
                headers={'X-API-Key':key,'Content-Type':'application/json'})
            response=connection.getresponse()
            if response.status in (401,403):
                raise AuthorizationDenied()
            if response.status==404 and method=='GET':
                return None
            if response.status==429 and method=='GET':
                raise Retryable(2)
            if response.status!=200:
                raise UnknownOutcome('non_success_response')
            raw=response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:
                raise UnknownOutcome('oversized_response')
            value=json.loads(raw)
            if not isinstance(value,dict) or value.get('success') is not True:
                raise UnknownOutcome('invalid_response')
            return value
        except (AuthorizationDenied,Retryable,WriterDisabled):
            raise
        except Exception:
            raise UnknownOutcome('transport_result_unknown') from None
        finally:
            connection.close()
            key=None


def immutable_content(source,op):
    desired=op['desired']
    path=desired['path']
    if (not source.binding.contains(path) or any(path==p or path.startswith(p+'/') for p in source.excludes) or
        desired['repo_id']!=source.binding.repo_id or desired['channel']!=source.binding.channel):
        raise AuthorizationDenied()
    location=document_location(source.repo,desired.get('parent_commit',desired['source_commit']),
                               path,source.binding.repo_id,source.gitlinks)
    if location is None or 'repo' not in location:
        raise AuthorizationDenied()
    mode,kind,blob=location['mode'],location['kind'],location['blob']
    if (mode not in ('100644','100755') or kind!='blob' or blob!=desired['blob_sha'] or
        location['source_commit']!=desired['source_commit'] or
        any(location[k]!=desired.get(k,location[k]) for k in ('source_repo_id','source_path','pin_chain'))):
        raise AuthorizationDenied()
    if int(git(location['repo'],'cat-file','-s',blob))>MAX_DOCUMENT_BYTES:
        raise AuthorizationDenied()
    content=git(location['repo'],'cat-file','blob',blob).decode('utf-8-sig')
    metadata,reason=document_metadata(content,path,source.allow_unmarked)
    if reason or metadata['title']!=desired['title']:
        raise AuthorizationDenied()
    return content


class RestAdapter:
    completion_evidence='document-chunks-plus-approved-index-attestation'
    def __init__(self,source,transport,*,content_loader=immutable_content,index_confirm=None,synthetic=False):
        self.source,self.transport=source,transport
        self.content_loader,self.index_confirm=content_loader,index_confirm
        self.synthetic=synthetic
        self.production_ready=(not synthetic and isinstance(index_confirm,IndexVerifier) and
                               index_confirm.production_ready and index_confirm.source.scope==source.scope)
        self.processing=process_config(dict(source.processing))

    def guard(self):
        self.source.require_writer()
        if not self.synthetic:
            require_current_policy(self.source)
        if not self.synthetic and (not self.production_ready or not os.environ.get(self.source.credential_env)):
            raise WriterDisabled('index_attestation_or_writer_credential_unavailable')

    def _request(self,method,path,body=None):
        self.guard()
        return self.transport.request(method,path,body)

    def _document(self,document_id):
        value=self._request('GET','/api/v1/knowledge/'+quote(document_id,safe=''))
        if value is None:
            return None
        doc=value['data']
        if doc.get('id')!=document_id or doc.get('knowledge_base_id')!=self.source.binding.kb_id:
            raise AuthorizationDenied()
        return doc

    @staticmethod
    def _metadata(doc):
        value=doc.get('custom_metadata') or {}
        if isinstance(value,str):
            value=json.loads(value)
        if not isinstance(value,dict):
            raise AuthorizationDenied()
        return value

    def inspect(self,document_id):
        doc=self._document(document_id)
        return dict(self._metadata(doc),document_id=document_id) if doc else None

    def _list(self,path):
        items=[]
        for page in range(1,33):
            value=self._request('GET',path+f'?page={page}&page_size=100')
            if value is None or not isinstance(value.get('data'),list) or type(value.get('total')) is not int:
                raise UnknownOutcome('invalid_pagination')
            rows=value['data']
            items.extend(rows)
            if len(items)>=value['total']:
                return items
            if not rows:
                raise UnknownOutcome('incomplete_pagination')
        raise UnknownOutcome('pagination_limit')

    def find_source(self,source_key):
        rows=self._list('/api/v1/knowledge-bases/'+quote(self.source.binding.kb_id,safe='')+'/knowledge')
        result=[]
        for doc in rows:
            meta=self._metadata(doc)
            if meta.get('source_key')==source_key:
                if doc.get('knowledge_base_id')!=self.source.binding.kb_id:
                    raise AuthorizationDenied()
                result.append(dict(meta,document_id=doc['id']))
        return result

    def submit(self,op):
        if op['kind']=='delete':
            response=self._request('DELETE','/api/v1/knowledge/'+quote(op['document_id'],safe=''))
            task=(response.get('data') or {}).get('task_id')
            if not task:
                raise UnknownOutcome('delete_task_missing')
            return {'document_id':op['document_id'],'task_id':task}
        if op['kind']!='add':
            return {'document_id':op['document_id']}
        payload=self._payload(op,'draft')
        response=self._request('POST','/api/v1/knowledge-bases/'+quote(self.source.binding.kb_id,safe='')+'/knowledge/manual',payload)
        doc=response['data']
        if doc.get('knowledge_base_id')!=self.source.binding.kb_id or not doc.get('id'):
            raise UnknownOutcome('create_identity_missing')
        return {'document_id':doc['id']}

    def _payload(self,op,status):
        return {'title':op['desired']['title'],'content':self.content_loader(self.source,op),
                'status':status,'channel':self.source.binding.channel,'process_config':self.processing}

    def finalize(self,op):
        doc=self._document(op['document_id'])
        if not doc:
            raise AuthorizationDenied()
        meta=self._metadata(doc)
        desired,previous=op['desired'],op['previous']
        if all(meta.get(k)==v for k,v in desired.items()):
            return
        if previous:
            if any(meta.get(k)!=v for k,v in previous.items() if k!='document_id'):
                raise AuthorizationDenied()
        elif meta or doc.get('type')!='manual' or (doc.get('metadata') or {}).get('status')!='draft' or (doc.get('metadata') or {}).get('content')!=self.content_loader(self.source,op):
            raise AuthorizationDenied()
        self._request('PUT','/api/v1/knowledge/'+quote(op['document_id'],safe=''),{'title':desired['title'],'custom_metadata':desired})
        actual=self.inspect(op['document_id'])
        if not actual or any(actual.get(k)!=v for k,v in desired.items()):
            raise UnknownOutcome('metadata_confirmation_missing')

    def publish(self,op):
        if reuses_index(op):
            return # Unchanged blob retains its prior index; only provenance/title changes.
        actual=self.inspect(op['document_id'])
        if not actual or any(actual.get(k)!=v for k,v in op['desired'].items()):
            raise AuthorizationDenied()
        self._request('PUT','/api/v1/knowledge/manual/'+quote(op['document_id'],safe=''),self._payload(op,'publish'))

    def reconcile(self,op):
        if op['kind']=='delete':
            if self._document(op['document_id']) is None:
                return {'state':'accepted','document_id':op['document_id']}
            return {'state':'ambiguous'}
        matches=self.find_source(op['desired']['source_key'])
        exact=[d for d in matches if all(d.get(k)==v for k,v in op['desired'].items())]
        if len(exact)==1:
            return {'state':'accepted','document_id':exact[0]['document_id']}
        # A lost draft-create reply has no durable source key yet. Absence proves nothing.
        return {'state':'ambiguous'}

    def reconcile_publish(self,op):
        doc=self._document(op['document_id'])
        if not doc or any(self._metadata(doc).get(k)!=v for k,v in op['desired'].items()):
            return {'state':'ambiguous'}
        if reuses_index(op):
            return {'state':'accepted'}
        metadata=doc.get('metadata') or {}
        if (metadata.get('status')=='publish' and metadata.get('content')==self.content_loader(self.source,op) and
            doc.get('parse_status') in ('pending','processing','finalizing','completed','failed') and
            (self.synthetic and not isinstance(self.index_confirm,IndexVerifier) or publish_version_advanced(op,metadata))):
            return {'state':'accepted'}
        return {'state':'ambiguous'}

    def poll(self,op):
        doc=self._document(op['document_id'])
        if op['kind']=='delete':
            # Document absence alone cannot attest index deletion. Approved attestation is mandatory.
            verdict=self._verify_index(op,None,[]) if doc is None else {'state':'pending'}
            return dict(verdict,document_id=op['document_id'])
        if not doc:
            return {'state':'failed','document_id':op['document_id']}
        chunks=[]
        if doc.get('parse_status')=='completed':
            chunks=self._list('/api/v1/chunks/'+quote(op['document_id'],safe=''))
        valid=bool(chunks) and all(c.get('knowledge_id')==op['document_id'] for c in chunks)
        valid=valid and (doc.get('metadata') or {}).get('content')==self.content_loader(self.source,op)
        verdict=self._verify_index(op,doc,chunks) if valid else {'state':'pending'}
        if doc.get('parse_status')=='failed':
            verdict={'state':'failed'}
        return dict(verdict,document_id=op['document_id'],parse_status=doc.get('parse_status'),chunks_observed=len(chunks))

    def _verify_index(self,op,doc,chunks):
        if self.index_confirm is None:
            return {'state':'pending'}
        verdict=self.index_confirm(op,doc,chunks)
        if self.synthetic and type(verdict) is bool:
            return {'state':'ready' if verdict else 'pending'}
        if not isinstance(verdict,dict) or verdict.get('state') not in ('ready','pending','failed','unknown'):
            return {'state':'unknown'}
        return dict(state=verdict['state'],index_receipt=verdict.get('receipt'))
