"""Artifact trust anchors and bounded native inspection never invoke market APIs."""
import json
import zipfile
from pathlib import Path
import pytest
from screener.decision_evidence.collector_artifact import extract,ASOF,RUN,GENERATION
from screener.decision_support.evidence import sha,encoded


def archive(tmp_path, extra=None):
    value=dict(asof=ASOF,run_id=RUN,generation_id=GENERATION,files={'report.json':sha(b'original')})
    raw=encoded(value);p=tmp_path/'source.zip'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('manifest.json',raw);z.writestr('report.json',b'original')
        if extra:z.writestr(*extra)
    return p,sha(raw)


def test_zip_anchor_manifest_and_read_only_original(tmp_path):
    p,m=archive(tmp_path);original=p.read_bytes();r=extract(p,tmp_path/'new',sha(original),m)
    assert r['files_verified']==2 and (tmp_path/'new/report.json').read_bytes()==b'original' and p.read_bytes()==original
    with pytest.raises(ValueError,match='must_be_new'):extract(p,tmp_path/'new',sha(original),m)
    with pytest.raises(ValueError,match='ZIP_digest'):extract(p,tmp_path/'other')
    with pytest.raises(ValueError,match='manifest_anchor'):extract(p,tmp_path/'other',sha(original),'0'*64)


@pytest.mark.parametrize('path',['../escape','/absolute','dir/../../escape','dir\\escape'])
def test_unsafe_archive_never_extracts(tmp_path,path):
    p,m=archive(tmp_path,(path,b'payload'))
    with pytest.raises(ValueError,match='unsafe_member'):extract(p,tmp_path/'new',sha(p.read_bytes()),m)
    assert not (tmp_path/'new').exists()


def test_unmanifested_file_is_not_approved(tmp_path):
    p,m=archive(tmp_path,('extra.json',b'unknown'))
    with pytest.raises(ValueError,match='population_mismatch'):extract(p,tmp_path/'new',sha(p.read_bytes()),m)


def test_new_workflow_is_finite_separate_and_has_no_market_credential():
    text=Path('.github/workflows/decision-v2-source-20261029.yml').read_text()
    assert 'schedule:' not in text and 'cron:' not in text and 'secrets.' not in text
    assert 'branches: [feat/decision-evidence-v2]' in text and 'paths: [.github/trials/decision-v2-source-20261029.json]' in text
    assert 'kis-validation-auth' not in text and 'decision_v2_native.py' in text


def history_evidence(kind='price',code='000720'):
    import datetime as dt
    from collector_v1.providers import task,Q
    from screener.decision_evidence.collector_artifact import ASOF,GENERATION
    market='KOSPI';p={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code}
    if kind=='flow':p.update(FID_INPUT_DATE_1='20261029',FID_ORG_ADJ_PRC='',FID_ETC_CLS_CODE='')
    else:p.update(FID_INPUT_DATE_1='20260830',FID_INPUT_DATE_2='20261029',FID_PERIOD_DIV_CODE='D',FID_ORG_ADJ_PRC='1')
    return dict(task=task(kind,ASOF,round=1,symbol=code,market=market,generation_id=GENERATION),params=p,
        endpoint=Q+('inquire-daily-itemchartprice' if kind=='price' else 'investor-trade-by-stock-daily'))


def test_history_binds_original_price_scope_and_no_future_or_duplicate():
    from screener.decision_evidence.collector_source import saved_rows
    e=history_evidence();obj=dict(rt_cd='0',output1={'stck_shrn_iscd':'000720'},output2=[{'stck_bsop_date':'20261029'}])
    assert list(saved_rows(obj,e,'price','000720','KOSPI'))==['2026-10-29']
    e['params']['FID_COND_MRKT_DIV_CODE']='UN'
    with pytest.raises(ValueError,match='binding'):saved_rows(obj,e,'price','000720','KOSPI')
    e=history_evidence();obj['output2']*=2
    with pytest.raises(ValueError,match='duplicate'):saved_rows(obj,e,'price','000720','KOSPI')
    obj['output2']=[{'stck_bsop_date':'20261030'}]
    with pytest.raises(ValueError,match='future'):saved_rows(obj,e,'price','000720','KOSPI')


def flow():
    return dict(frgn_ntby_qty='2',frgn_shnu_vol='4',frgn_seln_vol='2',frgn_ntby_tr_pbmn='1',
        frgn_shnu_tr_pbmn='3',frgn_seln_tr_pbmn='2',stck_clpr='1000',acml_vol='10',acml_tr_pbmn='10000',
        orgn_ntby_qty='-1',orgn_shnu_vol='0',orgn_seln_vol='1',orgn_ntby_tr_pbmn='0',orgn_shnu_tr_pbmn='0',orgn_seln_tr_pbmn='0')


