import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import threading
import unittest

SPEC = importlib.util.spec_from_file_location('knowledge_sync',
        Path(__file__).resolve().parents[1] / 'knowledge_sync.py')
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='knowledge-sync-test-')
        self.repo = Path(self.directory.name) / 'source'
        self.repo.mkdir()
        self.git('init', '-q', '--initial-branch=main')
        self.git('config', 'user.name', 'Synthetic fixture')
        self.git('config', 'user.email', 'fixture@localhost')
        self.write('docs/a.md', '# Committed title\n\n状态: Accepted\n\nfixture body\n')
        self.commit()
        self.first = self.sha()
        self.git('update-ref', 'refs/remotes/origin/main', self.first)

    def tearDown(self):
        self.directory.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args],
                              capture_output=True, check=True).stdout

    def write(self, path, body):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding='utf-8')

    def commit(self):
        self.git('add', '--', '.')
        self.git('commit', '-q', '-m', 'Synthetic fixture')

    def sha(self):
        return self.git('rev-parse', 'HEAD').decode().strip()

    def scan(self, channel='local', **kwargs):
        if channel == 'official':
            kwargs.setdefault('ref', 'refs/remotes/origin/main')
        return sync.build_manifest(self.repo, 'fixture', channel, ['docs'], **kwargs)

    def test_dirty_worktree_is_not_read(self):
        self.write('docs/a.md', '# Dirty title\n\n状态: Accepted\nchanged body')
        self.write('docs/untracked.md', '# Untracked\n\n状态: Accepted\n')
        manifest = self.scan()
        self.assertEqual(manifest['documents'][0]['title'], 'Committed title')
        self.assertEqual(len(manifest['documents']), 1)
        self.assertFalse(manifest['worktree_files_read'])
        self.assertNotIn('fixture body', str(manifest))

    def test_local_and_formal_are_separate_and_formal_does_not_advance(self):
        self.write('docs/a.md', '# Local commit\n\n状态: Accepted\nnew fixture')
        self.commit()
        local, official = self.scan(), self.scan('official')
        self.assertEqual(local['documents'][0]['title'], 'Local commit')
        self.assertEqual(official['source_commit'], self.first)
        self.assertEqual(official['documents'][0]['title'], 'Committed title')
        self.assertNotEqual(local['documents'][0]['source_key'],
                            official['documents'][0]['source_key'])
        self.assertFalse(official['remote_refresh_performed'])

    def test_exclusions_and_explicit_unmarked_scope(self):
        for path, state in [('draft.md', 'Draft'), ('old.md', 'Deprecated')]:
            self.write('docs/'+path, '# Synthetic\n\n状态: '+state+'\n')
        self.write('docs/unmarked.md', '# Unmarked\nfixture\n')
        self.write('docs/secrets/secret.md', '# Secret fixture\n\n状态: Accepted\n')
        self.write('docs/archive/history.md', '# History\n\n状态: Accepted\n')
        self.write('docs/sessions/raw.md', '# Raw fixture\n\n状态: Accepted\n')
        self.write('docs/.env.test', 'synthetic-placeholder')
        self.commit()
        self.assertEqual(len(self.scan()['documents']), 1)
        allowed = self.scan(allow_unmarked=True)
        self.assertEqual({x['path'] for x in allowed['documents']},
                         {'docs/a.md', 'docs/unmarked.md'})
        self.assertEqual(len(allowed['excluded']), 6)

    def test_symlink_blob_is_never_read_as_markdown(self):
        blob = subprocess.run(['git', '-C', str(self.repo), 'hash-object', '-w', '--stdin'],
                              input=b'../../outside-private-file', capture_output=True,
                              check=True).stdout.decode().strip()
        self.git('update-index', '--add', '--cacheinfo', '120000,'+blob+',docs/link.md')
        self.git('commit', '-q', '-m', 'Synthetic symlink mode')
        manifest = self.scan()
        self.assertEqual(len(manifest['documents']), 1)
        self.assertIn({'path': 'docs/link.md', 'reason': 'unsupported_mode_or_symlink'},
                      manifest['excluded'])

    def test_incremental_rename_modify_delete_and_revert(self):
        self.write('docs/delete.md', '# Remove\n\n状态: Accepted\n')
        self.write('docs/modify.md', '# Modify\n\n状态: Accepted\nold')
        self.commit()
        baseline = self.sha()
        self.git('mv', 'docs/a.md', 'docs/renamed.md')
        self.git('rm', 'docs/delete.md')
        self.write('docs/modify.md', '# Modify\n\n状态: Accepted\nnew')
        self.commit()
        actions = self.scan(baseline=baseline)['changes']
        self.assertEqual({x['action'] for x in actions},
                         {'rename_candidate', 'modify', 'delete_proposal'})
        self.git('revert', '--no-edit', 'HEAD')
        revert = self.scan(baseline=baseline)
        self.assertEqual(revert['changes'], [])
        self.assertNotEqual(revert['source_commit'], baseline)

    def test_parent_gitlink_does_not_follow_child_HEAD(self):
        # A child HEAD is not consulted; only the committed parent tree matters.
        child = self.repo / 'child'
        child.mkdir()
        subprocess.run(['git', '-C', str(child), 'init', '-q'], check=True,
                       capture_output=True)
        for key, value in [('user.name', 'Fixture'), ('user.email', 'fixture@localhost')]:
            subprocess.run(['git', '-C', str(child), 'config', key, value], check=True)
        (child/'README.md').write_text('# Child\n\n状态: Accepted\n', encoding='utf-8')
        subprocess.run(['git', '-C', str(child), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(child), 'commit', '-q', '-m', 'Child fixture'], check=True)
        pin = subprocess.run(['git', '-C', str(child), 'rev-parse', 'HEAD'],
                             check=True, capture_output=True).stdout.decode().strip()
        self.git('update-index', '--add', '--cacheinfo', '160000,'+pin+',child')
        self.git('commit', '-q', '-m', 'Pin child fixture')
        pinned_parent = self.sha()
        self.git('update-ref', 'refs/remotes/origin/main', pinned_parent)
        (child/'README.md').write_text('# New child\n\n状态: Accepted\n', encoding='utf-8')
        subprocess.run(['git', '-C', str(child), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(child), 'commit', '-q', '-m', 'Unpinned child'], check=True)
        manifest = sync.build_manifest(self.repo, 'fixture', 'local', ['docs', 'child'])
        self.assertEqual(manifest['gitlink_pins'],
                         [{'path': 'child', 'pinned_commit': pin, 'child_worktree_read': False}])
        self.assertEqual(len(manifest['documents']), 1)
        official = sync.build_manifest(self.repo, 'fixture', 'official', ['docs', 'child'],
                                       ref='refs/remotes/origin/main')
        self.assertEqual(official['source_commit'], pinned_parent)
        self.assertEqual(official['gitlink_pins'], manifest['gitlink_pins'])

    def test_snapshot_diff_finds_skipped_commits_without_hooks(self):
        self.write('docs/remove.md', '# Remove later\n\n状态: Accepted\n')
        self.commit()
        baseline = self.sha()
        hook_path = str(self.repo/'unused-hooks-directory')
        self.git('config', 'user.name', 'Synthetic second author')
        self.write('docs/a.md', '# Changed by second author\n\n状态: Accepted\n')
        self.git('add', '--', '.')
        self.git('-c', 'core.hooksPath='+hook_path, 'commit', '-q', '-m', 'Skipped event one')
        self.git('config', 'user.name', 'Synthetic third author')
        self.git('rm', 'docs/remove.md')
        self.write('docs/new.md', '# Added by third author\n\n状态: Accepted\n')
        self.git('add', '--', '.')
        self.git('-c', 'core.hooksPath='+hook_path, 'commit', '-q', '-m', 'Skipped event two')
        current = self.sha()
        self.git('update-ref', 'refs/remotes/origin/main', current)
        manifest = self.scan('official', baseline=baseline)
        self.assertEqual(manifest['source_commit'], current)
        self.assertEqual({(x['action'], x['path']) for x in manifest['changes']},
                         {('modify','docs/a.md'),('delete_proposal','docs/remove.md'),
                          ('add','docs/new.md')})
        self.assertFalse(manifest['kb_write_performed'])
        self.assertFalse(manifest['remote_refresh_performed'])
        self.assertFalse((self.repo/'unused-hooks-directory').exists())

    def test_path_ref_and_baseline_fail_closed(self):
        for path in ('../docs', '.', '/docs', 'C:/docs', 'docs\\a.md', 'docs//a.md'):
            with self.assertRaises(ValueError):
                sync.build_manifest(self.repo, 'fixture', 'local', [path])
        with self.assertRaises(ValueError):
            sync.build_manifest(self.repo, 'fixture', 'official', ['docs'], ref='HEAD')
        with self.assertRaises(ValueError):
            self.scan('official', ref='refs/remotes/origin/not-cached')
        with self.assertRaises(ValueError):
            self.scan(baseline='HEAD')

    def test_repeatable_digest_and_no_transport(self):
        self.git('config', 'remote.origin.url', 'https://example.invalid/fixture.git')
        self.git('config', 'remote.origin.promisor', 'true')
        a, b = self.scan('official'), self.scan('official')
        self.assertEqual(a, b)
        self.assertFalse(a['kb_write_performed'])
        self.assertFalse(a['model_calls_performed'])
        self.assertNotIn('example.invalid', str(a))

    def test_commit_replace_cannot_change_formal_snapshot(self):
        self.write('docs/a.md', '# Substitute title\n\n状态: Accepted\nreplacement')
        self.commit()
        self.git('replace', self.first, self.sha())
        manifest = self.scan('official')
        self.assertEqual(manifest['source_commit'], self.first)
        self.assertEqual(manifest['documents'][0]['title'], 'Committed title')

    def test_blob_replace_cannot_change_document_identity(self):
        original = self.git('rev-parse', 'HEAD:docs/a.md').decode().strip()
        replacement = subprocess.run(
            ['git', '-C', str(self.repo), 'hash-object', '-w', '--stdin'],
            input='# Substitute title\n\n状态: Accepted\nreplacement'.encode('utf-8'),
            capture_output=True, check=True).stdout.decode().strip()
        self.git('replace', original, replacement)
        manifest = self.scan()
        self.assertEqual(manifest['documents'][0]['source_blob_sha'], original)
        self.assertEqual(manifest['documents'][0]['title'], 'Committed title')

    def test_missing_promisor_object_fails_without_transport(self):
        requests = []

        class SyntheticRemote(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(500)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticRemote)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            self.git('config', 'remote.origin.url',
                     f'http://127.0.0.1:{server.server_port}/synthetic.git')
            self.git('config', 'remote.origin.promisor', 'true')
            self.git('config', 'core.repositoryformatversion', '1')
            self.git('config', 'extensions.partialclone', 'origin')
            blob = self.git('rev-parse', 'HEAD:docs/a.md').decode().strip()
            object_file = (self.repo/'.git/objects'/blob[:2]/blob[2:]).resolve()
            if not object_file.is_relative_to(self.repo.resolve()):
                raise RuntimeError('synthetic_object_path_outside_fixture')
            # Git marks loose objects read-only on Windows; only this fixture is altered.
            object_file.chmod(stat.S_IREAD | stat.S_IWRITE)
            object_file.unlink()
            with self.assertRaisesRegex(ValueError, 'git_read_failed_offline'):
                self.scan('official')
            self.assertEqual(requests, [])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_documented_cli_emits_utf8_metadata_without_body(self):
        self.write('docs/unicode.md', '# 合成🚀标题\n\n状态: Accepted\nbody-marker-not-emitted')
        self.commit()
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-B', SPEC.origin, '--repo', str(self.repo),
             '--repo-id', 'fixture', '--channel', 'local', '--include', 'docs'],
            capture_output=True, check=True)
        manifest = json.loads(result.stdout.decode('utf-8'))
        document = next(d for d in manifest['documents'] if d['path']=='docs/unicode.md')
        self.assertEqual(document['title'], '合成🚀标题')
        self.assertFalse(manifest['kb_write_performed'])
        self.assertNotIn(b'body-marker-not-emitted', result.stdout)


if __name__ == '__main__':
    unittest.main()
