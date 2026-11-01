"""Explicitly started internal worker. No startup registration is performed."""
import argparse
import json
import time

from scheduler import Scheduler
from sync_service import Service,REGISTRY_PATH
from source_registry import Registry
from native_canary_adapter import native_adapter_factory


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true',help='One scheduler/worker tick, then exit')
    args=parser.parse_args()
    service=Service(adapter_factory=native_adapter_factory)
    scheduler=Scheduler(service)
    try:
        while True:
            service.registry=Registry.load(REGISTRY_PATH)
            results=scheduler.tick()
            for source in service.registry.sources.values():
                if not source.ledger.exists():
                    continue
                try:
                    results.append({'sourceId':source.source_id,'channel':source.binding.channel,
                        'ok':True,'state':service.work_once(source)})
                except Exception:
                    results.append({'sourceId':source.source_id,'channel':source.binding.channel,
                        'ok':False,'state':'blocked'})
            print(json.dumps({'schemaVersion':1,'results':results},separators=(',',':')),flush=True)
            if args.once:
                return 0
            time.sleep(3)
    except KeyboardInterrupt:
        return 0


if __name__=='__main__':
    raise SystemExit(main())
