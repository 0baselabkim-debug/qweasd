"""Synthetic failure fixtures stay in tests; production publications use official responses only."""
import copy,hashlib
from pathlib import Path
import duckdb
import pytest
from screener.store import connect,load
from screener.daily import validate_bundle,build_candidate
from screener.scoring import evaluate

@pytest.fixture
def bundle():
    flow={'stck_bsop_date':'20261015','frgn_ntby_tr_pbmn':'3','frgn_ntby_qty':'100','frgn_shnu_vol':'200','frgn_seln_vol':'100','frgn_shnu_tr_pbmn':'5','frgn_seln_tr_pbmn':'2','stck_clpr':'10000','acml_vol':'1000','acml_tr_pbmn':'10000000'}
    return {'schema':1,'trade_date':'20261015','exchange':'KRX','status':'complete','finality':'UNVERIFIED',
        'master_sources':[{'sha256':'test only'}],'source_run':'TEST',
        'universe':[{'symbol':'000001','name':'TEST ONLY','market':'KOSPI','listing_date':'20200101','sector':'A','shares_thousands':'1000','halt':'N','spac':False}],
        'records':{'000001':{'flow':flow,'quote':{'stck_shrn_iscd':'000001','stck_prpr':'10000','acml_vol':'1000','lstn_stcn':'1000000','hts_avls':'100'},'observed_at':'2026-10-15T20:31:00+09:00','adjusted_prices':[{'stck_bsop_date':'20261015','stck_clpr':'10000','acml_vol':'1000'}]}},
        'excluded':{},'failures':{},'benchmarks':{m:{'output2':[{'stck_bsop_date':'20261015','bstp_nmix_prpr':'100'}]} for m in ['KOSPI','KOSDAQ']}}

@pytest.fixture
def baseline(tmp_path):
    path=tmp_path/'baseline.duckdb'
    with connect(path) as c:
        c.execute("insert into instruments values ('000001','TEST ONLY','KOSPI','2020-01-01',false)")
        c.execute("insert into memberships values ('000001','KOSPI','A','2026-10-14','2026-10-14','TEST')")
        c.execute("insert into flows values ('000001','2026-10-14',1000000,100,'KRX','TEST','UNVERIFIED')")
        c.execute("insert into calendar values ('KOSPI','2026-10-14')")
        c.execute("insert into prices(symbol,trade_date,close,turnover,volume,market_cap,source,exchange) values ('000001','2026-10-14',9900,9900000,1000,9900000000,'TEST','KRX')")
    return path

def test_success_and_repeat_preserve_previous_day(bundle,baseline,tmp_path):
    before=hashlib.sha256(baseline.read_bytes()).hexdigest();candidate=tmp_path/'candidate.duckdb'
    audit=build_candidate(bundle,baseline,candidate)
    assert audit['preservation_verified'] and audit['complete_sector_scores']==0 and audit['complete_stock_scores']==0
    assert hashlib.sha256(baseline.read_bytes()).hexdigest()==before
    data=load(candidate);assert len(data['flows'])==2
    assert data['flows'].set_index('trade_date').loc['2026-10-15','net_amount']==3000000
    repeated=tmp_path/'again.duckdb';build_candidate(bundle,candidate,repeated)
    assert len(load(repeated)['flows'])==2
    assert len(load(repeated)['memberships'])==2 # each observed date, no backdating/filling gaps

@pytest.mark.parametrize('fault',['missing','duplicate','wrong_date','quantity','amount','cap_unit','future_adjusted','missing_benchmark','unknown_exclusion'])
def test_bad_bundle_never_touches_published_db(bundle,baseline,tmp_path,fault):
    r=bundle['records']['000001'];before=baseline.read_bytes()
    if fault=='missing':bundle['records'].clear()
    elif fault=='duplicate':bundle['universe'].append(copy.deepcopy(bundle['universe'][0]))
    elif fault=='wrong_date':r['flow']['stck_bsop_date']='20261014'
    elif fault=='quantity':r['flow']['frgn_ntby_qty']='0'
    elif fault=='amount':r['flow']['frgn_ntby_tr_pbmn']='3000000'
    elif fault=='cap_unit':r['quote']['hts_avls']='10000000000'
    elif fault=='future_adjusted':r['adjusted_prices'].append({'stck_bsop_date':'20261016','stck_clpr':'10000'})
    elif fault=='missing_benchmark':bundle['benchmarks']['KOSDAQ']['output2']=[]
    elif fault=='unknown_exclusion':bundle['records'].clear();bundle['excluded']['000001']={'reason':'request_failed','name':'TEST ONLY'}
    with pytest.raises(ValueError):build_candidate(bundle,baseline,tmp_path/'candidate.duckdb')
    assert baseline.read_bytes()==before and not (tmp_path/'candidate.duckdb').exists()

def test_existing_success_cannot_be_overwritten(bundle,baseline,tmp_path):
    path=tmp_path/'published.duckdb';build_candidate(bundle,baseline,path);before=path.read_bytes()
    r=bundle['records']['000001']['flow'];r['frgn_ntby_tr_pbmn']='4';r['frgn_shnu_tr_pbmn']='6'
    with pytest.raises(ValueError,match='revision'):build_candidate(bundle,path,tmp_path/'bad.duckdb')
    assert path.read_bytes()==before
