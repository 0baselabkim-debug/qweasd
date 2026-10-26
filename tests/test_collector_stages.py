"""No real API calls: full cardinality synthetic transport, offline attestations."""
import copy
import datetime as dt
import json
from pathlib import Path
import pytest
from collector_v1.core import Failure, Stop, Store, encoded, sha
from collector_v1.experiment import engine, policy, stages, stage_analysis, krx_diagnostics
from collector_v1.experiment.storage import Metrics, events
from test_full_market_experiment import universe, transport, env

PROOF={'status':'PASS','interval_seconds':1.05}


def only_kis_transport(rows, **kwargs):
    calls, original=transport(rows, **kwargs)
    def opener(req, timeout):
        assert 'krx.co.kr' not in req.full_url, 'KIS stage must never request KRX'
        return original(req, timeout)
    return calls, opener


@pytest.fixture
def bundle(env):
    rows=universe();calls,opener=only_kis_transport(rows)
    result=stages.collect_kis(env/'session',1,rows,PROOF,'SYNTHETIC_R1',opener=opener)
    return env/'session',rows,result,calls


def references(tmp_path, rows, *, conflict_field=None, empty=False, wrong_date=False, market_swap=False):
    output={}
    for market in ('KOSPI','KOSDAQ'):
        data=[]
        if not empty:
            for r in rows:
                if not r['eligible'] or r['market']!=market:continue
                row=dict(BAS_DD='20261015' if wrong_date else '20261026',ISU_CD=r['symbol'],MKT_NM=market,TDD_OPNPRC='100',TDD_HGPRC='110',TDD_LWPRC='90',TDD_CLSPRC='105',ACC_TRDVOL='10',ACC_TRDVAL='1050',MKTCAP='100000')
                if conflict_field and r['symbol']=='000720':row[conflict_field]=str(int(row[conflict_field])+1)
                data.append(row)
        store=Store(tmp_path/market)
        metadata=dict(source='KRX',task=dict(kind='krx',asof='2026-10-26',market='KOSDAQ' if market_swap else market),endpoint='/svc/apis/sto/'+('stk' if market=='KOSPI' else 'ksq')+'_bydd_trd',params={'basDd':'20261026'},collected_at='2026-10-27T03:00:00+09:00',source_run='SYNTHETIC_REFERENCE')
        entry=store.preserve(encoded({'OutBlock_1':data}),metadata)
        output[market]=(store,entry)
    return output


def test_full_kis_round_succeeds_without_krx_and_never_accepts_official_price(bundle):
    root,rows,b,calls=bundle;r=b['report']
    assert r['status']=='PROVISIONAL_COMPLETE'
    assert r['received']==r['kis_validated']==2349 and r['validated']==r['official_accepted']==0
    assert r['physical_http']==r['physical_http_total']==len(calls)==4701
    assert len(r['indices'])==2 and all(x['source_hash'] and x['observed_at'] for x in r['indices'])
    assert r['db_json_match'] and all(x['validation_status']=='PENDING_KRX' for x in r['rows'])
    assert all(x['kis_validation_status']=='PASS' and x['market_cap'] is None for x in r['rows'])
    assert stages.load_generation(root,b['generation'])==r
    def no_http(*a,**kw):pytest.fail('completed same-round evidence must be reused')
    replay=stages.collect_kis(root,1,rows,PROOF,'SYNTHETIC_RECOVERY',opener=no_http)
    assert replay['report']['physical_http_this_run']==0 and replay['report']['physical_http_total']==4701
    assert stages.load_generation(root,b['generation'])==r, 'older sealed generation must remain readable'


def test_empty_krx_attestation_preserves_complete_kis_bundle(bundle, tmp_path):
    root,rows,b,_=bundle
    files={str(p):sha(p.read_bytes()) for p in (root/'kis/R1').rglob('*') if p.is_file()}
    result=stages.validate_krx(root,b['generation'],references(tmp_path/'ref',rows,empty=True))['report']
    assert result['status']=='PENDING_OR_HOLD' and result['matched']==0 and result['target']==2349
    assert result['physical_http_this_verification']==0
    assert result['reference_status']==dict(KOSPI='PENDING_KRX',KOSDAQ='PENDING_KRX')
    assert files=={str(p):sha(p.read_bytes()) for p in (root/'kis/R1').rglob('*') if p.is_file()}
    assert stages.load_generation(root,b['generation'])['kis_validated']==2349


def test_post_crosscheck_and_mismatch_are_separate_immutable_attestations(bundle,tmp_path):
    root,rows,b,_=bundle
    wrong=stages.validate_krx(root,b['generation'],references(tmp_path/'bad',rows,conflict_field='TDD_CLSPRC'))
    bad=next(x for x in wrong['report']['rows'] if x['symbol']=='000720')
    assert bad['status']=='HOLD_PRICE_CONFLICT' and bad['differences']['close']==dict(kis=105,krx=106,delta=-1)
    good=stages.validate_krx(root,b['generation'],references(tmp_path/'good',rows))
    assert good['report']['status']=='CROSSCHECK_COMPLETE' and good['report']['matched']==2349
    assert not good['report']['flow_officially_verified'] and good['report']['provider_finality']=='UNVERIFIED'
    assert good['attestation']!=wrong['attestation'] and (root/'attestations'/(wrong['attestation']+'.json')).exists()
    assert stages.load_generation(root,b['generation'])['official_accepted']==0


