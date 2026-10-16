"""Two saved failures only: public response evidence, no state mutation or secrets."""
import datetime as dt, hashlib, io, json, re, sys, zipfile, gzip
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.collect_corporate import Dart
from screener.corporate import load_state, business_section

def run():
    api=Dart(max_calls=12,max_seconds=180);state=load_state();out=Path('reports/corporate-diagnosis');out.mkdir(parents=True,exist_ok=True)
    report={'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'filings':[]}
    import os
    receipts=os.environ.get('DIAGNOSTIC_RECEIPTS','20260325000430,20260330001721').split(',')
    if len(receipts)>3 or any(not re.fullmatch(r'[0-9]{14}',r) or r not in state['filings'] for r in receipts):raise ValueError('Invalid bounded diagnostic receipts')
    for receipt in receipts:
        f=state['filings'][receipt];r={'requested_filing':f,'previous_job':state['jobs'][receipt]}
        try:
            raw=api.get('document.xml',{'rcept_no':receipt})
            r['document']={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'zip_signature':raw.startswith(b'PK'),'transport':getattr(api,'last_response',{})}
            (out/(receipt+'.document.gz')).write_bytes(gzip.compress(raw,mtime=0))
            if zipfile.is_zipfile(io.BytesIO(raw)):
                entries=[]
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    for info in z.infolist():
                        item={'name':info.filename,'bytes':info.file_size}
                        if info.filename.lower().endswith('.xml') and info.file_size<30000000:
                            xml=z.read(info).decode('utf-8');item['titles']=re.findall(r'<TITLE\b[^>]*>(.*?)</TITLE>',xml,re.S)[:150]
                            item['head']=xml[:2500]
                            try:item['business_characters']=len(business_section(xml))
                            except ValueError as e:item['error']=str(e)
                        entries.append(item)
                r['document']['entries']=entries
            else:r['document']['error_body']=raw.decode('utf-8',errors='replace')[:1500]
        except Exception as e:r['document_error']=str(e) if isinstance(e,ValueError) else type(e).__name__
        try:
            d=api.get('fnlttSinglAcntAll.json',{'corp_code':f['corp_code'],'bsns_year':str(f['year']),'reprt_code':f['report_code'],'fs_div':'CFS'})
            (out/(receipt+'.financial.json.gz')).write_bytes(gzip.compress(json.dumps(d,ensure_ascii=False).encode(),mtime=0))
            r['financial']={'status':d.get('status'),'rows':len(d.get('list',[])),'identities':[dict(zip(['rcept_no','reprt_code','bsns_year','corp_code','fs_div'],v)) for v in sorted({tuple(str(x.get(k)) for k in ['rcept_no','reprt_code','bsns_year','corp_code','fs_div']) for x in d.get('list',[])})]}
            d=api.get('list.json',{'corp_code':f['corp_code'],'bgn_de':'20260101','end_de':dt.date.today().strftime('%Y%m%d'),'pblntf_ty':'A','page_count':100})
            r['official_filing_list']=d
        except Exception as e:r['financial_error']=str(e) if isinstance(e,ValueError) else type(e).__name__
        try:
            audit=api.get('accnutAdtorNmNdAdtOpinion.json',{'corp_code':f['corp_code'],'bsns_year':str(f['year']),'reprt_code':f['report_code']})
            r['audit_opinion_response']=audit
            r['audit_identity_verified']=bool(audit.get('status')=='000' and audit.get('list') and all(x.get('corp_code')==f['corp_code'] and x.get('rcept_no')==receipt and x.get('bsns_year')==str(f['year']) for x in audit['list']))
        except Exception as e:r['audit_error']=str(e) if isinstance(e,ValueError) else type(e).__name__
        report['filings'].append(r)
    report['calls']=api.calls
    (out/'diagnosis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'filings':len(report['filings']),'calls':api.calls}))
if __name__=='__main__':run()
