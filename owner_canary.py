"""Owner-operated fixed-scope handoff. Default invocation is offline preview."""
from __future__ import annotations
import ctypes
from contextlib import closing, contextmanager
from ctypes import wintypes
from datetime import datetime, timezone
import json
import hashlib
import hmac
import os
from pathlib import Path
import sqlite3
import sys
import uuid
import urllib.error
import urllib.request

HOST = 'http://127.0.0.1:8081'
DB = Path('D:/knowledge-data/weknora/pilot-access/weknora.db')
STATE = Path('D:/services/weknora/pilot-access/owner-canary-handoff-state.json')
PRIVATE = Path('D:/services/weknora/cli/private')
OLD_KB = 'da1051a5-3fff-4198-ba55-017ea79df9c3'
NAMES = {'local': 'shidai3-dev-canary', 'official': 'shidai3-official-canary'}
VARS = {'local': 'KNOWLEDGE_SYNC_LOCAL_CANARY_KEY', 'official': 'KNOWLEDGE_SYNC_OFFICIAL_CANARY_KEY'}
HTTP_STAGES = {'/health': 'service-health', '/api/v1/auth/login': 'auth-login',
    '/api/v1/auth/me': 'auth-identity', '/api/v1/knowledge-bases': 'kb-create',
    '/api/v1/tenants/1/api-keys': 'writer-create'}
SAFE_STAGES = set(HTTP_STAGES.values()) | {'kb-detail'}
SAFE_TRANSPORTS = {'HTTP_STATUS', 'ACCESS_DENIED', 'CONNECTION_REFUSED',
    'CONNECTION_RESET', 'TIMEOUT', 'INVALID_JSON', 'UNAVAILABLE'}

class Stop(Exception):
    """Only fixed codes may leave the process."""
    def __init__(self, code, stage=None, transport=None, http_status=None):
        self.code = code
        self.stage = stage if stage in SAFE_STAGES else None
        self.transport = transport if transport in SAFE_TRANSPORTS else None
        self.http_status = http_status if isinstance(http_status, int) and 100 <= http_status <= 599 else None
        super().__init__(code)

def public_stop(error, kind='blocked'):
    output = dict(kind=kind, code=error.code)
    for field, value in [('stage', error.stage), ('transport', error.transport), ('httpStatus', error.http_status)]:
        if value is not None:
            output[field] = value
    return output

def http_failure(error, stage):
    # Never inspect the exception message, response body, URL, headers or input.
    status = error.code if isinstance(error, urllib.error.HTTPError) else None
    if status is not None:
        transport = 'HTTP_STATUS'
        # Official Login returns 401 for a rejected login. Do not apply this
        # classification to creation routes: their UNKNOWN recovery stays intact.
        if stage == 'auth-login' and status == 401:
            return Stop('AUTH_REJECTED', stage=stage, transport=transport, http_status=status)
    else:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        number = getattr(reason, 'winerror', None) or getattr(reason, 'errno', None)
        transport = {10013: 'ACCESS_DENIED', 13: 'ACCESS_DENIED',
            10061: 'CONNECTION_REFUSED', 111: 'CONNECTION_REFUSED',
            10054: 'CONNECTION_RESET', 104: 'CONNECTION_RESET',
            10060: 'TIMEOUT', 110: 'TIMEOUT'}.get(number, 'UNAVAILABLE')
        if isinstance(reason, TimeoutError):
            transport = 'TIMEOUT'
        elif isinstance(error, json.JSONDecodeError):
            transport = 'INVALID_JSON'
    return Stop('HTTP_RESULT_UNKNOWN', stage=stage, transport=transport, http_status=status)

def need(condition, code='CONTRACT_REJECTED'):
    if not condition:
        raise Stop(code)

