"""No-socket official-contract service tests; all credentials are synthetic."""
import contextlib
import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

MODULE = Path(__file__).with_name('owner_canary.py')
if not MODULE.is_file():
    MODULE = Path(__file__).resolve().parent.parent/'owner_canary.py'
spec = importlib.util.spec_from_file_location('owner_canary', MODULE)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
SENTINEL = 'SYNTHETIC-TOKEN-DO-NOT-PRINT-123456789'
NOW = 1791691200

def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()

class Service:
    """In-memory official endpoint/metadata simulator, never opens a socket."""
    def __init__(self):
        self.kbs, self.keys, self.calls = {}, {}, []
        self.next_id = 10
        self.drop = None
        self.wrong_role = False
        self.bad_key_scope = False
        self.token_to_id = {}

    def rows(self, kind, channel):
        objects = self.kbs if kind == 'kb' else self.keys
        name = app.NAMES[channel]+('-writer' if kind == 'writer' else '')
        return [copy.deepcopy(row) for row in objects.values() if row['name'] == name]

    def matches_key(self, channel, key_id, token):
        return self.token_to_id.get(token) == key_id

    def assert_empty(self, channel, kb_id):
        return dict(knowledges=0, chunks=0, lite_embeddings=0, fts=0,
            pending_ops=0, processing_spans=0, native_operations=0)

    def __call__(self, method, path, body=None, bearer=None, key=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path == '/api/v1/auth/login':
            return dict(success=True, token=SENTINEL, refresh_token=SENTINEL)
        if path == '/api/v1/auth/me':
            return dict(success=True, data=dict(tenant=dict(id=1), user=dict(is_active=True),
                memberships=[dict(tenant_id=1, role='viewer' if self.wrong_role else 'owner')]))
        if method == 'POST' and path == '/api/v1/knowledge-bases':
            row = copy.deepcopy(body)
            row.update(id=str(uuid.uuid4()), tenant_id=1, knowledge_count=0, chunk_count=0,
                processing_count=0, is_processing=False)
            self.kbs[row['id']] = row
            if self.drop == 'kb':
                self.drop = None
                raise app.Stop('HTTP_RESULT_UNKNOWN')
            return dict(success=True, data=row)
        if method == 'GET' and path.startswith('/api/v1/knowledge-bases/'):
            return dict(success=True, data=copy.deepcopy(self.kbs[path.rsplit('/', 1)[-1]]))
        if method == 'POST' and path == '/api/v1/tenants/1/api-keys':
            row = dict(id=self.next_id, tenant_id=1, scope_type='tenant', name=body['name'],
                full_access=self.bad_key_scope, knowledge_base_ids=body['knowledge_base_ids'],
                capabilities=body['capabilities'], revoked_at=None, created_at=stamp(NOW),
                expires_at=stamp(body['expires_at_unix']))
            self.next_id += 1
            self.keys[row['id']] = row
            token = SENTINEL+str(row['id'])
            self.token_to_id[token] = row['id']
            if self.drop == 'writer':
                self.drop = None
                raise app.Stop('HTTP_RESULT_UNKNOWN')
            return dict(success=True, data=dict(row, token=token, api_key=token))
        raise AssertionError('Unexpected synthetic route')

class UI:
    def __init__(self, cancel=None, recovery=None):
        self.events, self.cancel, self.recovery = [], cancel, recovery

    def __call__(self, kind, **data):
        self.events.append(dict(kind=kind, **data))
        if kind == 'confirm':
            return data['step'] != self.cancel
        if kind == 'secret':
            return self.recovery if data['step'] == 'recover-token' else 'synthetic-hidden-input'

class Tests(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(__file__).resolve().parent
        self.temporary = tempfile.TemporaryDirectory(prefix='owner-canary-', dir=self.fixture_root)
        self.root = Path(self.temporary.name)
        self.private = self.root / 'private'
        self.private.mkdir()
        self.store = app.Store(self.root/'state.json', self.private)
        self.service = Service()
        self.ui = UI()

    def tearDown(self):
        self.assertEqual(self.root.resolve().parent, self.fixture_root.resolve())
        self.assertTrue(self.root.name.startswith('owner-canary-'))
        self.temporary.cleanup()

    def engine(self, ui=None, store=None):
        return app.Engine(self.service, self.service, store or self.store, ui or self.ui, lambda: NOW)

    def posts(self, endpoint):
        return len([c for c in self.service.calls if c[:2] == ('POST', endpoint)])

    def test_full_fixed_scope_and_currentuser_sealed_files(self):
        result = self.engine().run()
        self.assertTrue(result['synthetic'])
        self.assertEqual(self.posts('/api/v1/knowledge-bases'), 2)
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 2)
        self.assertNotEqual(result['channels']['local']['kbId'], result['channels']['official']['kbId'])
        self.assertNotIn(SENTINEL, json.dumps(self.ui.events))
        self.assertNotIn(SENTINEL, self.store.state.read_text())
        for channel in app.NAMES:
            protected = self.store.protected_path(channel).read_text()
            self.assertNotIn(SENTINEL, protected)
            self.assertTrue(protected.startswith('01000000'))
            self.assertIn(SENTINEL, self.store.read_key(channel))
        for method, path, body in self.service.calls:
            self.assertNotIn(method, ('PUT', 'PATCH', 'DELETE'))
            if path == '/api/v1/tenants/1/api-keys':
                self.assertEqual(body['capabilities'], ['ingest', 'retrieve'])
                self.assertFalse(body['full_access'])
                self.assertEqual(len(body['knowledge_base_ids']), 1)
                self.assertEqual(body['expires_at_unix'], NOW+604800)

    def test_cancel_before_login_does_not_contact_anything(self):
        with self.assertRaisesRegex(app.Stop, 'OWNER_CANCELLED'):
            self.engine(UI(cancel='login')).run()
        self.assertEqual(self.service.calls, [])
        self.assertFalse(self.store.state.exists())

    def test_cancel_before_writer_then_resume_keeps_same_kb(self):
        with self.assertRaisesRegex(app.Stop, 'OWNER_CANCELLED'):
            self.engine(UI(cancel='create-and-save-writer')).run()
        self.assertEqual(self.posts('/api/v1/knowledge-bases'), 1)
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 0)
        self.engine().run()
        self.assertEqual(self.posts('/api/v1/knowledge-bases'), 2)
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 2)

    def test_dropped_kb_response_recovers_by_instance_marker_without_duplicate(self):
        self.service.drop = 'kb'
        with self.assertRaisesRegex(app.Stop, 'HTTP_RESULT_UNKNOWN'):
            self.engine().run()
        self.engine().run()
        self.assertEqual(self.posts('/api/v1/knowledge-bases'), 2)

    def test_dropped_writer_response_blocks_and_never_recreates(self):
        self.service.drop = 'writer'
        with self.assertRaisesRegex(app.Stop, 'HTTP_RESULT_UNKNOWN'):
            self.engine().run()
        with self.assertRaisesRegex(app.Stop, 'KEY_INPUT_INVALID'):
            self.engine(UI(recovery='')).run()
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 1)
        token = next(iter(self.service.token_to_id))
        self.engine(UI(recovery=token)).run()
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 2)
        self.assertNotIn(SENTINEL, self.store.state.read_text())

    def test_crash_after_protected_save_recovers_same_key_without_duplicate(self):
        original = self.store.save_key
        def save_then_interrupt(channel, token):
            original(channel, token)
            raise app.Stop('SIMULATED_CRASH')
        with patch.object(self.store, 'save_key', save_then_interrupt):
            with self.assertRaisesRegex(app.Stop, 'SIMULATED_CRASH'):
                self.engine().run()
        self.engine().run()
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 2)

    def test_completed_resume_does_not_recreate_or_refresh_expiry(self):
        initial = self.engine().run()
        resumed = self.engine().run()
        self.assertEqual(initial, resumed)
        self.assertEqual(self.posts('/api/v1/knowledge-bases'), 2)
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 2)

    def test_cancel_after_first_channel_is_durable_then_resume_without_duplicate(self):
        class CancelOfficial(UI):
            def __call__(self, kind, **data):
                if kind == 'confirm' and data.get('step') == 'create-kb' and data.get('channel') == 'official':
                    return False
                return super().__call__(kind, **data)
        with self.assertRaisesRegex(app.Stop, 'OWNER_CANCELLED'):
            self.engine(CancelOfficial()).run()
        self.assertEqual(self.store.load()['channels']['local']['writer']['phase'], 'done')
        self.engine().run()
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 2)

    def test_protected_pending_file_recovers_after_atomic_rename_interruption(self):
        self.engine().run()
        path = self.store.protected_path('local')
        path.rename(path.with_suffix(path.suffix+'.pending'))
        self.engine().run()
        self.assertTrue(path.exists())
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 2)

    def test_wrong_hidden_recovery_token_never_saved_or_recreated(self):
        self.service.drop = 'writer'
        with self.assertRaises(app.Stop):
            self.engine().run()
        with self.assertRaisesRegex(app.Stop, 'WRITER_TOKEN_BINDING_INVALID'):
            self.engine(UI(recovery=SENTINEL+'wrong')).run()
        self.assertFalse(self.store.has_key('local'))
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 1)

    def test_protected_save_failure_and_expired_resume_never_reissue(self):
        with patch.object(self.store, 'save_key', side_effect=app.Stop('PROTECTED_SAVE_FAILED')):
            with self.assertRaisesRegex(app.Stop, 'PROTECTED_SAVE_FAILED'):
                self.engine().run()
        token = next(iter(self.service.token_to_id))
        self.engine(UI(recovery=token)).run()
        with patch.object(self.store, 'read_key', side_effect=AssertionError('Must reject before unprotect')):
            expired = app.Engine(self.service, self.service, self.store, self.ui, lambda: NOW+604801)
            with self.assertRaisesRegex(app.Stop, 'WRITER_EXPIRY_INVALID'):
                expired.run()
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 2)

    def test_owner_entry_refuses_redirected_live_mode_before_starting_core(self):
        entry = MODULE.with_name('Start-OwnerCanary.ps1')
        run = subprocess.run(['pwsh','-NoProfile','-File',str(entry),'-OwnerOperate'],
            capture_output=True,timeout=15)
        self.assertEqual(run.returncode, 2)
        self.assertIn(b'interactive console', run.stdout)
        self.assertNotIn(SENTINEL.encode(), run.stdout+run.stderr)

    def test_owner_entry_launcher_check_starts_one_python_with_no_owner_core(self):
        entry = MODULE.with_name('Start-OwnerCanary.ps1')
        run = subprocess.run(['pwsh','-NoProfile','-File',str(entry),'-LauncherCheck'],
            capture_output=True,timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout)
        receipt = json.loads(run.stdout)
        self.assertEqual(receipt['mode'], 'synthetic-launch-check')
        self.assertTrue(receipt['ok'])
        self.assertFalse(receipt['realAuthenticationExecuted'])
        self.assertFalse(receipt['realApiWritesExecuted'])
        self.assertFalse(receipt['realStateRead'])
        self.assertEqual(run.stderr, b'')
        self.assertNotIn(SENTINEL.encode(), run.stdout)

    def test_owner_entry_launcher_check_conflict_stops_before_any_owner_operation(self):
        entry = MODULE.with_name('Start-OwnerCanary.ps1')
        run = subprocess.run(['pwsh','-NoProfile','-File',str(entry),'-LauncherCheck','-OwnerOperate'],
            capture_output=True,timeout=15)
        self.assertEqual(run.returncode, 2)
        self.assertIn(b'code=MODE_CONFLICT; stage=mode-selection', run.stdout)
        self.assertNotIn(b'Authenticate', run.stdout)
        self.assertNotIn(SENTINEL.encode(), run.stdout+run.stderr)

    def test_owner_entry_missing_python_reports_safe_stage_before_child_start(self):
        entry = MODULE.with_name('Start-OwnerCanary.ps1')
        shell = shutil.which('pwsh')
        self.assertIsNotNone(shell)
        environment = {name: os.environ[name] for name in ('SystemRoot','WINDIR','TEMP','TMP') if name in os.environ}
        environment['PATH'] = str(self.private)
        run = subprocess.run([shell,'-NoProfile','-File',str(entry),'-LauncherCheck'],
            env=environment,capture_output=True,timeout=15)
        self.assertEqual(run.returncode, 2)
        self.assertIn(b'code=PYTHON_NOT_FOUND; stage=resolve-python; ownerStep=none', run.stdout)
        self.assertNotIn(b'CommandNotFoundException', run.stdout+run.stderr)
        self.assertEqual(run.stderr, b'')
        self.assertNotIn(SENTINEL.encode(), run.stdout+run.stderr)

    def test_second_process_rejected_by_lock_before_authentication(self):
        code = ('import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                'from owner_canary import Store;'
                's=Store(Path(sys.argv[2]),Path(sys.argv[3]));'
                'ctx=s.owner_lock();ctx.__enter__();print("HELD",flush=True);'
                'sys.stdin.readline();ctx.__exit__(None,None,None)')
        process = subprocess.Popen([sys.executable,'-B','-c',code,str(MODULE.parent),
            str(self.store.state),str(self.private)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'HELD')
            with self.assertRaisesRegex(app.Stop, 'OWNER_ENTRY_BUSY'):
                self.engine().run()
            self.assertEqual(self.service.calls, [])
        finally:
            process.communicate('release\n', timeout=5)

    def test_owner_role_and_nonempty_kb_block_before_writer(self):
        self.service.wrong_role = True
        with self.assertRaisesRegex(app.Stop, 'TENANT1_OWNER_REQUIRED'):
            self.engine().run()
        self.assertEqual(self.posts('/api/v1/knowledge-bases'), 0)
        self.service.wrong_role = False
        with self.assertRaises(app.Stop):
            self.engine(UI(cancel='create-and-save-writer')).run()
        next(iter(self.service.kbs.values()))['knowledge_count'] = 1
        with self.assertRaisesRegex(app.Stop, 'KB_NOT_EMPTY'):
            self.engine().run()
        self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 0)

    def test_invalid_scope_keeps_protected_capture_but_never_marks_ready(self):
        self.service.bad_key_scope = True
        with self.assertRaisesRegex(app.Stop, 'WRITER_SCOPE_INVALID'):
            self.engine().run()
        self.assertTrue(self.store.has_key('local'))
        self.assertEqual(self.store.load()['channels']['local']['writer']['phase'], 'pending')
        self.assertFalse(any(e['kind'] == 'receipt' for e in self.ui.events))

    def test_no_api_key_collection_route_and_no_raw_exception_output(self):
        with self.assertRaisesRegex(app.Stop, 'ROUTE_NOT_ALLOWED'):
            app.OfficialHTTP()('GET', '/api/v1/tenants/1/api-keys')
        output = io.StringIO()
        with patch.object(sys, 'argv', ['owner_canary.py', '--owner-pipe']), \
             patch.object(app.Engine, 'run', side_effect=RuntimeError(SENTINEL)), \
             contextlib.redirect_stdout(output):
            self.assertEqual(app.main(), 2)
        self.assertNotIn(SENTINEL, output.getvalue())
        self.assertIn('LOCAL_OPERATION_STOPPED', output.getvalue())

    def test_sql_projection_never_reads_old_secret_columns(self):
        path = self.root/'synthetic.db'
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE tenant_api_keys(id INTEGER,name TEXT,tenant_id INTEGER,scope_type TEXT,full_access INTEGER,knowledge_base_ids BLOB,capabilities BLOB,expires_at TEXT,revoked_at TEXT,created_at TEXT,api_key TEXT,key_hash TEXT)')
            db.execute('INSERT INTO tenant_api_keys VALUES(1,?,1,?,0,?,?,?,NULL,?,?,?)',
                ('old-key', 'tenant', '[]', '[]', stamp(NOW+100), stamp(NOW), SENTINEL, SENTINEL))
            db.execute('INSERT INTO tenant_api_keys VALUES(2,?,1,?,0,?,?,?,NULL,?,?,?)',
                (app.NAMES['local']+'-writer','tenant',json.dumps(['mock']),json.dumps(['ingest','retrieve']),
                 stamp(NOW+604800), stamp(NOW), SENTINEL, SENTINEL))
            db.commit()
        rows = app.ReadOnlyMetadata(path).rows('writer', 'local')
        self.assertEqual(len(rows), 1)
        self.assertNotIn('api_key', rows[0])
        self.assertNotIn('key_hash', rows[0])
        self.assertNotIn(SENTINEL, json.dumps(rows))
        token = SENTINEL+'new'
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute('UPDATE tenant_api_keys SET key_hash=? WHERE id=2',
                (app.hashlib.sha256(token.encode()).hexdigest(),))
            db.commit()
        meta = app.ReadOnlyMetadata(path)
        self.assertTrue(meta.matches_key('local', 2, token))
        self.assertFalse(meta.matches_key('local', 1, SENTINEL))

    def test_default_get_zero_but_db_nonempty_or_query_failure_blocks_writer(self):
        for failure in ('KB_OR_INDEX_NOT_EMPTY', 'EMPTY_KB_METADATA_UNAVAILABLE'):
            with self.subTest(failure=failure):
                with patch.object(self.service, 'assert_empty', side_effect=app.Stop(failure)):
                    with self.assertRaisesRegex(app.Stop, failure):
                        self.engine().run()
                self.assertEqual(self.posts('/api/v1/tenants/1/api-keys'), 0)

    def test_sql_counts_include_orphan_fts_soft_deleted_and_durable_tasks(self):
        path = self.root/'empty-counts.db'
        kb_id = str(uuid.uuid4())
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.executescript('CREATE TABLE knowledge_bases(id TEXT,tenant_id INTEGER,name TEXT,deleted_at TEXT);'
                'CREATE TABLE knowledges(id TEXT,tenant_id INTEGER,knowledge_base_id TEXT,deleted_at TEXT);'
                'CREATE TABLE chunks(knowledge_base_id TEXT);'
                'CREATE TABLE lite_embeddings(knowledge_base_id TEXT);'
                'CREATE VIRTUAL TABLE lite_embeddings_fts USING fts5(knowledge_base_id);'
                'CREATE TABLE task_pending_ops(tenant_id INTEGER,scope TEXT,scope_id TEXT);'
                'CREATE TABLE knowledge_processing_spans(knowledge_id TEXT,status TEXT);')
            db.execute('INSERT INTO knowledge_bases VALUES(?,1,?,NULL)', (kb_id,app.NAMES['local']))
            db.commit()
        metadata = app.ReadOnlyMetadata(path)
        self.assertTrue(all(value == 0 for value in metadata.assert_empty('local', kb_id).values()))
        mutations = [
            ('INSERT INTO lite_embeddings_fts VALUES(?)', (kb_id,)),
            ('INSERT INTO knowledges VALUES(?,1,?,?)', ('soft-deleted',kb_id,stamp(NOW))),
            ('INSERT INTO chunks VALUES(?)', (kb_id,)),
            ('INSERT INTO lite_embeddings VALUES(?)', (kb_id,)),
            ("INSERT INTO task_pending_ops VALUES(1,'knowledge_base',?)", (kb_id,))]
        tables = ['lite_embeddings_fts','knowledges','chunks','lite_embeddings','task_pending_ops']
        for table, (sql, params) in zip(tables, mutations):
            with self.subTest(table=table):
                with contextlib.closing(sqlite3.connect(path)) as db:
                    db.execute(sql, params)
                    db.commit()
                with self.assertRaisesRegex(app.Stop, 'KB_OR_INDEX_NOT_EMPTY'):
                    metadata.assert_empty('local', kb_id)
                with contextlib.closing(sqlite3.connect(path)) as db:
                    db.execute('DELETE FROM '+table)
                    db.commit()
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute('DROP TABLE chunks')
            db.commit()
        with self.assertRaisesRegex(app.Stop, 'EMPTY_KB_METADATA_UNAVAILABLE'):
            metadata.assert_empty('local', kb_id)

    def test_dpapi_cross_compatible_with_existing_powershell_securestring_format(self):
        crypto = app.DPAPI()
        cipher = crypto.seal(SENTINEL)
        command = '$s=ConvertTo-SecureString ([Console]::In.ReadLine());$p=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s);try{$v=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($p);if($v -ne "SYNTHETIC-TOKEN-DO-NOT-PRINT-123456789"){exit 3};$c=ConvertFrom-SecureString $s;[Console]::Out.Write($c)}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p);$s.Dispose();$v=$null}'
        run = subprocess.run(['pwsh','-NoProfile','-Command',command],input=(cipher+'\n').encode(),
            capture_output=True,timeout=15)
        self.assertEqual(run.returncode, 0)
        self.assertNotIn(SENTINEL.encode(), run.stdout+run.stderr)
        self.assertEqual(crypto.open(run.stdout.decode()), SENTINEL)

    def test_http_diagnostic_distinguishes_login_identity_and_creation_without_error_body(self):
        for method, path, status, stage, code in [
            ('POST','/api/v1/auth/login',401,'auth-login','AUTH_REJECTED'),
            ('POST','/api/v1/auth/login',500,'auth-login','HTTP_RESULT_UNKNOWN'),
            ('GET','/api/v1/auth/me',403,'auth-identity','HTTP_RESULT_UNKNOWN'),
            ('POST','/api/v1/knowledge-bases',401,'kb-create','HTTP_RESULT_UNKNOWN'),
            ('POST','/api/v1/knowledge-bases',500,'kb-create','HTTP_RESULT_UNKNOWN'),
            ('POST','/api/v1/tenants/1/api-keys',401,'writer-create','HTTP_RESULT_UNKNOWN'),
            ('POST','/api/v1/tenants/1/api-keys',503,'writer-create','HTTP_RESULT_UNKNOWN')]:
            with self.subTest(stage=stage):
                client = app.OfficialHTTP()
                response_body = io.BytesIO(SENTINEL.encode())
                failure = app.urllib.error.HTTPError(app.HOST+path, status, SENTINEL,
                    {'Authorization':SENTINEL},response_body)
                with patch.object(client.opener,'open',side_effect=failure):
                    with self.assertRaises(app.Stop) as stopped:
                        client(method,path,{} if method == 'POST' else None)
                public = app.public_stop(stopped.exception)
                self.assertEqual(public['code'], code)
                self.assertEqual(public['stage'], stage)
                self.assertEqual(public['httpStatus'], status)
                self.assertEqual(public['transport'], 'HTTP_STATUS')
                self.assertEqual(response_body.tell(), 0)
                self.assertNotIn(SENTINEL,json.dumps(public))

    def test_http_diagnostic_classifies_transport_without_exception_text(self):
        for failure, transport in [
            (app.urllib.error.URLError(OSError(10013,SENTINEL)), 'ACCESS_DENIED'),
            (app.urllib.error.URLError(OSError(10061,SENTINEL)), 'CONNECTION_REFUSED'),
            (TimeoutError(SENTINEL), 'TIMEOUT'),
            (json.JSONDecodeError(SENTINEL,SENTINEL,0), 'INVALID_JSON'),
            (RuntimeError(SENTINEL), 'UNAVAILABLE')]:
            with self.subTest(transport=transport):
                public = app.public_stop(app.http_failure(failure,'auth-login'))
                self.assertEqual(public['transport'],transport)
                self.assertEqual(public['stage'],'auth-login')
                self.assertEqual(public['code'],'HTTP_RESULT_UNKNOWN')
                self.assertNotIn(SENTINEL,json.dumps(public))

    def test_rejected_login_wire_contract_preserves_pending_state_and_stops_before_identity(self):
        client = app.OfficialHTTP()
        requests = []
        email, password = 'owner+fixture@example.invalid', ' fixture-p\u00e4ss\u6f22\u5b57 '
        pending = b'{"fixture":"existing UNKNOWN recovery state"}\n'
        self.store.state.write_bytes(pending)
        response_body = io.BytesIO(SENTINEL.encode())
        class HealthResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,size): return b'{"status":"ok"}'
        def fake_open(request, timeout):
            requests.append(request)
            if request.full_url == app.HOST+'/health':
                return HealthResponse()
            raise app.urllib.error.HTTPError(request.full_url,401,SENTINEL,
                {'Authorization':SENTINEL},response_body)
        def fake_ui(kind, **data):
            if kind == 'confirm': return True
            return email if data['step'] == 'email' else password
        with patch.object(client.opener,'open',side_effect=fake_open), \
             patch.object(self.store,'load',side_effect=AssertionError('Must stop before state load')), \
             patch.object(self.store,'save',side_effect=AssertionError('Must not rewrite recovery state')):
            engine = app.Engine(client,self.service,self.store,fake_ui,lambda:NOW)
            with self.assertRaises(app.Stop) as stopped:
                engine.run()
        public = app.public_stop(stopped.exception)
        self.assertEqual(public,dict(kind='blocked',code='AUTH_REJECTED',
            stage='auth-login',transport='HTTP_STATUS',httpStatus=401))
        self.assertEqual(len(requests),2)
        login = requests[1]
        self.assertEqual((login.method,login.full_url),('POST',app.HOST+'/api/v1/auth/login'))
        self.assertEqual(login.get_header('Content-type'),'application/json')
        self.assertEqual(json.loads(login.data.decode('utf-8')),dict(email=email,password=password))
        self.assertNotIn('Authorization',login.headers)
        self.assertNotIn('X-api-key',login.headers)
        self.assertEqual(self.service.calls,[])
        self.assertEqual(self.store.state.read_bytes(),pending)
        self.assertEqual(list(self.private.iterdir()),[])
        self.assertIsNone(engine.bearer)
        self.assertEqual(response_body.tell(),0)
        self.assertNotIn(SENTINEL,json.dumps(public))

    def test_http_health_failure_precedes_owner_input_and_any_submit(self):
        client = app.OfficialHTTP()
        with patch.object(client.opener,'open',side_effect=app.urllib.error.URLError(OSError(10061,SENTINEL))) as request:
            with self.assertRaises(app.Stop) as stopped:
                app.Engine(client,self.service,self.store,self.ui,lambda:NOW).run()
        self.assertEqual(stopped.exception.stage,'service-health')
        self.assertEqual(self.ui.events,[])
        self.assertFalse(self.store.state.exists())
        self.assertEqual(self.service.calls,[])
        self.assertEqual(request.call_count,1)
        wire = request.call_args.args[0]
        self.assertEqual(wire.method,'GET')
        self.assertEqual(wire.full_url,app.HOST+'/health')
        self.assertIsNone(wire.data)
        self.assertNotIn('Authorization',wire.headers)
        self.assertNotIn('X-api-key',wire.headers)
        self.assertEqual(request.call_args.kwargs['timeout'],3)

    def test_http_connection_check_is_anonymous_without_store_or_raw_response(self):
        client = app.OfficialHTTP()
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,size): return json.dumps({'status':'ok','unexpected':SENTINEL}).encode()
        for failure in (None,app.urllib.error.HTTPError(app.HOST+'/health',503,SENTINEL,{},io.BytesIO(SENTINEL.encode()))):
            output = io.StringIO()
            with patch.object(sys,'argv',['owner_canary.py','--connection-check']), \
                 patch.object(app,'OfficialHTTP',return_value=client), \
                 patch.object(app,'Store',side_effect=AssertionError('No real state')), \
                 patch.object(client.opener,'open',side_effect=failure,return_value=Response()), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(app.main(),2 if failure else 0)
            public = json.loads(output.getvalue())
            self.assertEqual(public['stage'],'service-health')
            self.assertFalse(public['authenticationUsed'])
            self.assertFalse(public['runtimeStateRead'])
            self.assertNotIn(SENTINEL,output.getvalue())
            if failure:
                self.assertEqual(public['httpStatus'],503)
            else:
                self.assertEqual(public['health'],'ok')

    def test_http_wire_contract_redacts_error_body_and_disables_redirect_proxy(self):
        client = app.OfficialHTTP()
        class Response:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self, size): return json.dumps(dict(success=True,data=dict(token=SENTINEL))).encode()
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                self.timeout = timeout
                return Response()
        opener = Opener()
        client.opener = opener
        value = client('POST', '/api/v1/tenants/1/api-keys', {'name':'synthetic'}, bearer=SENTINEL)
        self.assertEqual(value['data']['token'], SENTINEL)
        self.assertEqual(requests[0].full_url, app.HOST+'/api/v1/tenants/1/api-keys')
        self.assertEqual(opener.timeout, 15)
        with patch.object(opener, 'open', side_effect=RuntimeError(SENTINEL)):
            with self.assertRaisesRegex(app.Stop, '^HTTP_RESULT_UNKNOWN$'):
                client('POST', '/api/v1/tenants/1/api-keys', {}, bearer=SENTINEL)

if __name__ == '__main__':
    unittest.main(verbosity=2)
