"""One request / one response bridge. No subprocess commands from frontend input."""
import argparse
import json
import sys

from sync_service import Service
from native_canary_adapter import native_adapter_factory

OPERATIONS=('status','dry-run','sync-once','scheduler-status')


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError('INVALID_REQUEST')
        result[key]=value
    return result


def handle(operation,raw,service=None):
    try:
        if len(raw)>4096:
            raise ValueError('INVALID_REQUEST')
        request=json.loads(raw,object_pairs_hook=unique_object)
        data=(service or Service(adapter_factory=native_adapter_factory)).dispatch(operation,request)
        return {'schemaVersion':1,'ok':True,'data':data,'error':None}
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and str(exc) in {
            'POLICY_NOT_APPROVED','INVALID_REQUEST','INVALID_OPERATION','INVALID_SOURCE_ID',
            'INVALID_CHANNEL','WRITER_DISABLED','NETWORK_DISABLED','WRITER_CREDENTIAL_MISSING',
            'INDEX_ATTESTATION_REQUIRED','RETENTION_REVIEW_REQUIRED','DELETE_POLICY_EXCEEDED',
            'POLICY_SCOPE_CHANGED',
            'PINNED_SOURCE_NOT_REGISTERED',
            'prior_batch_unresolved','baseline_checkpoint_mismatch'} else 'SYNC_UNAVAILABLE'
        return {'schemaVersion':1,'ok':False,'data':{'configured':False,'approved':False,
                'enabled':False,'writerReady':False,'schedulerEnabled':False},
                'error':{'code':code,'message':'Sync is disabled, blocked, or unavailable; inspect local nonsecret policy and job status.'}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation',required=True,choices=OPERATIONS)
    args=parser.parse_args()
    raw=sys.stdin.buffer.read(4097)
    try:
        result=handle(args.operation,raw.decode('utf-8'))
    except UnicodeDecodeError:
        result=handle(args.operation,'null')
    sys.stdout.buffer.write((json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8'))
    return 0 # Protocol ok, including a structured domain failure; envelope.ok is authoritative.


if __name__=='__main__':
    raise SystemExit(main())