def kb_body(channel, instance):
    return dict(name=NAMES[channel], type='document', is_temporary=False,
        description=f'owner-canary:{instance}:{channel}',
        indexing_strategy=dict(keyword_enabled=True, vector_enabled=False,
            wiki_enabled=False, graph_enabled=False),
        embedding_model_id='', summary_model_id='', vector_store_id=None,
        profile_config=None, vlm_config=dict(enabled=False, model_id=''),
        asr_config=dict(enabled=False, model_id=''), extract_config=dict(enabled=False),
        question_generation_config=dict(enabled=False),
        auto_tag_config=dict(enabled=False, model_id=''))

def unix(value):
    need(isinstance(value, str), 'EXPIRY_INVALID')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # SQLite stores UTC times without an offset in some existing rows.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (ValueError, OverflowError):
        raise Stop('EXPIRY_INVALID') from None

def valid_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value and value != OLD_KB
    except (ValueError, AttributeError):
        return False

def validate_kb(data, channel, instance):
    expected = kb_body(channel, instance)
    need(isinstance(data, dict) and valid_uuid(data.get('id')), 'KB_ID_INVALID')
    need(data.get('tenant_id') == 1 and data.get('name') == NAMES[channel], 'KB_BINDING_INVALID')
    for field, value in expected.items():
        if field in ('name', 'description', 'type', 'is_temporary', 'indexing_strategy',
                     'embedding_model_id', 'summary_model_id', 'vector_store_id', 'profile_config'):
            need(data.get(field) == value, 'KB_CONFIGURATION_INVALID')
        else:
            actual = data.get(field)
            need(isinstance(actual, dict) and actual.get('enabled') is False, 'KB_PROCESSING_INVALID')
            need(actual.get('model_id', '') == '', 'KB_PROCESSING_INVALID')
    need(data.get('knowledge_count') == 0 and data.get('chunk_count') == 0
         and data.get('is_processing') is False and data.get('processing_count') == 0,
         'KB_NOT_EMPTY')
    return data['id']

def validate_writer(row, channel, kb_id, step, now):
    need(isinstance(row, dict), 'WRITER_METADATA_INVALID')
    need(type(row.get('id')) is int and row['id'] > 0, 'WRITER_ID_INVALID')
    need(row.get('tenant_id', 1) == 1 and row.get('scope_type') == 'tenant'
         and row.get('name') == NAMES[channel]+'-writer', 'WRITER_BINDING_INVALID')
    need(row.get('full_access') in (False, 0) and row.get('knowledge_base_ids') == [kb_id]
         and sorted(row.get('capabilities', [])) == ['ingest', 'retrieve']
         and row.get('revoked_at') is None, 'WRITER_SCOPE_INVALID')
    end = unix(row.get('expires_at'))
    created = unix(row.get('created_at'))
    need(end == step['expires_unix'] and end > now, 'WRITER_EXPIRY_INVALID')
    need(0 <= created-step['submitted_unix'] <= 60
         and 604740 <= end-created <= 604800, 'WRITER_CREATION_TIME_INVALID')
    if step.get('id') is not None:
        need(row['id'] == step['id'], 'WRITER_ID_CHANGED')
    return row['id']

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class OfficialHTTP:
    """Fixed localhost, no proxy/redirect/retry; response/errors never logged."""
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def __call__(self, method, path, body=None, bearer=None, key=None):
        import re
        allowed = ((method, path) in {
            ('GET', '/health'),
            ('POST', '/api/v1/auth/login'), ('GET', '/api/v1/auth/me'),
            ('POST', '/api/v1/knowledge-bases'), ('POST', '/api/v1/tenants/1/api-keys')}
            or method == 'GET' and re.fullmatch('/api/v1/knowledge-bases/[0-9a-f-]{36}', path))
        need(allowed, 'ROUTE_NOT_ALLOWED')
        stage = HTTP_STAGES.get(path, 'kb-detail')
        if path == '/health':
            need(body is None and bearer is None and key is None, 'HEALTH_ANONYMOUS_ONLY')
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if bearer:
            headers['Authorization'] = 'Bearer '+bearer
        if key:
            headers['X-API-Key'] = key
        request = urllib.request.Request(HOST+path, method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode('utf-8'))
        try:
            with self.opener.open(request, timeout=3 if path == '/health' else 15) as response:
                raw = response.read(1048577)
                status = response.status
            need(status in (200, 201) and len(raw) <= 1048576, 'HTTP_RESPONSE_INVALID')
            value = json.loads(raw)
            if path == '/health':
                need(status == 200 and isinstance(value, dict) and value.get('status') == 'ok', 'SERVICE_HEALTH_UNAVAILABLE')
                return dict(status='ok')
            need(isinstance(value, dict) and value.get('success') is True, 'HTTP_RESPONSE_INVALID')
            return value
        except Stop as error:
            error.stage = stage
            raise
        except Exception as error:
            raise http_failure(error, stage) from None
        finally:
            headers.clear()
            body = bearer = key = request = None

