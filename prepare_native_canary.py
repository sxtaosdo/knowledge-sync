"""Prepare nonsecret, exact first-round policies after Owner creates two new KBs.

Reads only committed Git objects and cached refs. Does not access credentials,
services, running databases, models, real ledgers or scheduled tasks.
"""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import time
import uuid

from index_verifier import text_hash
from native_canary_adapter import PATHS, PROVIDER_ID, require_canary
from offline_writer import Binding, Ledger
from source_registry import Registry, Source
from sync_service import Service
from weknora_adapter import PROCESS_KEYS, immutable_content

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
OLD_PILOT_ID = 'da1051a5-3fff-4198-ba55-017ea79df9c3'


def prepare(tenant_id, local_kb, official_kb, expected_head, expected_official, *, now=None):
    now = time.time() if now is None else now
    if type(tenant_id) is not int or tenant_id <= 0:
        raise ValueError('INVALID_TENANT_ID')
    ids = [str(uuid.UUID(value)) for value in (local_kb, official_kb)]
    if len(set(ids)) != 2 or OLD_PILOT_ID in ids or str(uuid.UUID(int=0)) in ids:
        raise ValueError('TWO_NEW_DISTINCT_KBS_REQUIRED')
    expires = int(now)+7*24*60*60
    sources, targets, manifests = [], [], []
    for channel, kb_id, expected, name in zip(('local', 'official'), ids,
        (expected_head, expected_official), ('shidai3-dev-canary', 'shidai3-official-canary')):
        source = Source('shidai3-canary', REPO, Binding('shidai3', channel, str(tenant_id), kb_id,
            owner='knowledge-sync', includes=PATHS),
            'HEAD' if channel == 'local' else 'refs/remotes/origin/main',
            ROOT/'state'/f'{channel}-canary.sync.sqlite3', writer_enabled=True, network_enabled=True,
            base_url='http://127.0.0.1:8081', credential_env=f'KNOWLEDGE_SYNC_{channel.upper()}_CANARY_KEY',
            processing=tuple(sorted((key, False) for key in PROCESS_KEYS)),
            index_verifier=PROVIDER_ID, required_indexes=('keyword',))
        source = replace(source, approved_scope=source.scope, approval_expires=expires)
        require_canary(source)
        if source.ledger.exists():
            raise ValueError('FIRST_ROUND_REQUIRES_ABSENT_LOCAL_LEDGER')
        manifest, deletions, renames = Service(Registry([source])).plan(source)
        if (manifest['source_commit'] != expected or deletions or renames or
            [doc['path'] for doc in manifest['documents']] != list(PATHS)):
            raise ValueError('APPROVED_COMMIT_OR_DOCUMENT_SET_CHANGED')
        ledger = Ledger(source.binding)  # Temporary in-memory plan, no persisted queue.
        try:
            job = ledger.enqueue(manifest)
            operations = [json.loads(row['payload']) for row in ledger.operations(job)]
        finally:
            ledger.close()
        documents = []
        for op in operations:
            if op['kind'] != 'add':
                raise ValueError('FIRST_ADDS_ONLY')
            content = immutable_content(source, op)
            documents.append(dict(contentSHA256=text_hash(content), desired=op['desired']))
        targets.append(dict(tenantId=tenant_id, kbId=kb_id, name=name, channel=channel, documents=documents))
        manifests.append(dict(channel=channel, batchId=job, manifest=manifest))
        item = dict(sourceId=source.source_id, repo=str(source.repo), repoId='shidai3', channel=channel,
            ref=source.ref, includes=list(PATHS), tenantId=str(tenant_id), kbId=kb_id,
            owner=source.binding.owner, ledger=str(source.ledger), allowUnmarked=False,
            maxDeletes=0, maxDeleteFraction=0, writerEnabled=True, approvedScope=source.scope,
            approvalExpires=expires, networkEnabled=True, baseUrl=source.base_url,
            credentialEnv=source.credential_env, processing=dict(source.processing),
            scheduleEnabled=False, intervalSeconds=0, gitlinks=[], indexVerifier=PROVIDER_ID,
            requiredIndexes=['keyword'])
        sources.append(item)
    return dict(schemaVersion=1, sources=sources), dict(schemaVersion=1,
        instanceId=str(uuid.uuid4()), expiresAt=expires, targets=targets), manifests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tenant-id', required=True, type=int)
    parser.add_argument('--local-kb-id', required=True)
    parser.add_argument('--official-kb-id', required=True)
    parser.add_argument('--expected-head', required=True)
    parser.add_argument('--expected-official', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise ValueError('OUTPUT_MUST_BE_NEW')
    sources, policy, manifests = prepare(args.tenant_id, args.local_kb_id, args.official_kb_id,
        args.expected_head, args.expected_official)
    output.mkdir(parents=True)
    for filename, data in [('candidate-sources.json', sources), ('service-canary-policy.json', policy),
                           ('frozen-plans.json', manifests)]:
        (output/filename).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(ok=True, output=str(output), expiresAt=policy['expiresAt'],
        documents=4, serviceWrites=False, credentialsAccessed=False, schedulerEnabled=False)))


if __name__ == '__main__':
    main()
