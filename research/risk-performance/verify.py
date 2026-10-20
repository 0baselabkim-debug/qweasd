"""Independent artifact checks; no API calls and no production writes."""
from pathlib import Path
import csv, gzip, hashlib, json
ROOT=Path(__file__).resolve().parent

def rows(name):
    with (ROOT/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def verify():
    source=json.loads((ROOT/'inputs/source.json').read_text())
    for name,sha in source['files'].items():
        assert hashlib.sha256((ROOT/'inputs'/name).read_bytes()).hexdigest()==sha
    signals=rows('signals.csv');trades=rows('trades.csv');performance=rows('performance.csv')
    assert len({s['signal_id'] for s in signals})==len(signals)
    hist=[s for s in signals if s['record_kind']=='historical_partial_universe']
    refs=[s for s in signals if s['record_kind']=='retrospective_reference']
    assert len(hist)==55 and len(refs)==1287
    assert {g:sum(s['group']==g for s in hist) for g in 'ABCD'}==dict(A=1,B=0,C=0,D=54)
    assert {g:sum(s['group']==g for s in refs) for g in 'ABCD'}==dict(A=36,B=15,C=20,D=1216)
    # Independent summation of original dated bundle flows (not display CSV sums).
    from datetime import datetime
    for s in hist:
        raw=(ROOT/s['source']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==s['data_version']
        b=json.loads(gzip.decompress(raw) if s['source'].endswith('.gz') else raw)
        f=sorted(b['flows'],key=lambda x:x['trade_date'])[-20:]
        assert len(f)==20 and len({x['trade_date'] for x in f})==20
        assert f[-1]['trade_date']==s['signal_date']=='2026-10-15'
        recent=sum(x['net_amount'] for x in f[-5:]);previous=sum(x['net_amount'] for x in f[-10:-5])
        for key,value in [('net_5d_krw',recent),('previous_5d_krw',previous),('change_5d_krw',recent-previous),('net_20d_krw',sum(x['net_amount'] for x in f))]:
            assert float(s[key])==value
        assert datetime.fromisoformat(s['quality_known_at'])<datetime.fromisoformat('2026-10-16T09:00:00+09:00')
        assert max(x['trade_date'] for x in b['bars'])==s['signal_date']
    ids={s['signal_id'] for s in hist}
    assert all(s['pit_eligible']=='False' for s in refs)
    assert all(t['signal_id'] in ids for t in trades)
    assert len(trades)==168 and len(performance)==96
    assert all(t['entry_date']=='2026-10-16' and t['observed_sessions']=='3' for t in trades)
    assert all(not t[k] for t in trades for k in ['entry_price','exit_price','net_return','gross_return','mae'])
    assert all(p['completed_trades']=='0' and not p['mean_net_return'] and not p['mdd'] for p in performance)
    audit=rows('entry-observation-audit.csv')
    assert len(audit)==55 and sum(bool(x['changed_fields']) for x in audit)==37
    assert all(x['raw_OHLC_present']=='True' for x in audit)
    original=json.loads(gzip.decompress((ROOT/'inputs/checkpoint-20261016.json.gz').read_bytes()))['records']
    later=json.loads(gzip.decompress((ROOT.parent/'full-market/inputs/checkpoint-20261020.json.gz').read_bytes()))['records']
    fields=['stck_oprc','stck_hgpr','stck_lwpr','stck_clpr','acml_vol','acml_tr_pbmn']
    for row in audit:
        raw=original[row['symbol']]['flow']
        adj=next((x for x in later.get(row['symbol'],{}).get('adjusted_prices',[]) if x['stck_bsop_date']=='20261016'),None)
        assert float(row['raw_open'])==float(raw['stck_oprc'])
        assert row['changed_fields']==(','.join(k for k in fields if float(raw[k])!=float(adj[k])) if adj else '')
    return {'status':'PASS','independent_flow_checks':len(hist)*4,'historic_signals':len(hist),
            'reference_records_excluded_from_trades':len(refs),'completed_outcomes':0,'market_API_calls':0}

if __name__=='__main__':print(json.dumps(verify(),ensure_ascii=False,indent=2))
