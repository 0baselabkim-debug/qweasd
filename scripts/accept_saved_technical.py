"""One-time migration using original public KIS response checkpoints, not invented rows."""
import argparse,datetime as dt,gzip,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.technical_batch import CACHE,read,write,krx_reference,refresh_summary
from scripts.technical_acceptance import accept_saved_responses
from screener.technical_quality import daily_accepted,digest

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--history',type=Path,required=True)
    parser.add_argument('--history-revision',required=True);args=parser.parse_args()
    history=read(args.history);daily_path=Path('research/daily-replay.json.gz');daily=read(daily_path)
    if history['exchange']!='KRX' or daily['exchange']!='KRX' or daily['status']!='complete':raise ValueError('Unvalidated source scope')
    if history['analysis_date']!=daily['trade_date']:raise ValueError('Source date mismatch')
    day=dt.datetime.strptime(daily['trade_date'],'%Y%m%d').date().isoformat();refs,status=krx_reference(day)
    sources=[{'kind':'original_history_api_responses','sha256':hashlib.sha256(args.history.read_bytes()).hexdigest(),
              'url':f'https://github.com/0baselabkim-debug/qweasd/blob/{args.history_revision}/staging/kis_history_20261014.json.gz',
              'run_id':history['run_id']},
             {'kind':'original_daily_api_responses','sha256':hashlib.sha256(daily_path.read_bytes()).hexdigest(),
              'url':'https://github.com/0baselabkim-debug/qweasd/blob/141dcb7/research/daily-replay.json.gz',
              'run_id':daily['source_run']}]
    report={'asof':day,'flow_evidence_kind':'saved_api_response_replay','new_kis_calls':0,'sources':sources,
            'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'krx_reference_status':status,'symbols':{}}
    for path in sorted(CACHE.glob('*.json.gz')):
        b=read(path)
        if b['asof']!=day:continue
        code=b['symbol'];record=history['records'].get(code,{});latest=daily['records'].get(code,{})
        metric=record.get('metric',record.get('history_metric',{}))
        if (record.get('exchange',history['exchange'])!='KRX' or metric.get('http')!=200
            or any(record.get(k) not in (None,0) for k in ['invalid_rows','duplicate_dates'])):
            report['symbols'][code]={'accepted':False,'reason':'original response quality or scope unverified'};continue
        rows=record.get('rows',[])+[latest.get('flow',{})]
        before={k:digest(b[k]) for k in ['bars','flows']}
        b=accept_saved_responses(b,refs,rows,sources)
        b['daily_acceptance']['original_latest_flow_observed_at']=latest.get('observed_at')
        assert all(digest(b[k])==v for k,v in before.items())
        write(path,b)
        report['symbols'][code]={'accepted':daily_accepted(b),'issues':b['validation'],'bars_and_flows_preserved':True}
    report['accepted_count']=sum(v['accepted'] for v in report['symbols'].values())
    write(Path('reports/technical-expansion/saved-response-acceptance.json'),report)
    refresh_summary(day)
    print({'accepted':report['accepted_count'],'evaluated':len(report['symbols']),'new_kis_calls':0})

if __name__=='__main__':main()