@pytest.mark.parametrize('field',['TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','TDD_CLSPRC','ACC_TRDVOL','ACC_TRDVAL'])
def test_all_six_fields_still_require_exact_equality(env, tmp_path, field):
    # Full-population synthetic responses use the production qualifier and evidence hashes.
    rows=universe();calls,opener=only_kis_transport(rows)
    b=stages.collect_kis(env/'session',1,rows,PROOF,'SYNTHETIC',opener=opener)
    report=stages.validate_krx(env/'session',b['generation'],references(tmp_path/'ref',rows,conflict_field=field))['report']
    bad=next(x for x in report['rows'] if x['symbol']=='000720')
    assert bad['status']=='HOLD_PRICE_CONFLICT' and len(bad['differences'])==1
    assert report['matched']==2348


def test_wrong_date_and_wrong_request_market_never_crosscheck(bundle,tmp_path):
    root,rows,b,_=bundle
    bad=stages.validate_krx(root,b['generation'],references(tmp_path/'date',rows,wrong_date=True))['report']
    assert bad['matched']==0 and all(s.startswith('HOLD_') for s in bad['reference_status'].values())
    with pytest.raises(Failure,match='request_identity'):
        stages.validate_krx(root,b['generation'],references(tmp_path/'market',rows,market_swap=True))


def test_tampered_generation_and_missing_shared_ledger_block_before_new_http(bundle):
    root,rows,b,_=bundle
    (root/'http.jsonl').unlink()
    def no_http(*a,**kw):pytest.fail('missing ledger must block before HTTP')
    with pytest.raises(Failure,match='journal_missing'):stages.collect_kis(root,1,rows,PROOF,'SYNTHETIC',opener=no_http)
    with pytest.raises(FileNotFoundError):stages.load_generation(root,b['generation'])


def test_real_transport_is_denied_by_default_without_any_side_effect(env):
    with pytest.raises(Failure,match='separate_approval'):stages.collect_kis(env/'blocked',1,universe(),PROOF,'NONE')
    assert not (env/'blocked').exists()
    with pytest.raises(Failure,match='separate_approval'):engine.run_round(env/'blocked',1,universe(),PROOF,'NONE',kis_only=True)
    assert not (env/'blocked').exists()


def test_internal_kis_conflict_blocks_expansion_without_any_krx(env):
    rows=universe();calls,original=only_kis_transport(rows)
    def opener(req,timeout):
        response=original(req,timeout)
        if 'investor-trade' in req.full_url:
            body=json.loads(response.raw);body['output2'][0]['acml_vol']='11';response.raw=encoded(body)
        return response
    r=stages.collect_kis(env/'conflict',1,rows,PROOF,'SYNTHETIC',opener=opener)['report']
    assert r['status']=='UNRESOLVED' and r['physical_http']<=20 and r['kis_validated']==0
    assert r['errors'][0]['reason']=='flow_price_conflict'
    assert not any('FID_INPUT_ISCD=100000' in url for url in calls)


def test_transient_error_recovers_and_budget_continues_from_shared_journal(env,monkeypatch):
    rows=universe();calls,opener=only_kis_transport(rows,fail_once=True)
    r=stages.collect_kis(env/'recover',1,rows,PROOF,'SYNTHETIC',opener=opener)['report']
    assert r['status']=='PROVISIONAL_COMPLETE' and r['physical_http']==4702
    assert r['failed_http_requests']==1 and r['task_failure_count']==1 and r['http_failure_rate']>0
    assert r['recovery_intervals'][0]['recovered_at'] is not None
    session=Store(env/'recover');s=Store(env/'recover/kis/R2')
    budget=stages.SharedBudget(s,2,Metrics(),'SYNTHETIC',session)
    assert budget.total_used==4702 and budget.used==0
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[1]+dt.timedelta(minutes=5))
    budget.total_used=policy.TOTAL_LIMIT-2
    with pytest.raises(Stop):budget.check()


