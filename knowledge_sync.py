"""Read committed Markdown metadata; never fetch, import or run a writer."""
import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

BLOCKED = {'.git', '.env', '.aws', '.codex', '.agents', '.dsh', 'sessions',
           'logs', 'archive', 'node_modules', 'credentials', 'secrets',
           'generated', 'temp', '__pycache__'}
MAX_DOCUMENT_BYTES = 262144
MAX_PIN_DEPTH = 16


@dataclass(frozen=True)
class PinnedRepository:
    path: str
    repo: Path
    repo_id: str


def pin_policy(repositories):
    result = {}
    if len(repositories) > 64:
        raise ValueError('too_many_pinned_repositories')
    for item in repositories:
        path = safe_path(item.path)
        if path in result or excluded_path(path+'/scope.md'):
            raise ValueError('invalid_pinned_repository_scope')
        if not isinstance(item.repo_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', item.repo_id):
            raise ValueError('invalid_repo_id')
        if not Path(item.repo).is_absolute():
            raise ValueError('absolute_pinned_cache_required')
        result[path] = item
    return result


def pinned_cache(item, commit):
    raw = Path(item.repo)
    if raw.is_symlink():
        raise ValueError('pinned_cache_symlink_rejected')
    repo = raw.resolve(strict=True)
    top = Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if top != repo or resolve_commit(repo, commit) != commit:
        raise ValueError('pinned_cache_or_commit_unavailable')
    return repo


def scoped(path, includes, *, ancestors=False):
    return any(path == p or path.startswith(p+'/') or
               ancestors and p.startswith(path+'/') for p in includes)


def document_location(repo, commit, path, repo_id, repositories=()):
    """Prove existence/absence through immutable trees. Missing pins are never absence."""
    policy = pin_policy(repositories)
    path = safe_path(path)
    prefix, remaining, chain = '', path, []
    while True:
        parts = remaining.split('/')
        entered = False
        for count in range(1, len(parts)+1):
            relative = '/'.join(parts[:count])
            raw = git(repo, 'ls-tree', '-z', commit, '--', ':(literal)'+relative)
            entries = [e for e in raw.split(b'\0') if e]
            if not entries:
                return None
            if len(entries) != 1:
                raise ValueError('ambiguous_git_path')
            header, name = entries[0].split(b'\t', 1)
            mode, kind, identity = header.decode('ascii').split()
            if name.decode('utf-8') != relative:
                raise ValueError('unexpected_git_path')
            global_path = prefix+relative
            if mode == '160000' and kind == 'commit':
                if count == len(parts):
                    return dict(mode=mode, kind=kind, blob=identity)
                if global_path not in policy or len(chain) >= MAX_PIN_DEPTH:
                    raise ValueError('pinned_source_not_registered_or_too_deep')
                item = policy[global_path]
                repo = pinned_cache(item, identity)
                chain.append(dict(path=global_path, parent_commit=commit,
                                  pinned_commit=identity, source_repo_id=item.repo_id))
                commit, repo_id = identity, item.repo_id
                prefix, remaining = global_path+'/', '/'.join(parts[count:])
                entered = True
                break
            if count == len(parts):
                return dict(repo=repo, mode=mode, kind=kind, blob=identity,
                            source_commit=commit, source_repo_id=repo_id,
                            source_path=relative, pin_chain=chain)
            if mode != '040000' or kind != 'tree':
                return None
        if not entered:
            raise ValueError('unresolved_git_path')


def provenance_valid(doc, manifest):
    """Validate the parent-to-leaf identity carried into the durable outbox."""
    root_commit = manifest['source_commit']
    if doc.get('parent_commit', root_commit) != root_commit:
        return False
    chain = doc.get('pin_chain', [])
    if not isinstance(chain, list) or len(chain) > MAX_PIN_DEPTH:
        return False
    commit, prefix, leaf_repo = root_commit, '', manifest['repo_id']
    pins = {p['path']: p['pinned_commit'] for p in manifest.get('gitlink_pins', [])}
    reads = {p['path']: p for p in manifest.get('gitlink_cache_reads', [])}
    for pin in chain:
        mount = pin.get('path', '')
        if (not mount or prefix and not mount.startswith(prefix+'/') or
            not doc['path'].startswith(mount+'/') or pin.get('parent_commit') != commit or
            pins.get(mount) != pin.get('pinned_commit') or
            reads.get(mount, {}).get('source_repo_id') != pin.get('source_repo_id')):
            return False
        commit, prefix, leaf_repo = pin['pinned_commit'], mount, pin['source_repo_id']
    relative = doc['path'][len(prefix)+1:] if prefix else doc['path']
    return (doc['source_commit'] == commit and
            doc.get('source_repo_id', manifest['repo_id']) == leaf_repo and
            doc.get('source_path', doc['path']) == relative)


def safe_path(value):
    parts = value.split('/')
    if (not value or '\\' in value or ':' in value or value.startswith('/')
            or any(p in ('', '.', '..') for p in parts)
            or any(ord(c) < 32 for c in value)):
        raise ValueError('invalid_git_path')
    return PurePosixPath(value).as_posix()


def git(repo, *arguments):
    # Block transports, lazy fetch, hooks and optional locks in subprocesses.
    environment = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith('GIT_')}
    environment.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1',
                       GIT_NO_LAZY_FETCH='1', GIT_CONFIG_NOSYSTEM='1',
                       GIT_CONFIG_GLOBAL=os.devnull)
    command = ['git', '--no-replace-objects', '-c', 'protocol.allow=never', '-c', 'core.fsmonitor=false',
               '-c', 'core.hooksPath='+os.devnull, '-C', str(repo), *arguments]
    try:
        return subprocess.run(command, env=environment, capture_output=True,
                              check=True, timeout=20).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Git diagnostics can contain credential-bearing remote URLs.
        raise ValueError('git_read_failed_offline') from None


