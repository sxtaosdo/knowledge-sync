"""Local, nonsecret allow-list. UI requests never define a source or approval."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time

from knowledge_sync import safe_path, excluded_path, PinnedRepository, pin_policy
from offline_writer import Binding, digest

SOURCE_ID = re.compile(r'[a-z][a-z0-9._-]{0,63}')


def require_current_policy(source,path=None):
    """Re-read the fixed local allow-list before a production mutation or credential access."""
    registry=Registry.load(path or Path(__file__).resolve().parent/'sources.json')
    current=registry.get(source.source_id,source.binding.channel)
    if current.scope!=source.scope:
        raise ValueError('POLICY_SCOPE_CHANGED')
    current.require_writer()
    return current


@dataclass(frozen=True)
class Source:
    source_id: str
    repo: Path
    binding: Binding
    ref: str
    ledger: Path
    excludes: tuple = ()
    allow_unmarked: bool = False
    max_deletes: int = 0
    max_delete_fraction: float = 0
    writer_enabled: bool = False
    approved_scope: str = ''
    approval_expires: float = 0
    network_enabled: bool = False
    base_url: str = ''
    credential_env: str = ''
    processing: tuple = ()
    schedule_enabled: bool = False
    interval_seconds: int = 0
    gitlinks: tuple = ()
    index_verifier: str = ''
    required_indexes: tuple = ()

    @property
    def scope(self):
        # Credentials are references only; no secret values appear in this contract.
        return digest(dict(sourceId=self.source_id, repo=str(self.repo),
                           binding=self.binding.__dict__, ref=self.ref,
                           excludes=self.excludes, allowUnmarked=self.allow_unmarked,
                           maxDeletes=self.max_deletes,
                           maxDeleteFraction=self.max_delete_fraction,
                           ledger=str(self.ledger), baseUrl=self.base_url,
                           credentialEnv=self.credential_env,
                           processing=self.processing, intervalSeconds=self.interval_seconds,
                           gitlinks=[dict(path=p.path,repo=str(p.repo),repoId=p.repo_id) for p in sorted(self.gitlinks,key=lambda p:p.path)],
                           indexVerifier=self.index_verifier,requiredIndexes=self.required_indexes))

    def approved(self, now=None):
        return self.approved_scope == self.scope and self.approval_expires > (time.time() if now is None else now)

    def require_writer(self, now=None):
        if not self.approved(now):
            raise ValueError('POLICY_NOT_APPROVED')
        if not self.writer_enabled:
            raise ValueError('WRITER_DISABLED')
        if not self.network_enabled:
            raise ValueError('NETWORK_DISABLED')


class Registry:
    def __init__(self, sources=()):
        self.sources = {}
        targets, ledgers = set(), set()
        for source in sources:
            key = (source.source_id, source.binding.channel)
            target = (source.binding.tenant_id, source.binding.kb_id)
            if key in self.sources or target in targets or source.ledger in ledgers:
                raise ValueError('DUPLICATE_SOURCE_OR_TARGET')
            self.sources[key] = source
            targets.add(target)
            ledgers.add(source.ledger)

    def get(self, source_id, channel):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ValueError('INVALID_SOURCE_ID')
        if channel not in ('local', 'official'):
            raise ValueError('INVALID_CHANNEL')
        try:
            return self.sources[source_id, channel]
        except KeyError:
            raise ValueError('POLICY_NOT_APPROVED') from None

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError('INVALID_REGISTRY')
        data = json.loads(path.read_text(encoding='utf-8'))
        if set(data) != {'schemaVersion', 'sources'} or data['schemaVersion'] != 1 or not isinstance(data['sources'], list):
            raise ValueError('INVALID_REGISTRY')
        sources = []
        allowed = {'sourceId','repo','repoId','channel','ref','includes','excludes',
                   'tenantId','kbId','owner','ledger','allowUnmarked','maxDeletes',
                   'maxDeleteFraction','writerEnabled','approvedScope','approvalExpires',
                   'networkEnabled','baseUrl','credentialEnv','processing',
                   'scheduleEnabled','intervalSeconds','gitlinks','indexVerifier','requiredIndexes'}
        for item in data['sources']:
            if not isinstance(item, dict) or set(item)-allowed:
                raise ValueError('INVALID_REGISTRY')
            channel, ref = item['channel'], item['ref']
            if not SOURCE_ID.fullmatch(item['sourceId']):
                raise ValueError('INVALID_SOURCE_ID')
            if channel == 'local' and ref != 'HEAD' or channel == 'official' and not ref.startswith('refs/remotes/'):
                raise ValueError('INVALID_SOURCE_REF')
            includes = tuple(sorted({safe_path(p) for p in item['includes']}))
            excludes = tuple(sorted({safe_path(p) for p in item.get('excludes', [])}))
            if any(excluded_path(p+'/scope.md') for p in includes):
                raise ValueError('INVALID_INCLUDE')
            for field in ('allowUnmarked','writerEnabled','networkEnabled','scheduleEnabled'):
                if type(item.get(field, False)) is not bool:
                    raise ValueError('INVALID_POLICY_FLAG')
            count, fraction, interval = item.get('maxDeletes',0), item.get('maxDeleteFraction',0), item.get('intervalSeconds',0)
            if type(count) is not int or count < 0 or type(interval) is not int or interval < 0 or (interval and interval < 60):
                raise ValueError('INVALID_POLICY_LIMIT')
            if not isinstance(fraction,(int,float)) or not 0 <= fraction <= 1:
                raise ValueError('INVALID_POLICY_LIMIT')
            repo, ledger = Path(item['repo']), Path(item['ledger'])
            if not repo.is_absolute() or not ledger.is_absolute():
                raise ValueError('ABSOLUTE_REGISTERED_PATH_REQUIRED')
            credential = item.get('credentialEnv','')
            if credential and not re.fullmatch(r'KNOWLEDGE_SYNC_[A-Z0-9_]+', credential):
                raise ValueError('INVALID_CREDENTIAL_REFERENCE')
            processing = item.get('processing', {})
            if not isinstance(processing,dict) or any(type(v) is not bool for v in processing.values()):
                raise ValueError('INVALID_PROCESSING_POLICY')
            pins = []
            raw_pins = item.get('gitlinks', [])
            if not isinstance(raw_pins,list):
                raise ValueError('INVALID_PIN_POLICY')
            for pin in raw_pins:
                if not isinstance(pin,dict) or set(pin)!={'path','repo','repoId'}:
                    raise ValueError('INVALID_PIN_POLICY')
                raw_repo=Path(pin['repo'])
                if not raw_repo.is_absolute() or raw_repo.is_symlink():
                    raise ValueError('INVALID_PIN_POLICY')
                pins.append(PinnedRepository(safe_path(pin['path']),raw_repo.resolve(),pin['repoId']))
            pins=tuple(sorted(pins,key=lambda p:p.path))
            pin_policy(pins)
            verifier=item.get('indexVerifier','')
            indexes=item.get('requiredIndexes',[])
            if (not isinstance(verifier,str) or verifier and not SOURCE_ID.fullmatch(verifier) or
                not isinstance(indexes,list) or any(i not in ('keyword','vector') for i in indexes) or
                len(set(indexes))!=len(indexes)):
                raise ValueError('INVALID_INDEX_POLICY')
            sources.append(Source(source_id=item['sourceId'],repo=repo.resolve(),
                binding=Binding(item['repoId'],channel,str(item['tenantId']),item['kbId'],item['owner'],includes),
                ref=ref,ledger=ledger.resolve(),excludes=excludes,allow_unmarked=item.get('allowUnmarked',False),
                max_deletes=count,max_delete_fraction=fraction,writer_enabled=item.get('writerEnabled',False),
                approved_scope=item.get('approvedScope',''),approval_expires=item.get('approvalExpires',0),
                network_enabled=item.get('networkEnabled',False),base_url=item.get('baseUrl',''),
                credential_env=credential,processing=tuple(sorted(processing.items())),
                schedule_enabled=item.get('scheduleEnabled',False),interval_seconds=interval,
                gitlinks=pins,index_verifier=verifier,required_indexes=tuple(sorted(indexes))))
        return cls(sources)
