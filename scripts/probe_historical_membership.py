"""One public KRX screen probe; stop on login/access denial, never substitute current constituents."""
import datetime as dt,gzip,hashlib,http.cookiejar,json,urllib.error,urllib.parse,urllib.request
from pathlib import Path

def main():
    out=Path('reports/sector');out.mkdir(parents=True,exist_ok=True)
    result={'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'source':'https://data.krx.co.kr/','attempts':[],'usable_history':False}
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    for market in ['STK','KSQ']:
        for day in ['20260911','20261014']:
            params={'bld':'dbms/MDC/STAT/standard/MDCSTAT03901','mktId':market,'trdDd':day,'money':'1','csvxls_isNo':'false'}
            try:
                req=urllib.request.Request('https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd',data=urllib.parse.urlencode(params).encode(),headers={'Content-Type':'application/x-www-form-urlencoded','Referer':'https://data.krx.co.kr/'})
                with opener.open(req,timeout=30) as r:status=r.status;raw=r.read(10000000);url=r.url
                try:d=json.loads(raw)
                except ValueError:d=None
                record={'market':market,'date':day,'http':status,'final_url':url,'sha256':hashlib.sha256(raw).hexdigest(),'keys':list(d) if isinstance(d,dict) else [],'bytes':len(raw)}
                if d is not None:(out/f'industry-{market}-{day}.json.gz').write_bytes(gzip.compress(raw,mtime=0))
                else:record['body_prefix']=raw[:400].decode('utf-8',errors='replace')
                result['attempts'].append(record)
                if status in [401,403] or 'login' in url.lower() or d is None:break
            except Exception as e:
                result['attempts'].append({'market':market,'date':day,'error':type(e).__name__,'http':getattr(e,'code',None)});break
    (out/'historical-membership-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
