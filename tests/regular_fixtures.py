"""Explicit synthetic market/network data; never a saved real trading result."""
import copy
import datetime as dt
import io
import json
import threading
import urllib.parse
import zipfile
from collector_v1.core import encoded,sha
from collector_v1.providers import LAYOUTS
from collector_v1 import trial
from collector_v1.regular import policy
from collector_v1.regular.github import APIError


def context(day='2026-10-29',run_id='99'):
    r=dict(id=run_id,event='schedule',created_at=day+'T11:30:01Z',head_sha='a'*40)
    c=policy.freeze(r,policy.CRON);c.update(mode='SCHEDULED_COLLECTION',artifact_name=c['generation_id']+'-run-'+run_id)
    return c


class Client:
    def __init__(self,c):self.c=c;self.docs={'FORMAT.json':copy.deepcopy(policy.FORMAT)};self.lock=threading.Lock();self.runs=[]
    def contents(self,path,*args,**kwargs):
        with self.lock:
            if path not in self.docs:raise APIError(404)
            value=copy.deepcopy(self.docs[path]);return value,sha(encoded(value))
    def write(self,path,value,version=None):
        with self.lock:
            old=self.docs.get(path)
            if (old is None and version is not None) or (old is not None and version!=sha(encoded(old))):raise APIError(409)
            self.docs[path]=copy.deepcopy(value)
        return {}
    def run(self,id,**kwargs):return dict(id=id,status='in_progress',head_sha=self.c['code_commit'],path=policy.WORKFLOW)
    def active(self,*args):return copy.deepcopy(self.runs)
    def workflow(self,run):return 'name: SYNTHETIC non-KIS work\non:\n  workflow_dispatch:\n'


def master_raw(market,count,halt_one=False):
    layout=LAYOUTS[market];vals=['0']*len(layout['widths'])
    vals[layout['group_index']]='ST';vals[layout['preferred_index']]='0';vals[layout['listing_date_index']]='20000101'
    for i,name in enumerate(layout['columns']):
        if name in ('거래정지','거래정지 여부','관리종목','관리 종목 여부','정리매매','정리매매 여부','SPAC','기업인수목적회사여부'):vals[i]='N'
    symbols=[s for s,(m,_) in trial.TARGETS.items() if m==market]
    symbols += [f'{i:06}' for i in range(100000 if market=='KOSPI' else 400000,(100000 if market=='KOSPI' else 400000)+count)]
    lines=[]
    for code in symbols:
        cells=vals[:]
        if halt_one and code=='100000':
            for i,n in enumerate(layout['columns']):
                if n in ('거래정지','거래정지 여부'):cells[i]='Y'
        name=trial.TARGETS[code][1] if code in trial.TARGETS else 'SYNTHETIC ONLY'
        lines.append(code.ljust(9)+'KR0000000000'+name+''.join(v.ljust(n) for v,n in zip(cells,layout['widths'])))
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:z.writestr(layout['member'],'\n'.join(lines).encode('cp949'))
    return out.getvalue()


def transport(c,*,closed=False,unknown_calendar=False,conflict=False,halt_one=False):
    calls=[];day=c['asof'].replace('-','');masters={m:master_raw(m,600 if m=='KOSPI' else 1400,halt_one) for m in ('KOSPI','KOSDAQ')}
    class Response:
        status=200
        def __init__(self,value):self.raw=value if isinstance(value,bytes) else encoded(value)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return self.raw
    def opener(req,timeout):
        calls.append(req.full_url);u=urllib.parse.urlparse(req.full_url);p=urllib.parse.parse_qs(u.query,keep_blank_values=True)
        if u.path.endswith('tokenP'):return Response({'access_token':'SYNTHETIC_SESSION_ONLY'})
        if 'chk-holiday' in u.path:return Response({'rt_cd':'0','output':[] if unknown_calendar else [{'bass_dt':day,'opnd_yn':'N' if closed else 'Y'}]})
        for m,l in LAYOUTS.items():
            if req.full_url==l['url']:return Response(masters[m])
        if u.path.endswith('inquire-daily-indexchartprice'):return Response({'rt_cd':'0','output2':[{'stck_bsop_date':day,'bstp_nmix_prpr':'1000'}]})
        code=p['FID_INPUT_ISCD'][0]
        if u.path.endswith('investor-trade-by-stock-daily'):
            return Response({'rt_cd':'0','output2':[dict(stck_bsop_date=day,frgn_ntby_qty='3',frgn_ntby_tr_pbmn='2',orgn_ntby_qty='-1',orgn_shnu_vol='1',orgn_seln_vol='2',orgn_ntby_tr_pbmn='100',stck_clpr='105',acml_vol='10',acml_tr_pbmn='1050')]})
        assert u.path.endswith('inquire-daily-itemchartprice') and p['FID_ORG_ADJ_PRC']==['1']
        return Response({'rt_cd':'0','output1':{'stck_shrn_iscd':code},'output2':[dict(stck_bsop_date=day,stck_oprc='100',stck_hgpr='110',stck_lwpr='90',stck_clpr='106' if conflict and code=='100000' else '105',acml_vol='10',acml_tr_pbmn='1050',mod_yn='N')]})
    return calls,opener
