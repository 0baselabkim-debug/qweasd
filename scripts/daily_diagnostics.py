"""Immutable, credential-free market-response evidence for failed stock checks."""
import datetime as dt
import hashlib
import json
from pathlib import Path

SENSITIVE={'authorization','appkey','appsecret','accesstoken','refreshtoken','token','authkey','cano','account','accountnumber','acntprdtcd'}

def redact(value,secrets=()):
    if isinstance(value,dict):
        return {k:('[REDACTED]' if ''.join(c for c in str(k).lower() if c.isalnum()) in SENSITIVE else redact(v,secrets)) for k,v in value.items()}
    if isinstance(value,list):return [redact(v,secrets) for v in value]
    if isinstance(value,str):
        for secret in secrets:
            if secret:value=value.replace(secret,'[REDACTED]')
    return value

def preserve_failure(directory,day,symbol,error,observations,secrets=()):
    if directory is None:return None
    if len(day)!=8 or not day.isdigit() or len(symbol)!=6 or not symbol.isdigit():raise ValueError('Invalid diagnostic identity')
    record=redact({'schema':1,'trade_date':day,'symbol':symbol,'recorded_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'error':error,'observations':observations,'decision':'REJECTED_PENDING_REVIEW','originals_not_overwritten':True},secrets)
    raw=(json.dumps(record,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    name=hashlib.sha256(raw).hexdigest()+'.json';folder=Path(directory)/day/symbol;folder.mkdir(parents=True,exist_ok=True)
    path=folder/name
    # Exclusive create: no observation can replace a previous one.
    with path.open('xb') as f:f.write(raw)
    return str(path)
