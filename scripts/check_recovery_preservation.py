"""Verify every actual repair's immutable evidence and coherent publication archive."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.technical_batch import read
from screener.technical_quality import digest,daily_accepted
from screener.technical import validate
from screener.publication import coherent

def check(root):
    checks=[];responses=set()
    for path in sorted(root.glob('run-[0-9]*.json')):
        r=json.loads(path.read_text())
        for item in r['results']:
            day,code=item['asof'],item['symbol'];original=item['original_sha256']
            p=Path('technical/observations')/day/code/(original+'.json.gz')
            assert digest(read(p))==original,(str(p),'original missing or changed')
            for h in item['response_observations']:
                p=Path('technical/provider-observations')/day/code/(h+'.json.gz')
                assert digest(read(p))==h,(str(p),'response missing or changed')
                responses.add(str(p))
            if item['promoted']:
                h=item['candidate_sha256'];p=Path('technical/candidates')/day/code/(h+'.json.gz');b=read(p)
                assert digest(b)==h and daily_accepted(b) and not validate(b,day)
                assert b['finality']=='UNVERIFIED'
                assert max(x['trade_date'] for x in b['bars'])==day
            checks.append({'run_id':r['run_id'],'asof':day,'symbol':code,'original_sha256':original,'original_and_responses_preserved':True,'promoted':item['promoted']})
    assert coherent(Path('snapshot'))
    archives=[]
    for path in root.glob('publication-[0-9]*.json'):
        audit=json.loads(path.read_text());p=Path('snapshot/publication-history')/audit['baseline_sha256']
        assert coherent(p),str(p)
        archives.append(audit['baseline_sha256'])
    out={'observations':checks,'response_observations':len(responses),'current_publication_coherent':True,'previous_coherent_generations':sorted(set(archives)),'production_writes':0}
    (root/'preservation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');return out
if __name__=='__main__':print(json.dumps({'observations':len(check(Path('reports/completeness-phase2'))['observations'])}))
