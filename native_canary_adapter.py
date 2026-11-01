"""Explicit native canary contract; only the approved first two additions.

Imports create no transport, credentials, database, scheduler or process.
The server must attest its enabled exact KB scope before the worker can write.
"""
import os
import time
from urllib.parse import quote

from index_verifier import IndexVerifier
from offline_writer import AuthorizationDenied, UnknownOutcome, WriterDisabled
from source_registry import require_current_policy
from weknora_adapter import RestAdapter, NetworkTransport, immutable_content, PROCESS_KEYS

PROVIDER_ID = 'native-keyword-canary-v1'
PATHS = ('AGENTS.md', 'AI_CONTEXT.md')


class NativeProvider:
    provider_id = PROVIDER_ID
    production_ready = True  # Code contract; runtime readiness requires the server probe.

    def __init__(self, *, synthetic=False):
        self.production_ready = not synthetic


def require_canary(source):
    if (source.binding.repo_id != 'shidai3' or tuple(sorted(source.binding.includes)) != PATHS or
        source.gitlinks or source.excludes or source.allow_unmarked or source.max_deletes != 0 or
        source.max_delete_fraction != 0 or source.schedule_enabled or source.interval_seconds != 0 or
        source.index_verifier != PROVIDER_ID or source.required_indexes != ('keyword',) or
        dict(source.processing) != {key: False for key in PROCESS_KEYS}):
        raise WriterDisabled('exact_first_canary_policy_required')


class NativeCanaryAdapter(RestAdapter):
    def __init__(self, source, transport, *, content_loader=immutable_content, synthetic=False, clock=time.time):
        require_canary(source)
        verifier = IndexVerifier(source, NativeProvider(synthetic=synthetic), clock=clock)
        super().__init__(source, transport, content_loader=content_loader,
                         index_confirm=verifier, synthetic=synthetic)
        self.clock = clock
        self.prefix = '/api/v1/knowledge-bases/' + quote(source.binding.kb_id, safe='') + '/sync-canary'

    def runtime_probe(self):
        value = self.transport.request('GET', self.prefix + '/capabilities')
        data = value.get('data') if isinstance(value, dict) else None
        if (not isinstance(data, dict) or data.get('enabled') is not True or
            data.get('providerId') != PROVIDER_ID or str(data.get('tenantId')) != self.source.binding.tenant_id or
            data.get('kbId') != self.source.binding.kb_id or data.get('channel') != self.source.binding.channel or
            data.get('keywordOnly') is not True or data.get('deleteAllowed') is not False or
            data.get('firstAddsOnly') is not True or data.get('documentPaths') != list(PATHS) or
            type(data.get('expiresAt')) not in (int, float) or data['expiresAt'] <= self.clock()):
            raise WriterDisabled('native_canary_not_attested')
        return True

    def guard(self):
        self.source.require_writer(self.clock())
        require_canary(self.source)
        if not self.synthetic:
            require_current_policy(self.source)
            if not os.environ.get(self.source.credential_env):
                raise WriterDisabled('writer_credential_unavailable')
        self.runtime_probe()

    def _observation(self, op):
        value = self._request('GET', self.prefix + '/operations/' + quote(op['id'], safe=''))
        if value is None:
            return None
        data = value.get('data')
        operation = data.get('operation') if isinstance(data, dict) else None
        proof = data.get('proof') if isinstance(data, dict) else None
        if (not isinstance(operation, dict) or operation.get('operationId') != op['id'] or
            not isinstance(operation.get('documentId'), str) or not operation['documentId'] or
            not isinstance(operation.get('taskId'), str) or not operation['taskId'] or
            operation.get('state') not in ('queued', 'running', 'failed', 'done') or
            op.get('document_id') and op['document_id'] != operation['documentId'] or
            op.get('task_id') and op['task_id'] != operation['taskId'] or
            not isinstance(proof, dict) or
            proof.get('binding') != dict(op['desired'], document_id=operation['documentId'])):
            raise UnknownOutcome('native_operation_binding_missing')
        return data

    def submit(self, op):
        if op['kind'] != 'add' or op.get('document_id') or op.get('previous'):
            raise AuthorizationDenied()
        value = self._request('POST', self.prefix + '/operations',
            dict(operationId=op['id'], kind='add', desired=op['desired'],
                 content=self.content_loader(self.source, op)))
        result = value.get('data') or {}
        if (result.get('operationId') != op['id'] or result.get('state') not in ('queued', 'done') or
            not result.get('documentId') or not result.get('taskId')):
            raise UnknownOutcome('native_acceptance_missing')
        return dict(document_id=result['documentId'], task_id=result['taskId'])

    def finalize(self, op):
        if self._observation(op) is None:
            raise UnknownOutcome('native_operation_missing')

    def publish(self, op):
        self.finalize(op)  # The captured server operation already queued the immutable content.

    def reconcile(self, op):
        data = self._observation(op)
        if data is None:
            return {'state': 'ambiguous'}
        result = data['operation']
        return dict(state='accepted', document_id=result['documentId'], task_id=result['taskId'])

    def reconcile_publish(self, op):
        return {'state': 'accepted' if self._observation(op) is not None else 'ambiguous'}

    def poll(self, op):
        data = self._observation(op)
        if data is None:
            return dict(state='unknown', document_id=op['document_id'])
        document, chunks, proof = data.get('document'), data.get('chunks'), data['proof']
        verdict = self.index_confirm.verify(op, document, chunks, proof)
        if verdict['state'] == 'ready' and proof.get('taskId') != op.get('task_id'):
            return dict(state='unknown', document_id=op['document_id'])
        return dict(state=verdict['state'], document_id=op['document_id'],
            index_receipt=verdict.get('receipt'), parse_status=(document or {}).get('parse_status'),
            chunks_observed=len(chunks or []))


def native_adapter_factory(source):
    require_canary(source)
    return NativeCanaryAdapter(source, NetworkTransport(source))