def resolve_commit(repo, ref):
    value = git(repo, 'rev-parse', '--verify', ref+'^{commit}').decode().strip()
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
        raise ValueError('invalid_commit_identity')
    return value


def excluded_path(path):
    parts = path.casefold().split('/')
    if any(p in BLOCKED or p.startswith('.env.') for p in parts):
        return 'excluded_sensitive_or_generated_path'
    if not path.casefold().endswith(('.md', '.markdown')):
        return 'unsupported_document_type'
    return None


def document_metadata(text, path, allow_unmarked):
    metadata_lines = []
    for line in text.splitlines()[:40]:
        if re.match(r'^##\s', line):
            break
        metadata_lines.append(line)
    states = re.findall(r'^\s*(?:状态|Status)\s*[:：]\s*(\S+)\s*$',
                        '\n'.join(metadata_lines), re.MULTILINE | re.IGNORECASE)
    if states and any(s.casefold() != 'accepted' for s in states):
        return None, 'document_not_accepted'
    if not states and not allow_unmarked:
        return None, 'document_status_missing'
    heading = next((m.group(1).strip() for line in text.splitlines()
                    if (m := re.match(r'^#\s+(.+?)\s*#*\s*$', line))), None)
    return {'title': heading or PurePosixPath(path).name,
            'document_state': 'Accepted' if states else 'explicitly_allowed_unmarked'}, None


