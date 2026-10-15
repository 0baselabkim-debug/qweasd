"""Read-only independent real-cache audit, including preservation of initial 107."""
import collections,csv,gzip,json,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.technical_batch import read,write,CACHE
from scripts.verify_technical_expansion import verify
from screener.technical import buy_guide,validate
from screener.technical_quality import digest,daily_accepted
from screener.store import load

def main():
    data=load();asof=str(data['flows'].trade_date.max().date());flow=data['flows'].set_index(['symbol','trade_date']).net_amount
    initial=subprocess.check_output(['git','ls-tree','-r','--name-only','141dcb7','technical/market']).decode().splitlines()
    preserved={}
    for name in initial:
        old=json.loads(gzip.decompress(subprocess.check_output(['git','show','141dcb7:'+name])))
        new=read(Path(name));assert new is not None
        # Historical OHLCV and flow values remain byte-equivalent JSON payloads on this asof.
        preserved[old['symbol']]=all(digest(old[k])==digest(new[k]) for k in ['bars','flows'])
    assert all(preserved.values())
    rows=[]
    for path in sorted(CACHE.glob('*.json.gz')):
        b=read(path);code=b['symbol'];result=buy_guide(b,asof)
        arithmetic=verify(b) if b.get('indicator_ready') else None
        unchanged=all(int(flow.loc[(code,r['trade_date'])])==r['net_amount'] for r in b['flows'])
        assert unchanged
        rows.append({'symbol':code,'market':b['market'],'asof':b['asof'],'bars':len(b['bars']),
            'first':b['bars'][0]['trade_date'] if b['bars'] else None,
            'independent_arithmetic':bool(arithmetic),'flow_matches_snapshot':unchanged,
            'accepted_daily':daily_accepted(b),'data_gate_passed':not validate(b,asof),
            'guide':result['status'],'hold_reasons':' / '.join(result['reasons']),
            'official_finality':b['finality']})
    folder=Path('reports/technical-expansion')
    with (folder/'current-coverage.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else []);writer.writeheader();writer.writerows(rows)
    report={'asof':asof,'cached_symbols':len(rows),'bars':sum(r['bars'] for r in rows),
        'independent_arithmetic_passed':sum(r['independent_arithmetic'] for r in rows),
        'initial_107_raw_payloads_preserved':preserved,'flow_matches_snapshot':all(r['flow_matches_snapshot'] for r in rows),
        'accepted_daily':sum(r['accepted_daily'] for r in rows),'data_gate_passed':sum(r['data_gate_passed'] for r in rows),
        'guide_counts':dict(collections.Counter(r['guide'] for r in rows)),
        'hold_reasons':dict(collections.Counter(r['hold_reasons'] for r in rows if r['guide']=='판정 보류'))}
    write(folder/'current-audit.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='initial_107_raw_payloads_preserved'},ensure_ascii=False))

if __name__=='__main__':main()
