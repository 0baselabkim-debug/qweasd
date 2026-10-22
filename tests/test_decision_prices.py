import json
import pytest
from scripts import collect_decision_prices as c


def test_physical_budget_persists_and_retry_never_sends(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'guard',lambda:None)
    monkeypatch.setattr(c.time,'sleep',lambda _:None)
    b=c.Budget(tmp_path,limit=2)
    b.before_http('/oauth2/tokenP');b.finish('authenticated')
    b.before_http('/price');b.finish('failed')
    with pytest.raises(ValueError,match='exhausted'):b.before_http('/price')
    with pytest.raises(ValueError,match='retry_disabled'):c.Budget(tmp_path).before_http('/price',1)
    assert len(c.read(tmp_path/'requests.json')['requests'])==2
    monkeypatch.setattr(c,'TOTAL_LIMIT',2)
    with pytest.raises(ValueError,match='exhausted'):c.Budget(tmp_path).before_http('/price')


def test_preserved_success_reused_without_api(tmp_path,monkeypatch):
    obj={'asof':'2026-10-21','rows':[{'BAS_DD':'20261021'}]}
    c.cache(tmp_path,'krx:KOSPI:2026-10-21',obj)
    monkeypatch.setattr(c.urllib.request,'urlopen',lambda *a,**k:pytest.fail('duplicate API'))
    c.krx('2026-10-21','KOSPI',tmp_path,None)
    c.cache(tmp_path,'krx:KOSPI:2026-10-21',{'asof':'2026-10-21','rows':[]})
    assert len(c.cached(tmp_path,'krx:KOSPI:2026-10-21'))==2


def test_tampered_cache_blocked(tmp_path):
    c.cache(tmp_path,'key',{'rows':[]})
    p=next((tmp_path/'objects').iterdir());p.write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash_mismatch'):c.cached(tmp_path,'key')


def test_missing_calendar_not_weekday_synthesized(tmp_path):
    with pytest.raises(ValueError,match='missing'):c.index_days(tmp_path,'2026-10-21','0001')


def test_lost_response_and_unfinished_leases_consume_budget(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'guard',lambda:None)
    monkeypatch.setattr(c,'TOTAL_LIMIT',4)
    c.save(tmp_path/'requests.json',{'requests':[],
        'unrecovered_runs':[{'maximum_unrecorded_requests':2}],
        'leases':{'earlier':{'status':'reserved','limit':2}}})
    with pytest.raises(ValueError,match='exhausted'):c.Budget(tmp_path).before_http('/price')
    assert c.read(tmp_path/'requests.json')['requests']==[]


def test_manual_collector_keeps_shared_lock_and_no_cron():
    from pathlib import Path
    s=Path('.github/workflows/decision-price-evidence.yml').read_text()
    assert 'kis-validation-auth' in s and 'schedule:' not in s
    assert 'python scripts/preserve_decision_evidence.py' in s
    assert 'fetch-depth: 0' in s and 'actions/upload-artifact@v4' in s
    assert 'HEAD:main' not in s


def history_fixture():
    import datetime as dt
    from screener.decision_support.evidence import KIS_FIELDS
    days=[(dt.date(2025,1,1)+dt.timedelta(days=i)).isoformat() for i in range(200)]
    asof=days[-1];objects={};official={};db={}
    for code,options in [('000720',('0','1')),('0001',('index',))]:
        for option in options:
            end=asof.replace('-','')
            for page,ds in enumerate((days[150:],days[100:150],days[50:100]) if option=='index' else (days[100:],days[:100])):
                params={'FID_COND_MRKT_DIV_CODE':'U' if option=='index' else 'J','FID_INPUT_ISCD':code,
                        'FID_INPUT_DATE_1':'20240101','FID_INPUT_DATE_2':end,'FID_PERIOD_DIV_CODE':'D'}
                if option!='index':params['FID_ORG_ADJ_PRC']=option
                rows=[]
                for d in ds:
                    value={'open':100,'high':110,'low':90,'close':105,'volume':1000,'turnover':105000}
                    row={v:value[k] for k,v in KIS_FIELDS.items()}
                    row.update(stck_bsop_date=d.replace('-',''),mod_yn='N',flng_cls_code='00',prtt_rate='0',revl_issu_reas='',bstp_nmix_prpr='100')
                    rows.append(row)
                    official['000720',d,'KOSPI']=[{'value':value,'source_id':'krx'}]
                    db['000720',d]=(104,1000,105000) # old observation stays unchanged
                objects[f'kis:{asof}:{code}:{option}:{page}']=[(f'{code}-{option}-{page}',{
                    'params':params,'endpoint':'/uapi/domestic-stock/v1/quotations/'+('inquire-daily-indexchartprice' if option=='index' else 'inquire-daily-itemchartprice'),
                    'observed_at':'2026-01-01T01:00:00+00:00','response':{'rt_cd':'0','output1':{'stck_shrn_iscd':code},'output2':rows}})]
                end=(dt.date.fromisoformat(ds[0])-dt.timedelta(days=1)).strftime('%Y%m%d')
    return days,objects,official,db


