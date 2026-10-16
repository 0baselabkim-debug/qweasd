"""Measure against the user-specified baseline; no API calls or data mutations."""
import collections,gzip,hashlib,io,json,subprocess,sys,tarfile,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from screener.store import load,PACKAGED_DB
from screener.technical_quality import blocking_conflicts,daily_accepted,digest
from screener.technical import buy_guide,validate
from screener.indicators import universe,stock_activity,sector_indicators,turnover_matrix
from screener.corporate import load_state
BASE='c834c172b6fe733add5d35e35c22ffc8f5b467d6'
def at(path):return subprocess.check_output(['git','show',BASE+':'+path])
def run():
    d=load(PACKAGED_DB);day=str(d['flows'].trade_date.max().date());x,m=universe(d,day);x=stock_activity(d,x,day);sectors,_=sector_indicators(d,x,m,day);codes=set(x.symbol)
    original={}
    with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git','archive',BASE,'technical/market']))) as tar:
        for member in tar:
            if member.name.endswith('.json.gz'):
                b=json.loads(gzip.decompress(tar.extractfile(member).read()));original[b['symbol']]=b
    guides=collections.Counter();accepted=[];active=[];recovered=[];newly=[]
    for code in codes:
        b=json.loads(gzip.decompress((Path('technical/market')/(code+'.json.gz')).read_bytes()));before=original.get(code,{})
        ok=daily_accepted(b) and not validate(b,day)
        if ok:accepted.append(code)
        if blocking_conflicts(b):active.append(code)
        guides[buy_guide(b,day)['status']]+=1
        if ok and not daily_accepted(before):
            record={'symbol':code,'name':x.set_index('symbol').loc[code,'name'],'asof':b['asof'],'before_sha256':digest(before),'after_sha256':digest(b),'guide':buy_guide(b,day)['status']}
            (recovered if blocking_conflicts(before) else newly).append(record)
    baseline_db=Path('/tmp/stabilization-baseline.duckdb');baseline_db.write_bytes(at('snapshot/dashboard.duckdb'));old=load(baseline_db)
    preservation={}
    for t in ['instruments','memberships','flows','prices','financials','benchmarks','calendar','exclusions']:
        pd.testing.assert_frame_equal(old[t],d[t]);preservation[t]={'identical':True,'rows':len(d[t])}
    from screener.scoring import evaluate
    before_scores=evaluate(old,day);after_scores=evaluate(d,day)
    # evaluate returns sector/stock frames; retain exact legacy output including holds.
    for a,b in zip(before_scores,after_scores):
        if isinstance(a,pd.DataFrame):pd.testing.assert_frame_equal(a,b)
    state=load_state();prior=json.loads(gzip.decompress(at('snapshot/corporate-state.json.gz')))
    corp={'business_success_before':sum(j.get('business_status')=='success' for j in prior['jobs'].values()),'business_success_after':sum(j.get('business_status')=='success' for j in state['jobs'].values()),
          'failed_financial_jobs':sum(j.get('financial_status')=='failed' for j in state['jobs'].values()),'attachment_held':sum(j.get('financial_status')=='held_attachment_only' for j in state['jobs'].values()),
          'source_reviews':len(state.get('document_source_reviews',{})),
          'prior_versions_preserved':all(state['financials'][k]==v for k,v in prior['financials'].items()),'prior_events_preserved':state['events'][:len(prior['events'])]==prior['events']}
    independent=[]
    for row in sectors[sectors.activity_ratio.notna()].itertuples():
        members=x[(x.market==row.market)&(x.sector==row.sector)].symbol.tolist();matrix=turnover_matrix(d,members,[row.market],day)
        assert matrix.shape==(25,len(members)) and not matrix.isna().any().any()
        daily=[sum(float(matrix.loc[date,c]) for c in members) for date in matrix.index]
        ratio=(sum(daily[-5:])/5)/(sum(daily[:20])/20)
        assert abs(ratio-row.activity_ratio)<1e-12
        independent.append({'market':row.market,'sector':row.sector,'symbols':members,'activity':ratio,'dates':[str(v.date()) for v in matrix.index],'daily_turnover':daily})
    rules={name:at(name)==Path(name).read_bytes() for name in ['technical_rules.json','eligibility_rules.json','theme_mapping.json','config.toml']};assert all(rules.values())
    r={'baseline':BASE,'asof':day,'universe':len(x),'accepted':len(accepted),'guides':dict(guides),'active_conflicts':len(active),'baseline_blocking':sum(bool(blocking_conflicts(b)) for code,b in original.items() if code in codes),
       'recovered_existing_blocked':sorted(recovered,key=lambda r:r['symbol']),'newly_verified_nonblocked':sorted(newly,key=lambda r:r['symbol']),
       'stock_activity':int(x.activity_ratio.notna().sum()),'sector_activity':len(independent),'independent_complete_sectors':independent,'raw_preservation':preservation,'legacy_scores_unchanged':True,'rules_unchanged':rules,
       'corporate':corp,'before_db_sha256':hashlib.sha256(baseline_db.read_bytes()).hexdigest(),'after_db_sha256':hashlib.sha256(PACKAGED_DB.read_bytes()).hexdigest(),'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
    out=Path('reports/operations-stabilization/final-verification.json');out.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in r.items() if k not in ['recovered_existing_blocked','newly_verified_nonblocked','independent_complete_sectors']},ensure_ascii=False))
if __name__=='__main__':run()