class ReadOnlyMetadata:
    """Only exact new names and nonsecret columns, following ReaderMetadataGuard."""
    def __init__(self, path=DB):
        self.path = path

    def rows(self, kind, channel):
        name = NAMES[channel] + ('-writer' if kind == 'writer' else '')
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                if kind == 'kb':
                    result = db.execute(
                        'SELECT id,name,tenant_id,description FROM knowledge_bases '
                        'WHERE name=? AND deleted_at IS NULL', (name,)).fetchall()
                else:
                    result = db.execute(
                        'SELECT id,name,tenant_id,scope_type,full_access,knowledge_base_ids,'
                        'capabilities,expires_at,revoked_at,created_at FROM tenant_api_keys '
                        'WHERE name=?', (name,)).fetchall()
                output = [dict(row) for row in result]
                if kind == 'writer':
                    for row in output:
                        for field in ('knowledge_base_ids', 'capabilities'):
                            row[field] = json.loads(row[field])
                return output
        except Exception:
            raise Stop('NONSECRET_METADATA_UNAVAILABLE') from None

    def matches_key(self, channel, key_id, token):
        """Owner-only local binding to this newly created ID; no old key/hash read."""
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
                db.execute('PRAGMA query_only=ON')
                rows = db.execute('SELECT key_hash FROM tenant_api_keys '
                    "WHERE id=? AND tenant_id=1 AND name=? AND scope_type='tenant' "
                    'AND full_access=0 AND revoked_at IS NULL',
                    (key_id, NAMES[channel]+'-writer')).fetchall()
            if len(rows) != 1:
                return False
            expected = rows[0][0]
            if isinstance(expected, bytes):
                expected = expected.decode('ascii')
            actual = hashlib.sha256(token.encode('utf-8')).hexdigest()
            return isinstance(expected, str) and hmac.compare_digest(expected, actual)
        except Exception:
            raise Stop('NEW_WRITER_LOCAL_BINDING_UNAVAILABLE') from None
        finally:
            token = expected = actual = rows = None

    def assert_empty(self, channel, kb_id):
        need(valid_uuid(kb_id), 'KB_ID_INVALID')
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                binding = db.execute('SELECT count(*) FROM knowledge_bases '
                    'WHERE id=? AND tenant_id=1 AND name=? AND deleted_at IS NULL',
                    (kb_id, NAMES[channel])).fetchone()[0]
                need(binding == 1, 'KB_BINDING_INVALID')
                # Match native canary preflight: include soft-deleted/orphan records.
                counts = {table: db.execute('SELECT count(*) FROM '+table+
                    ' WHERE knowledge_base_id=?', (kb_id,)).fetchone()[0]
                    for table in ('knowledges', 'chunks', 'lite_embeddings')}
                counts['fts'] = db.execute('SELECT count(*) FROM lite_embeddings_fts '
                    'WHERE lite_embeddings_fts MATCH ?',
                    ('knowledge_base_id:"'+kb_id+'"',)).fetchone()[0]
                counts['pending_ops'] = db.execute('SELECT count(*) FROM task_pending_ops '
                    "WHERE tenant_id=1 AND ((scope='knowledge_base' AND scope_id=?) OR "
                    "(scope='knowledge' AND scope_id IN (SELECT id FROM knowledges WHERE knowledge_base_id=?)))",
                    (kb_id, kb_id)).fetchone()[0]
                counts['processing_spans'] = db.execute('SELECT count(*) FROM knowledge_processing_spans s '
                    'JOIN knowledges k ON s.knowledge_id=k.id WHERE k.knowledge_base_id=? '
                    "AND s.status IN ('pending','running')", (kb_id,)).fetchone()[0]
                native = db.execute("SELECT count(*) FROM sqlite_master "
                    "WHERE type='table' AND name='sync_canary_operations'").fetchone()[0]
                counts['native_operations'] = (db.execute('SELECT count(*) FROM sync_canary_operations '
                    'WHERE kb_id=?', (kb_id,)).fetchone()[0] if native else 0)
                need(all(type(value) is int and value == 0 for value in counts.values()),
                    'KB_OR_INDEX_NOT_EMPTY')
                return counts
        except Stop:
            raise
        except Exception:
            raise Stop('EMPTY_KB_METADATA_UNAVAILABLE') from None