def snapshot(repo, commit, includes, repo_id, channel, allow_unmarked, excludes=(), repositories=()):
    entries, rejected, pins, reads = {}, [], [], []
    policy = pin_policy(repositories)
    root_commit = commit

    def walk(cache, revision, prefix='', leaf_repo=None, chain=()):
        if len(chain) > MAX_PIN_DEPTH:
            raise ValueError('pinned_source_too_deep')
        # Include ancestors so child/docs can discover the parent gitlink child.
        local_scopes = set()
        entire = False
        for scope in includes:
            if prefix and (scope == prefix or prefix.startswith(scope+'/')):
                entire = True
                break
            if not prefix or scope.startswith(prefix+'/'):
                local = scope[len(prefix)+1:] if prefix else scope
                parts = local.split('/')
                local_scopes.update('/'.join(parts[:n]) for n in range(1, len(parts)+1))
        args = () if entire else ('--', *(':(literal)'+p for p in sorted(local_scopes)))
        raw = git(cache, 'ls-tree', '-r', '-z', '--full-tree', revision, *args)
        for entry in raw.split(b'\0'):
            if not entry:
                continue
            header, raw_path = entry.split(b'\t', 1)
            mode, kind, blob = header.decode('ascii').split(' ')
            relative = safe_path(raw_path.decode('utf-8', errors='strict'))
            path = prefix+'/'+relative if prefix else relative
            is_pin = mode == '160000' and kind == 'commit'
            if not scoped(path, includes, ancestors=is_pin):
                continue
            if any(path == p or path.startswith(p+'/') for p in excludes):
                rejected.append({'path': path, 'reason': 'explicitly_excluded_scope'})
                continue
            if is_pin:
                pins.append({'path': path, 'pinned_commit': blob, 'child_worktree_read': False})
                if path in policy:
                    item = policy[path]
                    child = pinned_cache(item, blob)
                    pin = dict(path=path, parent_commit=revision, pinned_commit=blob, source_repo_id=item.repo_id)
                    reads.append(pin)
                    walk(child, blob, path, item.repo_id, (*chain, pin))
                continue
            reason = excluded_path(path)
            if mode not in ('100644', '100755') or kind != 'blob':
                reason = 'unsupported_mode_or_symlink'
            if reason:
                rejected.append({'path': path, 'reason': reason})
                continue
            size = int(git(cache, 'cat-file', '-s', blob))
            if size > MAX_DOCUMENT_BYTES:
                rejected.append({'path': path, 'reason': 'document_too_large'})
                continue
            body = git(cache, 'cat-file', 'blob', blob)
            if len(body) != size:
                raise ValueError('blob_size_changed')
            try:
                metadata, reason = document_metadata(body.decode('utf-8-sig'), path, allow_unmarked)
            except UnicodeDecodeError:
                metadata, reason = None, 'document_not_utf8'
            if reason:
                rejected.append({'path': path, 'reason': reason})
                continue
            entries[path] = dict(metadata, path=path, repo_id=repo_id, channel=channel,
                source_key=f'{repo_id}:{channel}:{path}', source_commit=revision,
                parent_commit=root_commit, source_repo_id=leaf_repo or repo_id,
                source_path=relative, pin_chain=list(chain), source_blob_sha=blob, size_bytes=size)

    walk(repo, commit)
    return entries, rejected, sorted(pins, key=lambda item: item['path']), sorted(reads, key=lambda item: item['path'])


def changes(before, after):
    removed, added = set(before)-set(after), set(after)-set(before)
    old_blobs, new_blobs = defaultdict(list), defaultdict(list)
    for path in removed:
        old_blobs[before[path]['source_blob_sha']].append(path)
    for path in added:
        new_blobs[after[path]['source_blob_sha']].append(path)
    result = []
    for blob in sorted(old_blobs.keys() & new_blobs.keys()):
        if len(old_blobs[blob]) == len(new_blobs[blob]) == 1:
            old, new = old_blobs[blob][0], new_blobs[blob][0]
            result.append({'action': 'rename_candidate', 'from': old, 'path': new,
                           'identity_resolution_required': True})
            removed.remove(old)
            added.remove(new)
    result.extend({'action': 'add', 'path': p} for p in sorted(added))
    result.extend({'action': 'delete_proposal', 'path': p,
                   'writer_ownership_check_required': True} for p in sorted(removed))
    result.extend({'action': 'modify', 'path': p} for p in sorted(before.keys() & after.keys())
                  if before[p]['source_blob_sha'] != after[p]['source_blob_sha'])
    return result