def test_flow_million_unit_identity_institution_unit_never_borrowed():
    from screener.decision_evidence.collector_source import flow_observation
    r=flow();n=flow_observation(r,r)
    assert n['net_amount_krw']==1000000 and n['net_quantity_shares']==2
    assert n['institution_net_quantity_shares']==-1 and n['institution_net_amount_krw'] is None
    r['frgn_shnu_vol']='5'
    assert flow_observation(r,r)['net_amount_krw'] is None


def test_amount_identity_uses_existing_one_provider_million_rule_not_price_tolerance():
    from screener.decision_evidence.collector_source import flow_observation
    r=flow();r['frgn_shnu_tr_pbmn']='4'
    assert flow_observation(r,r)['net_amount_krw']==1000000
    r['frgn_shnu_tr_pbmn']='5'
    assert flow_observation(r,r)['net_amount_krw'] is None
    r=flow();p={**r,'acml_vol':'11'}
    assert flow_observation(r,p)['net_amount_krw'] is None


def test_preserved_official_reference_hash_is_checked_before_use(tmp_path):
    from screener.decision_evidence.collector_source import references
    from screener.decision_support.evidence import timestamp
    p=tmp_path/'ref.json';p.write_text('{}')
    m=dict(references={'r':dict(file='ref.json',sha256='0'*64,url='https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20261023',observed_at='2026-10-24T00:00:00Z')})
    (tmp_path/'source-manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='hash_mismatch'):references(tmp_path,timestamp('2026-10-30T00:00:00Z'))


def test_new_loader_is_separate_from_published_input_and_never_silently_falls_back(tmp_path):
    from screener.decision_evidence.build import build
    p=tmp_path/'empty';p.mkdir()
    with pytest.raises(FileNotFoundError):build(p,tmp_path/'output','2026-10-30T00:00:00Z',source_kind='collector-artifact')
    assert not (tmp_path/'output').exists()


@pytest.mark.parametrize('case,classification,confirmed',[
 ('later_match','LATER_ORIGINAL_SIX_FIELDS_MATCH_SAVED_KRX',False),
 ('paired_later_match','LATER_ORIGINAL_SIX_FIELDS_MATCH_SAVED_KRX',True),
 ('unchanged','SIX_FIELD_CONFLICT_PERSISTS',False),
 ('other_revision','LATER_PRICE_CHANGED_CONFLICT_UNRESOLVED',False)])
def test_price_cause_requires_old_original_adjusted_witness(tmp_path,monkeypatch,case,classification,confirmed):
    import screener.decision_evidence.price_diagnosis as d
    from screener.decision_evidence.metrics import FIELDS
    from screener.decision_support.evidence import KIS_FIELDS
    old=dict(open=100,high=110,low=90,close=105,volume=10,turnover=1000)
    official={**old,'close':106};after=official if case in ('later_match','paired_later_match') else old if case=='unchanged' else {**old,'close':107}
    stock=dict(symbol='000720',market='KOSPI',asof='2026-10-23',price_series={'columns':['trade_date',*FIELDS],
        'rows':[['2026-10-23',*[old[k] for k in FIELDS]]]})
    source=dict(index={'asof':'2026-10-23','dataset_id':'old'},rows=[stock],official={('000720','2026-10-23','KOSPI'):[{'value':official,'source_id':'krx'}]})
    monkeypatch.setattr(d,'load_published',lambda *a:source)
    pair={'000720':{o:[dict(value=old,source_id=o,observed_at='2026-10-24T01:00:00Z')] for o in ('0','1')}} if case=='paired_later_match' else {}
    monkeypatch.setattr(d,'paired_old_basis',lambda *a:pair)
    class Store:
        def __init__(self,*a):pass
        def raw(self,e):return b'{}'
    monkeypatch.setattr(d,'Store',Store)
    monkeypatch.setattr(d,'replay',lambda *a,**kw:{'key':dict(source_hash='a'*64,collected_at='2026-10-30T00:00:00Z')})
    monkeypatch.setattr(d,'identity',lambda t:'key')
    monkeypatch.setattr(d,'read',lambda p:[dict(symbol='000720',listing_date='2020-01-01')] if p.name=='universe.json' else {'rows':[{'symbol':'000720'}]})
    raw={v:after[k] for k,v in KIS_FIELDS.items()};raw.update(flng_cls_code='00',prtt_rate='0',revl_issu_reas='',mod_yn='N')
    monkeypatch.setattr(d,'saved_rows',lambda *args:{'2026-10-23':raw})
    summary,rows=d.diagnose(tmp_path,tmp_path,'2026-10-30T01:00:00Z')
    assert rows[0]['classification']==classification
    assert (summary['confirmed_price_vintage_revision']==1)==confirmed
    assert rows[0]['price_approved_for_20261029'] is False and not summary['original_records_changed']
