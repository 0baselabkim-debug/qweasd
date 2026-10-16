"""Classify observed differences separately from unproven economic causes."""
import collections,gzip,json,hashlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.technical_batch import read
from screener.technical_quality import blocking_conflicts,daily_accepted,FIELDS,digest
from screener.store import load,PACKAGED_DB
from screener.indicators import universe

def run():
    data=load(PACKAGED_DB);day=str(data['flows'].trade_date.max().date());x,_=universe(data,day);rows=[]
    for code in x.symbol:
        b=read(Path('technical/market')/(code+'.json.gz'));p=b.get('price_comparison',{});active=blocking_conflicts(b)
        if not active:continue
        adjusted=p.get('kis_adjusted');raw=p.get('kis_unadjusted');official=p.get('krx_official_unadjusted')
        rows.append({'symbol':code,'asof':b['asof'],'blocking_reasons':active,'bundle_sha256':digest(b),
                     'adjusted_raw_difference_observed':adjusted is not None and raw is not None and adjusted!=raw,
                     'kis_krx_raw_difference_observed':raw is not None and official is not None and raw!=official,
                     'corporate_action_rows':len(b.get('corporate_actions',[])),
                     'market_scope_cause':'unconfirmed; KIS request J is KRX, not evidence of NX/UN cause',
                     'corporate_action_cause':'unconfirmed','root_cause':'not established by a close mismatch alone'})
    refs=[];ledger=read(Path('technical/reference-status.json'),{})
    for p in Path('technical/reference').rglob('*.json.gz'):
        c=read(p);checks=[r for r in ledger.get('checks',[]) if r.get('http')==200 and r.get('url')==c.get('url') and r.get('checked_at')==c.get('observed_at')]
        refs.append({'file':str(p),'file_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'rows_sha256':digest(c['rows']),'rows':len(c['rows']),
                     'asof':c['asof'],'url':c.get('url'),'observed_at':c.get('observed_at'),'embedded_http':c.get('http'),
                     'matching_http_200_records':checks,'reuse_policy':'normal same-date verification permitted; fresh response mandatory for conflict recovery'})
    r={'asof':day,'current_blocking_symbols':len(rows),'different_concepts':{'legacy_database_masks_by_date':{d:len(v) for d,v in data['metadata'].get('adjusted_price_conflicts',{}).items()},
        'adjusted_raw_difference_observed':sum(v['adjusted_raw_difference_observed'] for v in rows),'kis_krx_raw_difference_observed':sum(v['kis_krx_raw_difference_observed'] for v in rows),
        'corporate_action_evidence_present':sum(v['corporate_action_rows']>0 for v in rows),'cause_confirmed_KRX_vs_UN':0,'cause_confirmed_KRX_vs_NXT':0},
        'warning':'flags overlap; corporate action occurrence or equal volume does not prove the cause','symbols':rows,'references':refs}
    out=Path('reports/operations-stabilization');(out/'conflicts-and-references.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k not in ['symbols','references']},ensure_ascii=False))
if __name__=='__main__':run()