class DPAPI:
    """Same CurrentUser hex/UTF-16 DPAPI format as ConvertFrom-SecureString."""
    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    def crypt(self, raw, protect):
        need(os.name == 'nt', 'WINDOWS_DPAPI_REQUIRED')
        buf = ctypes.create_string_buffer(raw)
        source = self.Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        dest = self.Blob()
        crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(self.Blob), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(self.Blob)]
        fn.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        try:
            need(fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest)),
                 'DPAPI_FAILED')
            return ctypes.string_at(dest.pbData, dest.cbData)
        finally:
            ctypes.memset(buf, 0, len(raw))
            if dest.pbData:
                ctypes.memset(dest.pbData, 0, dest.cbData)
                kernel.LocalFree(dest.pbData)

    def seal(self, value):
        return self.crypt(value.encode('utf-16-le'), True).hex()

    def open(self, value):
        try:
            return self.crypt(bytes.fromhex(value.strip()), False).decode('utf-16-le')
        except Exception:
            raise Stop('PROTECTED_INPUT_UNAVAILABLE') from None

class Store:
    def __init__(self, state=STATE, private=PRIVATE, crypto=None):
        self.state, self.private = state, private
        self.crypto = crypto or DPAPI()

    @contextmanager
    def owner_lock(self):
        # Windows byte-range lock spans all Owner processes, without ACL changes.
        need(os.name == 'nt', 'WINDOWS_DPAPI_REQUIRED')
        import msvcrt
        handle = None
        locked = False
        try:
            need(self.state.parent.is_dir() and self.private.is_dir(), 'EXISTING_DIRECTORIES_REQUIRED')
            handle = self.state.with_suffix(self.state.suffix+'.owner.lock').open('a+b')
            if handle.seek(0, 2) == 0:
                handle.write(b'0')
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError:
                raise Stop('OWNER_ENTRY_BUSY') from None
            yield
        finally:
            if handle:
                if locked:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                handle.close()

    def load(self):
        if not self.state.exists():
            return dict(schema=1, instance=str(uuid.uuid4()), channels={c: {} for c in NAMES})
        try:
            data = json.loads(self.state.read_text(encoding='utf-8'))
            need(set(data) == {'schema', 'instance', 'channels'} and data['schema'] == 1
                 and valid_uuid(data['instance']) and set(data['channels']) == set(NAMES),
                 'STATE_INVALID')
            for channel, parts in data['channels'].items():
                need(set(parts) <= {'kb', 'writer'}, 'STATE_INVALID')
                for kind, step in parts.items():
                    need(set(step) <= {'phase', 'id', 'expires_unix', 'submitted_unix'},
                         'STATE_INVALID')
                    need(step.get('phase') in ('pending', 'done'), 'STATE_INVALID')
            return data
        except Exception:
            raise Stop('STATE_INVALID') from None

    def save(self, value):
        # Whitelist state is constructed by the engine, never from API payloads.
        need(self.state.parent.is_dir() and self.private.is_dir(), 'EXISTING_DIRECTORIES_REQUIRED')
        temporary = self.state.with_name(self.state.name+'.'+str(uuid.uuid4())+'.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as file:
                file.write(json.dumps(value, indent=2)+'\n')
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.state)
        except Exception:
            raise Stop('STATE_SAVE_FAILED') from None

    def protected_path(self, channel):
        return self.private / (NAMES[channel]+'-writer.dpapi')

    def has_key(self, channel):
        path = self.protected_path(channel)
        return path.is_file() or path.with_suffix(path.suffix+'.pending').is_file()

    def save_key(self, channel, value):
        cipher = self.crypto.seal(value)
        path = self.protected_path(channel)
        pending = path.with_suffix(path.suffix+'.pending')
        try:
            need(not path.exists(), 'PROTECTED_FILE_ALREADY_EXISTS')
            with pending.open('x', encoding='utf-8') as file:
                file.write(cipher+'\n')
                file.flush()
                os.fsync(file.fileno())
            # Windows rename is atomic and refuses an existing destination.
            need(os.name == 'nt', 'WINDOWS_DPAPI_REQUIRED')
            os.rename(pending, path)
        except Exception:
            raise Stop('PROTECTED_SAVE_FAILED') from None
        finally:
            value = cipher = None

    def read_key(self, channel):
        try:
            path = self.protected_path(channel)
            if not path.exists():
                path = path.with_suffix(path.suffix+'.pending')
            return self.crypto.open(path.read_text(encoding='utf-8'))
        except Exception:
            raise Stop('PROTECTED_INPUT_UNAVAILABLE') from None

    def finish_key(self, channel):
        path = self.protected_path(channel)
        pending = path.with_suffix(path.suffix+'.pending')
        try:
            if not path.exists() and pending.exists():
                need(os.name == 'nt', 'WINDOWS_DPAPI_REQUIRED')
                os.rename(pending, path)
            need(path.is_file(), 'PROTECTED_SAVE_FAILED')
        except Exception:
            raise Stop('PROTECTED_SAVE_FAILED') from None