def test_four_rounds_are_fresh_and_provisional_comparison_never_claims_finality(env,monkeypatch,tmp_path):
    rows=universe();reports=[];bundles=[];root=env/'four'
    for n in range(1,5):
        monkeypatch.setattr(policy,'utcnow',lambda n=n:policy.STARTS[n-1]+dt.timedelta(minutes=5))
        calls,opener=only_kis_transport(rows)
        b=stages.collect_kis(root,n,rows,PROOF,'SYNTHETIC_R'+str(n),opener=opener)
        assert len(calls)==4701 and b['report']['physical_http_total']==n*4701
        reports.append(b['report']);bundles.append(b)
    for b in bundles:assert stages.load_generation(root,b['generation'])['kis_validated']==2349
    refs=references(tmp_path/'shared-reference',rows)
    checks=[stages.validate_krx(root,b['generation'],refs)['report'] for b in bundles]
    assert all(c['matched']==2349 and c['physical_http_this_verification']==0 for c in checks)
    summary=stage_analysis.summarize_four(reports,checks)
    assert len(summary['krx_results'])==4 and len({c['kis_generation'] for c in checks})==4
    assert summary['all_four_provisional_complete'] and summary['provider_finality']=='UNVERIFIED'
    assert all(c['classification']=='PROVISIONAL_COMPLETE_MATCH' and not c['official_price_acceptance'] for c in summary['comparisons'])
    revised=copy.deepcopy(reports[1]);revised['rows'][0]['foreign_net_quantity']=0
    before=copy.deepcopy(reports[0]);before['rows'][0]['foreign_net_quantity']=None
    assert stage_analysis.compare_kis(before,revised)['classification']=='VALUES_CHANGED'
    before['rows']=[];assert stage_analysis.compare_kis(before,revised)['classification']=='PARTIAL_MATCH'
    assert stage_analysis.compare_kis(reports[0],reports[2])['classification']=='NOT_COMPARABLE'


def test_diagnostic_plan_is_two_requests_without_a_network_executor():
    p=krx_diagnostics.diagnostic_plan()
    assert p['max_physical_http']==2 and p['automatic_retries']==0
    assert p['status']=='NOT_APPROVED_NOT_RUN' and p['requires_separate_explicit_execution_approval']
    assert [r['params']['basDd'] for r in p['requests']]==['20261015','20261026']
    assert all(r['url'].endswith('/stk_bydd_trd') for r in p['requests'])


def test_local_incident_audit_rejects_untrusted_archive_before_extraction(tmp_path):
    archive=tmp_path/'untrusted.zip';archive.write_bytes(b'not the original archive')
    with pytest.raises(Failure,match='zip_digest_mismatch'):
        krx_diagnostics.audit_archive(archive,tmp_path/'restore')
    assert not (tmp_path/'restore').exists()


def test_partial_or_repeated_rounds_do_not_claim_four_round_performance():
    assert not stage_analysis.summarize_four([])['all_four_provisional_complete']
    from test_full_market_experiment import report,row
    r=report(1,[row()]);r.update(phase='KIS_PROVISIONAL',status='PROVISIONAL_COMPLETE')
    assert stage_analysis.summarize_four([r]*4)['elapsed_improvement_percent'] is None


def test_global_stop_and_non_kis_endpoint_are_checked_before_http(env):
    root=Store(env/'session');root.write('session.json',{})
    budget=stages.SharedBudget(Store(root.root/'kis/R1'),1,Metrics(),'SYNTHETIC',root)
    with pytest.raises(Failure,match='non_KIS'):budget.before('KRX','/svc/apis/sto/stk_bydd_trd','test')
    (root.root/'STOP').touch()
    with pytest.raises(Stop):budget.before('KIS','/oauth2/tokenP','test')
    assert not (root.root/'http.jsonl').exists()


def test_missing_data_cannot_make_four_round_success_despite_saved_labels():
    from test_full_market_experiment import report,row
    reports=[]
    for n in range(1,5):
        r=report(n,[dict(row(),kis_validation_status='PASS')]);r.update(phase='KIS_PROVISIONAL',status='PROVISIONAL_COMPLETE');reports.append(r)
    reports[1]['rows']=[]
    summary=stage_analysis.summarize_four(reports)
    assert not summary['all_four_provisional_complete'] and summary['elapsed_improvement_percent'] is None


def test_local_incident_audit_verifies_parameters_raw_and_call_ledger(tmp_path,monkeypatch):
    import zipfile
    from collector_v1.experiment import runtime
    folder=tmp_path/'synthetic-archive';store=Store(folder)
    for minute in range(6):
        store.preserve(encoded({'OutBlock_1':[]}),dict(task=dict(kind='krx',asof='2026-10-26',round=1,market='KOSPI'),source='KRX',endpoint='/svc/apis/sto/stk_bydd_trd',params={'basDd':'20261026'},collected_at=f'2026-10-26T10:{51+minute}:00+00:00',source_run='SYNTHETIC'))
    ledger=[]
    for n in range(9):ledger.extend([dict(event='started',sequence=n+1),dict(event='finished',sequence=n+1,http=200)])
    (folder/'http.jsonl').write_bytes(b''.join(encoded(e)+b'\n' for e in ledger))
    store.write('manifest.json',dict(run_id=str(krx_diagnostics.RUN_ID),round=1,asof='2026-10-26',files=runtime.inventory(folder)))
    archive=tmp_path/'synthetic.zip'
    with zipfile.ZipFile(archive,'w') as z:
        for p in folder.rglob('*'):
            if p.is_file():z.write(p,p.relative_to(folder))
    monkeypatch.setattr(krx_diagnostics,'ZIP_SHA',sha(archive.read_bytes()))
    result=krx_diagnostics.audit_archive(archive,tmp_path/'restore')
    assert result['new_provider_http']==0 and result['physical_http']==9 and len(result['krx_empty_observations'])==6