def test_whole_verified_candidate_keeps_operating_conflict_as_audit():
    from screener.decision_support.history import candidate
    days,objects,official,db=history_fixture()
    result=candidate(objects,'000720','KOSPI',days[-1],'2020-01-01',official,db,days[-30:],{})
    assert result['error'] is None and len(result['accepted'])==121
    assert len(result['operating_version_comparison'])==121
    assert db['000720',days[-1]][0]==104


@pytest.mark.parametrize('defect',['official','adjustment','original','calendar','vintage','missing'])
def test_bad_history_never_accepted(defect):
    from screener.decision_support.history import candidate
    days,objects,official,db=history_fixture();asof=days[-1]
    a=objects[f'kis:{asof}:000720:0:0'][0][1]
    if defect=='official':official['000720',asof,'KOSPI'][0]['value']['close']=106
    if defect=='adjustment':a['response']['output2'][-1]['prtt_rate']='2'
    if defect=='original':objects[f'kis:{asof}:000720:1:0'][0][1]['response']['output2'][-1]['stck_clpr']=106
    if defect=='calendar':objects[f'kis:{asof}:0001:index:0'][0][1]['response']['output2'].pop(-2)
    if defect=='vintage':a['observed_at']='2026-01-02T01:00:00+00:00'
    if defect=='missing':a['response']['output2'].pop()
    result=candidate(objects,'000720','KOSPI',asof,'2020-01-01',official,db,days[-30:],{})
    assert result['error'] or asof not in result['accepted']


def test_unverified_event_blocks_crossing_not_verified_post_event_suffix():
    from screener.decision_support.history import candidate
    from screener.decision_support.indicators import technical
    days,objects,official,db=history_fixture();asof=days[-1]
    objects[f'kis:{asof}:000720:0:0'][0][1]['response']['output2'][-31]['flng_cls_code']='05'
    result=candidate(objects,'000720','KOSPI',asof,'2020-01-01',official,db,days[-30:],{})
    assert len(result['accepted'])==30
    values=technical(result['days'],result['accepted'])
    assert values['sma_20']['value']==105
    assert values['sma_60']['value'] is None


@pytest.mark.parametrize('defect',[None,'overlap','new_reference','short_overlap','new_adjustment'])
def test_incremental_history_links_only_proven_whole_candles(defect):
    import copy,datetime as dt
    from screener.decision_support.history import continuation
    days,objects,official,db=history_fixture();asof=days[-1]
    newday=(dt.date.fromisoformat(asof)+dt.timedelta(days=1)).isoformat()
    old=objects[f'kis:{asof}:000720:0:0'][0][1]['response']['output2']
    rows=copy.deepcopy(old[-30:]);new=copy.deepcopy(rows[-1]);new['stck_bsop_date']=newday.replace('-','');rows.append(new)
    official['000720',newday,'KOSPI']=copy.deepcopy(official['000720',asof,'KOSPI'])
    if defect=='overlap':rows[-2]['stck_clpr']=106
    if defect=='new_reference':official['000720',newday,'KOSPI'][0]['value']['close']=106
    if defect=='short_overlap':rows=rows[-10:]
    if defect=='new_adjustment':rows[-1]['flng_cls_code']='05'
    r=continuation(objects,'000720','KOSPI',newday,'2020-01-01',official,db,days[-30:]+[newday],{},
        {'adjusted_prices':rows,'observed_at':'2026-01-02T00:00:00+00:00'},'checkpoint')
    if defect:assert r.get('error') or newday not in r['accepted']
    else:
        assert len(r['accepted'])==122 and r['link_proof']['overlap_days']==30
        assert db['000720',asof][0]==104
