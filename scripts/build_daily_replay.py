"""Assemble archived official responses for the publication rehearsal, without market calls."""
import argparse,gzip,json
from pathlib import Path
import duckdb

def read(path):
    p=Path(path);return json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_text())

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--original-flows',type=Path,required=True);p.add_argument('--absence-evidence',type=Path,required=True);a=p.parse_args()
    original=read(a.original_flows);absent=read(a.absence_evidence);quotes=read('staging/kis_quotes_20261014.json.gz');support=read('staging/sector_support.json.gz');sample=read('research/kis_history_sample.json.gz');ref=read('research/kis_reference.json.gz');delisted=read('research/verified-delistings.json')
    reference={r['symbol']:r for r in ref['records']}
    assert all(s['matches_original_20261014'] for s in ref['sources'])
    assert support['asof']=='2026-10-14' and original['trade_date']=='20261014'
    with duckdb.connect('snapshot/dashboard.duckdb',read_only=True) as c:
        sectors=dict(c.execute("select symbol,sector from memberships where valid_from='2026-10-14'").fetchall())
    b={'schema':1,'trade_date':'20261014','exchange':'KRX','status':'complete','finality':'UNVERIFIED','records':{},'excluded':{},'failures':{},'universe':[],
       'master_sources':ref['sources'],'source_run':original['source_run_id'],'input_mode':'saved_real_responses_replay',
       'source_runs':{'original_flow':original['source_run_id'],'quotes':quotes['run_id'],'adjusted_and_benchmarks':support['run_id'],'absence_diagnosis':absent['source_run_id']},
       'benchmarks':support['benchmarks'],'source_flow_collection_times':absent['original_timing']}
    for r in original['rows']:
        code=r['symbol'];q=quotes['records'][code];master=reference[code]
        b['universe'].append({'symbol':code,'name':r['name'],'market':r['market'],'listing_date':r['listing_date'],'halt':r['master']['halt'],'sector':sectors[code],'spac':r['master']['spac']=='Y','shares_thousands':master['listed_shares_raw']})
        b['records'][code]={'flow':dict(r['source_fields'],stck_bsop_date='20261014'),'quote':q['quote'],'observed_at':q.get('received_at_utc') or sample['observed_at_upper_bound_utc'],'adjusted_prices':support['records'][code]['rows']}
    for r in absent['rows']:
        code=r['symbol'];m=r['master']
        b['universe'].append({'symbol':code,'name':r['name'],'market':r['market'],'listing_date':m['listing_date'],'halt':m['halt']})
        if code in delisted:b['excluded'][code]=delisted[code]
        else:
            assert m['halt']=='Y'
            b['excluded'][code]={'reason':'official_master_halt','name':r['name'],'source_run':absent['source_run_id'],'source_observed_at':absent['inspected_at']}
    assert len(b['universe'])==2543 and len(b['records'])==2430 and len(b['excluded'])==113
    Path('research/daily-replay.json.gz').write_bytes(gzip.compress(json.dumps(b,ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
    print(json.dumps({'mode':b['input_mode'],'records':len(b['records']),'excluded':len(b['excluded']),'universe':len(b['universe']),'source_runs':b['source_runs']}))