class Engine:
    def __init__(self, api, metadata, store, ui, now=None):
        self.api, self.metadata, self.store, self.ui = api, metadata, store, ui
        self.now = now or (lambda: int(datetime.now(timezone.utc).timestamp()))
        self.bearer = None
        self.synthetic = not isinstance(api, OfficialHTTP)

    def confirm(self, step, channel=None, expiry=None):
        need(self.ui('confirm', step=step, channel=channel, expiresUnix=expiry) is True,
             'OWNER_CANCELLED')

    def call(self, method, path, body=None, key=None):
        return self.api(method, path, body, bearer=self.bearer if key is None else None, key=key)

    def bind_key(self, channel, kb_id, step, token):
        try:
            need(isinstance(token, str) and len(token) >= 20 and '\r' not in token
                 and '\n' not in token and not token.startswith('mcp_'), 'KEY_INPUT_INVALID')
            rows = self.metadata.rows('writer', channel)
            need(len(rows) == 1, 'WRITER_NAME_AMBIGUOUS')
            key_id = validate_writer(rows[0], channel, kb_id, step, self.now())
            need(self.metadata.matches_key(channel, key_id, token), 'WRITER_TOKEN_BINDING_INVALID')
            # Existing reader-identity is retrieve-only; its permission gate stays intact.
            detail = self.call('GET', '/api/v1/knowledge-bases/'+kb_id, key=token).get('data', {})
            need(detail.get('id') == kb_id and detail.get('tenant_id') == 1,
                 'WRITER_AUTHENTICATION_INVALID')
            rows = self.metadata.rows('writer', channel)
            need(len(rows) == 1, 'WRITER_NAME_AMBIGUOUS')
            need(validate_writer(rows[0], channel, kb_id, dict(step, id=key_id), self.now()) == key_id,
                 'WRITER_SCOPE_CHANGED')
        finally:
            token = None

    def run(self):
        with self.store.owner_lock():
            return self._run_locked()

    def _run_locked(self):
        password = email = login = token = None
        try:
            if not self.synthetic:
                self.api('GET', '/health')  # Anonymous prerequisite before any hidden input.
            self.confirm('login')
            email = self.ui('secret', step='email')
            password = self.ui('secret', step='password')
            need(isinstance(email, str) and email and isinstance(password, str) and password,
                 'LOGIN_INPUT_REQUIRED')
            login = self.api('POST', '/api/v1/auth/login',
                dict(email=email, password=password))
            self.bearer = login.get('token')
            need(isinstance(self.bearer, str) and self.bearer, 'LOGIN_REJECTED')
            email = password = login = None
            identity = self.call('GET', '/api/v1/auth/me').get('data', {})
            need(identity.get('tenant', {}).get('id') == 1 and
                 identity.get('user', {}).get('is_active') is True and
                 any(m.get('tenant_id') == 1 and m.get('role') == 'owner'
                     for m in identity.get('memberships', [])), 'TENANT1_OWNER_REQUIRED')
            state = self.store.load()
            for channel in NAMES:
                parts = state['channels'][channel]
                kb_step = parts.get('kb')
                rows = self.metadata.rows('kb', channel)
                need(len(rows) <= 1, 'KB_NAME_AMBIGUOUS')
                if kb_step is None:
                    need(not rows and not self.store.has_key(channel), 'UNOWNED_OBJECT_EXISTS')
                    self.confirm('create-kb', channel)
                    need(not self.metadata.rows('kb', channel), 'UNOWNED_OBJECT_EXISTS')
                    parts['kb'] = kb_step = dict(phase='pending')
                    self.store.save(state)
                    created = self.call('POST', '/api/v1/knowledge-bases',
                        kb_body(channel, state['instance'])).get('data', {})
                    need(valid_uuid(created.get('id')), 'KB_RESULT_UNKNOWN')
                    kb_step['id'] = created['id']
                    self.store.save(state)
                elif not kb_step.get('id'):
                    need(len(rows) == 1 and rows[0].get('tenant_id') == 1
                         and rows[0].get('description') == kb_body(channel, state['instance'])['description'],
                         'KB_RESULT_UNKNOWN_NO_RETRY')
                    self.confirm('recover-kb', channel)
                    kb_step['id'] = rows[0]['id']
                kb_id = kb_step['id']
                need(valid_uuid(kb_id), 'KB_ID_INVALID')
                detail = self.call('GET', '/api/v1/knowledge-bases/'+kb_id).get('data', {})
                validate_kb(detail, channel, state['instance'])
                self.metadata.assert_empty(channel, kb_id)
                kb_step['phase'] = 'done'
                self.store.save(state)
                other = state['channels']['official' if channel == 'local' else 'local'].get('kb', {})
                need(other.get('id') != kb_id, 'KB_IDS_MUST_DIFFER')
                step = parts.get('writer')
                rows = self.metadata.rows('writer', channel)
                need(len(rows) <= 1, 'WRITER_NAME_AMBIGUOUS')
                if step is None:
                    need(not rows and not self.store.has_key(channel), 'UNOWNED_OBJECT_EXISTS')
                    expires = self.now()+604800
                    self.confirm('create-and-save-writer', channel, expires)
                    need(not self.metadata.rows('writer', channel), 'UNOWNED_OBJECT_EXISTS')
                    self.metadata.assert_empty(channel, kb_id)
                    submitted = self.now()
                    # Exact seven days from final Owner submission, never a historical date.
                    step = parts['writer'] = dict(phase='pending', submitted_unix=submitted,
                        expires_unix=submitted+604800)
                    self.store.save(state)
                    body = dict(name=NAMES[channel]+'-writer', full_access=False,
                        knowledge_base_ids=[kb_id], capabilities=['ingest', 'retrieve'],
                        expires_at_unix=step['expires_unix'])
                    created = self.call('POST', '/api/v1/tenants/1/api-keys', body).get('data', {})
                    # Capture in DPAPI immediately under Owner CREATE+SAVE confirmation.
                    # No API-key list or full payload enters state/output.
                    token = created.get('token')
                    need(isinstance(token, str) and len(token) >= 20, 'WRITER_RESULT_UNKNOWN')
                    self.store.save_key(channel, token)
                    step['id'] = validate_writer(created, channel, kb_id, step, self.now())
                    self.bind_key(channel, kb_id, step, token)
                    token = created = None
                else:
                    need(len(rows) == 1, 'WRITER_RESULT_UNKNOWN_NO_RETRY')
                    key_id = validate_writer(rows[0], channel, kb_id, step, self.now())
                    self.confirm('resume-protected-writer', channel, step['expires_unix'])
                    if self.store.has_key(channel):
                        token = self.store.read_key(channel)
                    else:
                        token = self.ui('secret', step='recover-token', channel=channel)
                        self.bind_key(channel, kb_id, dict(step, id=key_id), token)
                        self.confirm('save-recovered-writer', channel, step['expires_unix'])
                        self.store.save_key(channel, token)
                    step['id'] = key_id
                    self.bind_key(channel, kb_id, step, token)
                    token = None
                self.store.finish_key(channel)
                step['phase'] = 'done'
                self.store.save(state)
            empties = {channel: self.metadata.assert_empty(channel, parts['kb']['id'])
                       for channel, parts in state['channels'].items()}
            result = dict(tenantId=1, protectedSaveReady=True, synthetic=self.synthetic, dbEmptyCounts=empties,
                channels={channel: dict(kbId=parts['kb']['id'], writerId=parts['writer']['id'],
                    writerName=NAMES[channel]+'-writer', credentialEnv=VARS[channel],
                    expiresAtUnix=parts['writer']['expires_unix'])
                    for channel, parts in state['channels'].items()})
            self.ui('receipt', data=result)
            return result
        finally:
            # Python immutable strings cannot promise physical zeroing; no disk/log persistence.
            self.bearer = password = email = login = token = None

