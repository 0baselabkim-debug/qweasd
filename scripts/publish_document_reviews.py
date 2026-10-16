"""Append source-review evidence only; preserve failed financial jobs and all versions."""
import copy,datetime as dt,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.corporate import load_state,save_state,build_database
from scripts.publish_corporate import validate

def add_reviews(state,report):
    new=copy.deepcopy(state);added=[]
    for kind,items in [('correction',report['corrections']),('attachment',report['attachments'])]:
        for item in items:
            receipt=item['receipt']
            if item['filing']!=state['filings'][receipt]:raise ValueError('filing_identity_changed')
            value={'kind':kind,'receipt':receipt,'parent_receipt':item['parent_receipt'],'status':item['status'],'financial_acceptance':False}
            if kind=='correction':
                value['comparisons']=item['comparisons'];value['source_hashes']={k:item[k].get('raw_sha256') for k in ['corrected','original']}
                value['correction_text']=[d['correction_text'] for d in item['corrected'].get('documents',[]) if d['identity_verified']]
            else:
                e=item['evidence'];value.update(source_status=e['status'],source_sha256=e.get('raw_sha256'),
                    documents=[{k:d[k] for k in ['filename','document_title','identity_verified','xml_sha256','opinion_excerpts']} for d in e.get('documents',[])])
            sha=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if sha in new.setdefault('document_source_reviews',{}):continue
            new['document_source_reviews'][sha]=value
            new['events'].append({'kind':'official_document_source_review','receipt':receipt,'evidence_sha256':sha,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'financial_acceptance':False})
            added.append(receipt)
    validate(state,new)
    assert new['jobs']==state['jobs'] and new['financials']==state['financials'] and new['reviews']==state['reviews']
    return new,added

if __name__=='__main__':
    p=Path('/tmp/stabilization-corporate');p.mkdir(exist_ok=True)
    r=json.loads(Path('reports/operations-stabilization/correction-review.json').read_text());state=load_state();new,added=add_reviews(state,r)
    save_state(new,p/'state.json.gz');build_database(new,p/'corporate.duckdb')
    Path('reports/operations-stabilization/document-review-publication.json').write_text(json.dumps({'added':added,'financial_versions_changed':False,'jobs_preserved':True},indent=2))
    print(json.dumps({'added':len(added),'candidate_dir':str(p)}))
