"""Validate one already-retrieved pilot citation. No network or credentials."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sys
from urllib.parse import urlsplit, urlunsplit

TENANT = 1
KB = 'da1051a5-3fff-4198-ba55-017ea79df9c3'
DOCUMENT = '0390fad7-593f-4323-90f6-d4bd5426a44f'
CHUNK = '158e5cda-4450-4619-a755-54a01f7675e0'
TITLE = 'README-P1060.md'
MAX_CONTENT = 12000
LOCAL_SOURCE = Path('D:/code/shidai3/P1000/P1060/README-P1060.md')
LOCAL_CANONICAL = LOCAL_SOURCE.parent / 'README.md'


class CitationRejected(ValueError):
    pass


def data(envelope):
    if not isinstance(envelope, dict) or envelope.get('ok') is not True or not isinstance(envelope.get('data'), dict):
        raise CitationRejected('INVALID_CLI_ENVELOPE')
    return envelope['data']


def validate_document(envelope):
    doc = data(envelope)
    if doc.get('id') != DOCUMENT or doc.get('knowledge_base_id') != KB or doc.get('title') != TITLE:
        raise CitationRejected('DOCUMENT_SCOPE_MISMATCH')
    return doc


def safe_locator(value):
    if not isinstance(value, str):
        return value
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme not in ('http', 'https') and not (not scheme and parsed.netloc):
            return value
        host = parsed.hostname
        if not host:
            raise CitationRejected('DOCUMENT_URL_INVALID')
        if ':' in host:
            host = '[' + host + ']'
        if parsed.port:
            host += ':' + str(parsed.port)
        return urlunsplit((scheme, host, parsed.path, '', ''))
    except ValueError as error:
        raise CitationRejected('DOCUMENT_URL_INVALID') from error


def citation(document, chunk, binding):
    doc = validate_document(document)
    row = data(chunk)
    if (not isinstance(binding, dict) or binding.get('credentialBindingVerified') is not True or
            type(binding.get('tenantId')) is not int or binding['tenantId'] != TENANT or
            type(binding.get('keyId')) is not int or binding['keyId'] != 3 or binding.get('kbId') != KB):
        raise CitationRejected('READER_BINDING_MISMATCH')
    if row.get('id') != CHUNK or row.get('knowledge_id') != DOCUMENT:
        raise CitationRejected('CHUNK_PARENT_MISMATCH')
    # Some SDK versions omit derived KB/tenant fields. A validated parent is
    # mandatory; any explicitly supplied scope must also match.
    if row.get('knowledge_base_id') not in (None, '', KB) or row.get('tenant_id') not in (None, 0, TENANT):
        raise CitationRejected('CHUNK_SCOPE_MISMATCH')
    if row.get('tenant_id') is not None and type(row['tenant_id']) is not int:
        raise CitationRejected('CHUNK_SCOPE_MISMATCH')
    text = row.get('content')
    if not isinstance(text, str) or not text.strip() or '\x00' in text or len(text) > MAX_CONTENT:
        raise CitationRejected('CHUNK_CONTENT_INVALID_OR_TOO_LARGE')
    for field in ('chunk_index', 'start_at', 'end_at'):
        if type(row.get(field)) is not int or row[field] < 0:
            raise CitationRejected('CHUNK_LOCATOR_INVALID')
    if row['end_at'] < row['start_at']:
        raise CitationRejected('CHUNK_LOCATOR_INVALID')
    allowed = ('id', 'title', 'knowledge_base_id', 'file_name', 'file_path', 'file_hash', 'source',
               'type', 'created_at', 'updated_at', 'processed_at', 'parse_status')
    if any(doc.get(key) is not None and not isinstance(doc[key], str) for key in allowed):
        raise CitationRejected('DOCUMENT_METADATA_INVALID')
    if any(row.get(key) is not None and not isinstance(row[key], str) for key in ('content_hash', 'updated_at')):
        raise CitationRejected('CHUNK_METADATA_INVALID')
    document_metadata = {key: safe_locator(doc[key]) if key in ('file_path', 'source') else doc[key]
                         for key in allowed if key in doc}
    return {
        'schemaVersion': 1, 'checkedUtc': datetime.now(timezone.utc).isoformat(),
        'provenance': 'authenticated official CLI typed GET responses; no new search',
        'reader': {'tenantId': TENANT, 'keyId': 3, 'kbId': KB, 'credentialBindingVerified': True},
        'documentRequest': ['doc', 'view', DOCUMENT, '--format', 'json'],
        'chunkRequest': ['chunk', 'view', CHUNK, '--format', 'json'],
        'document': document_metadata,
        'citation': {
            'documentId': DOCUMENT, 'documentTitle': TITLE, 'knowledgeBaseId': KB,
            'chunkId': CHUNK, 'chunkIndex': row['chunk_index'], 'startAt': row['start_at'],
            'endAt': row['end_at'], 'content': text,
            'contentSHA256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'apiContentHash': row.get('content_hash'), 'apiUpdatedAt': row.get('updated_at'),
            'scopeValidation': 'exact known chunk and document; validated document KB; supplied chunk scope checked',
            'locatorUnits': 'API start_at/end_at values; not assumed to be Markdown line numbers',
        },
        'apiPassageReturned': True, 'credentialsRecorded': False,
        'freshness': {'state': 'unknown', 'reason': 'Compare API hash/version/content with the explicitly mapped local source; timestamps alone do not prove freshness.'},
    }


def local_comparison(result, source_bytes, canonical_bytes):
    text = result['citation']['content']
    def describe(path, raw):
        decoded = raw.decode('utf-8-sig')
        return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                'md5': hashlib.md5(raw, usedforsecurity=False).hexdigest(),
                'apiChunkIsExactSubstring': text in decoded,
                'apiChunkIsSubstringAfterLfNormalization': text.replace('\r\n', '\n') in decoded.replace('\r\n', '\n')}
    result['localFileComparison'] = [describe(LOCAL_SOURCE, source_bytes), describe(LOCAL_CANONICAL, canonical_bytes)]
    result['comparisonBoundary'] = 'API citation content above is independent of these local files. Local bytes never substitute for API content.'
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(1048577)
        if len(raw) > 1048576:
            raise CitationRejected('INPUT_TOO_LARGE')
        payload = json.loads(raw.decode('utf-8-sig'))
        result = citation(payload['document'], payload['chunk'], payload['binding'])
        local_comparison(result, LOCAL_SOURCE.read_bytes(), LOCAL_CANONICAL.read_bytes())
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (CitationRejected, KeyError, TypeError, ValueError, OSError):
        print(json.dumps({'ok': False, 'error': 'CITATION_VALIDATION_OR_LOCAL_COMPARISON_FAILED'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