def build_manifest(repo, repo_id, channel, includes, ref=None, baseline=None,
                   allow_unmarked=False, excludes=(), repositories=()):
    repo = Path(repo).resolve(strict=True)
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', repo_id):
        raise ValueError('invalid_repo_id')
    includes = sorted({safe_path(p) for p in includes})
    excludes = sorted({safe_path(p) for p in excludes})
    if not includes or any(any(part.casefold() in BLOCKED for part in p.split('/'))
                           for p in includes):
        raise ValueError('explicit_safe_include_required')
    top = Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if top != repo:
        raise ValueError('explicit_repository_root_required')
    if channel == 'local':
        if ref not in (None, 'HEAD'):
            raise ValueError('local_channel_requires_HEAD')
        ref = 'HEAD'
    elif channel == 'official':
        if not ref or not ref.startswith('refs/remotes/'):
            raise ValueError('official_channel_requires_cached_remote_ref')
        git(repo, 'check-ref-format', ref)
    else:
        raise ValueError('unknown_channel')
    commit = resolve_commit(repo, ref)
    current, excluded, pins, reads = snapshot(repo, commit, includes, repo_id, channel,
                                             allow_unmarked, excludes, repositories)
    previous, baseline_commit = {}, None
    if baseline:
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', baseline):
            raise ValueError('baseline_must_be_exact_commit_sha')
        baseline_commit = resolve_commit(repo, baseline)
        previous, _, _, _ = snapshot(repo, baseline_commit, includes, repo_id, channel,
                                      allow_unmarked, excludes, repositories)
    manifest = {'schema_version': 1, 'mode': 'dry-run', 'repo_id': repo_id,
                'channel': channel, 'channel_namespace': f'{repo_id}/{channel}',
                'source_ref': ref, 'source_commit': commit,
                'baseline_commit': baseline_commit, 'includes': includes,
                'excludes': excludes,
                'allow_unmarked': allow_unmarked,
                'worktree_files_read': False, 'remote_refresh_performed': False,
                'official_ref_freshness': 'unknown_cached_ref' if channel == 'official' else None,
                'kb_write_performed': False, 'model_calls_performed': False,
                'documents': [current[p] for p in sorted(current)],
                'excluded': sorted(excluded, key=lambda item: item['path']),
                'gitlink_pins': pins, 'gitlink_cache_reads': reads,
                'unread_gitlinks': sorted({p['path'] for p in pins}-{p['path'] for p in reads}),
                'provenance_changes': [{'path': p, 'action': 'provenance_update'}
                    for p in sorted(previous.keys() & current.keys())
                    if previous[p]['source_blob_sha'] == current[p]['source_blob_sha'] and
                    any(previous[p].get(k) != current[p].get(k) for k in
                        ('source_commit', 'parent_commit', 'source_repo_id', 'source_path', 'pin_chain'))],
                'changes': changes(previous, current)}
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':')).encode('utf-8')
    manifest['manifest_sha256'] = hashlib.sha256(canonical).hexdigest()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--repo-id', required=True)
    parser.add_argument('--channel', required=True, choices=('local', 'official'))
    parser.add_argument('--include', action='append', required=True,
                        help='Explicit Git-relative directory or document; repeatable')
    parser.add_argument('--ref', help='Official cached refs/remotes/...; local uses HEAD')
    parser.add_argument('--baseline', help='Exact prior commit SHA for change proposals')
    parser.add_argument('--allow-unmarked', action='store_true',
                        help='Explicitly admit unmarked Markdown within the include scope')
    args = parser.parse_args()
    try:
        manifest = build_manifest(args.repo, args.repo_id, args.channel,
                                  args.include, args.ref, args.baseline,
                                  args.allow_unmarked)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    except (ValueError, OSError, UnicodeError):
        print(json.dumps({'status': 'dry_run_failed',
                          'reason': 'invalid_scope_or_unavailable_local_git_objects'}),
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