class PipeUI:
    """stdout is a whitelist UI protocol, never a response/exception dump."""
    def __call__(self, kind, **data):
        print(json.dumps(dict(kind=kind, **data)), flush=True)
        if kind in ('confirm', 'secret'):
            try:
                answer = json.loads(sys.stdin.readline())
                return answer.get('value')
            except Exception:
                raise Stop('OWNER_INPUT_CANCELLED') from None

def main():
    if sys.argv[1:] == ['--connection-check']:
        try:
            OfficialHTTP()('GET', '/health')
            print(json.dumps(dict(kind='connection-check', ok=True, stage='service-health',
                origin=HOST, health='ok', authenticationUsed=False, runtimeStateRead=False)))
            return 0
        except Stop as error:
            print(json.dumps(dict(public_stop(error, 'connection-check'), ok=False,
                origin=HOST, authenticationUsed=False, runtimeStateRead=False)))
            return 2
        except BaseException:
            print(json.dumps(dict(kind='connection-check', ok=False, code='LOCAL_OPERATION_STOPPED',
                stage='service-health', authenticationUsed=False, runtimeStateRead=False)))
            return 2
    if sys.argv[1:] != ['--owner-pipe']:
        print(json.dumps(dict(ok=True, mode='offline-preview', realApiCalled=False,
            targets=list(NAMES.values()), tenantId=1, capabilities=['ingest', 'retrieve'],
            ttlDays=7, protectedFormat='Windows CurrentUser DPAPI / SecureString hex',
            liveEntry='Start-OwnerCanary.ps1 -OwnerOperate')))
        return 0
    try:
        Engine(OfficialHTTP(), ReadOnlyMetadata(), Store(), PipeUI()).run()
        return 0
    except Stop as error:
        print(json.dumps(public_stop(error)), flush=True)
        return 2
    except BaseException:
        print(json.dumps(dict(kind='blocked', code='LOCAL_OPERATION_STOPPED')), flush=True)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
