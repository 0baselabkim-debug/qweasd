"""Append validated symbol outcomes before periodic bundle compression.

A truncated final line is ignored after interruption; prior complete, hashed lines survive.
The journal is replayed only for its exact trading date. No response is marked successful
until the collector's existing validation accepts it.
"""
import hashlib,json,os
from pathlib import Path

class Journal:
    def __init__(self,path,day):self.path=Path(path);self.day=day
    def append(self,code,record=None,excluded=None):
        data={'day':self.day,'symbol':code,'record':record,'excluded':excluded}
        raw=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        envelope=json.dumps({'sha256':hashlib.sha256(raw.encode()).hexdigest(),'data':data},ensure_ascii=False,separators=(',',':'))
        with self.path.open('a',encoding='utf-8') as f:
            f.write(envelope+'\n');f.flush();os.fsync(f.fileno())
    def restore(self,bundle):
        if not self.path.exists():return 0
        count=0;valid_bytes=0
        for raw in self.path.read_bytes().splitlines(keepends=True):
            if not raw.endswith(b'\n'):
                with self.path.open('r+b') as f:f.truncate(valid_bytes)
                break
            valid_bytes+=len(raw)
            entry=json.loads(raw);d=entry['data']
            canonical=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            if hashlib.sha256(canonical.encode()).hexdigest()!=entry['sha256'] or d['day']!=self.day:raise ValueError('Checkpoint journal hash/date mismatch')
            key='records' if d['record'] is not None else 'excluded';value=d['record'] if key=='records' else d['excluded']
            if value is None:raise ValueError('Empty checkpoint outcome')
            code=d['symbol']
            if code in bundle[key] and bundle[key][code]!=value:raise ValueError('Conflicting validated checkpoint outcome')
            if code not in bundle[key]:bundle[key][code]=value;count+=1
            bundle['failures'].pop(code,None)
        return count
