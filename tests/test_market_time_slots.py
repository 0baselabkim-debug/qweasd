"""Offline, synthetic four-slot lifecycle; never calls financial or GitHub APIs."""
import copy
import datetime as dt
import io
import json
from pathlib import Path
import zipfile
import pytest
from collector_v1 import kis_manual as manual
from collector_v1.core import Failure, Stop, Store, encoded, sha
from collector_v1.experiment import engine, runtime
from collector_v1.experiment.storage import events, Metrics
from collector_v1.time_slots import policy, runner
from collector_v1.time_slots.compare import (normalize, market_view, differences, compare_pair,
    stabilization, build_observations, markdown, numeric_difference)
from test_full_market_experiment import universe, transport


@pytest.fixture
def synthetic(monkeypatch, tmp_path):
    monkeypatch.setattr('collector_v1.experiment.storage.time.sleep', lambda _: None)
    monkeypatch.setattr(policy, 'utcnow', lambda: policy.STARTS[0]+dt.timedelta(seconds=1))
    monkeypatch.setattr(manual.ManualBudget, 'remaining_seconds', lambda self: 10000)
    for key in ('KIS_APP_KEY','KIS_APP_SECRET'):
        monkeypatch.setenv(key, 'SYNTHETIC_'+key)
    monkeypatch.setenv('GITHUB_RUN_ID','12345');monkeypatch.setenv('GITHUB_SHA','SYNTHETIC_COMMIT')
    monkeypatch.setenv('GITHUB_STEP_SUMMARY',str(tmp_path/'summary.md'))
    monkeypatch.setattr(runtime,'api',lambda *a,**k:{'jobs':[]})
    rows=universe()
    for row in rows:
        if row['symbol'] not in manual.TARGETS:
            row.update(eligible=False,exclusion_reasons=['halted'])
    return tmp_path,rows


def next_day_transport(rows, **kwargs):
    calls, original=transport(rows, **kwargs)
    class Response:
        status=200
        def __init__(self,raw):self.raw=raw
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return self.raw
    def opener(req,timeout):
        response=original(req,timeout)
        raw=response.raw.replace(b'20261026',b'20261027')
        return Response(raw)
    return calls,opener


def sample_view(value='1', extra=None):
    return dict(symbol='000720',market='KOSPI',asof=policy.ASOF,name='SYNTHETIC',
        all_market_fields={'price/close':normalize(value),**(extra or {})},
        target_day_fields={},core={k:normalize(value) for k in ('open','high','low','close','volume','turnover','foreign_net_quantity','foreign_net_amount','institution_net_quantity','institution_amount_raw')},
        metadata={'flow':{'observed_at':'SYNTHETIC_1','source_hash':'SYNTHETIC_HASH'}})


def generation_report(n=1, value='1', extra=None, status='PASS', target=None):
    targets=target or ['000720']
    return dict(report=dict(round=n,generation_id=policy.generation(n),status=status,asof=policy.ASOF,
        target_symbols=targets,population_sha256='SYNTHETIC_SAME_POPULATION'),
        observations={'000720':sample_view(value,extra)})


def test_fixed_dates_midnight_and_four_unique_generations():
    config=policy.configuration('SYNTHETIC_REQUEST');policy.validate_configuration(config)
    assert len({r['generation_id'] for r in config['rounds']})==4
    assert [d.date().isoformat() for d in policy.STARTS]==['2026-10-27']*2+['2026-10-28']*2
    for n in range(1,5):
        c=policy.control(n);assert c['asof']=='2026-10-27'
        policy.validate_control(c,current=policy.STARTS[n-1])
        with pytest.raises(Failure):policy.validate_control(c,current=policy.STARTS[n-1]-dt.timedelta(microseconds=1))
        with pytest.raises(Failure):policy.validate_control(c,current=policy.STARTS[n-1]+dt.timedelta(minutes=170))
    with pytest.raises(Failure):policy.generation(5)


@pytest.mark.parametrize('field,value',[('asof','2026-10-28'),('max_total_http_per_generation',5601),('interval_seconds',1.0),('approval','UNAPPROVED')])
def test_configuration_cannot_change_approved_scope(field,value):
    c=policy.configuration('SYNTHETIC');c[field]=value
    with pytest.raises(Failure):policy.validate_configuration(c)


def test_wait_does_not_backdate_and_has_finite_job_horizon():
    clock=[policy.STARTS[0]-dt.timedelta(seconds=65)];sleeps=[]
    def sleep(n):sleeps.append(n);clock[0]+=dt.timedelta(seconds=n)
    runner.wait_until(policy.STARTS[0],clock=lambda:clock[0],sleeper=sleep)
    assert clock[0]==policy.STARTS[0] and max(sleeps)<=30
    late=policy.STARTS[0]+dt.timedelta(minutes=3)
    runner.wait_until(policy.STARTS[0],clock=lambda:late,sleeper=lambda _:pytest.fail('late start must not sleep/backdate'))
    with pytest.raises(Failure):runner.wait_until(policy.STARTS[0],clock=lambda:policy.STARTS[0]-dt.timedelta(hours=5))


def test_dynamic_population_not_old_2349_and_duplicates_block(synthetic):
    _,rows=synthetic
    assert len(policy.eligible(rows))==3
    receipt=dict(asof=policy.ASOF,status='PASS',population_sha256=sha(encoded(rows)),master_rows=len(rows),eligible=3)
    assert len(policy.verify_population(rows,receipt))==3
    receipt['eligible']=2349
    with pytest.raises(Failure):policy.verify_population(rows,receipt)
    with pytest.raises(Failure,match='duplicate'):policy.eligible(rows+[rows[0]])
    large=universe()
    for row in large:row.update(eligible=True,exclusion_reasons=[])
    with pytest.raises(Failure,match='budget'):policy.eligible(large)


def test_four_generations_make_fresh_requests_and_keep_same_trade_date(synthetic,monkeypatch):
    root,rows=synthetic; hashes=[];all_calls=[];all_observed=[]
    for n in range(1,5):
        monkeypatch.setattr(policy,'utcnow',lambda n=n:policy.STARTS[n-1]+dt.timedelta(seconds=1))
        calls,opener=next_day_transport(rows)
        folder=root/policy.generation(n)
        report=manual.run(folder/'collection',policy.control(n),rows,'SYNTHETIC',opener=opener,policy=policy)
        report.update(round=n,generation_id=policy.generation(n))
        assert report['status']=='PASS' and report['physical_http']==9 and len(calls)==9
        assert report['asof']==policy.ASOF and report['db_json_match']
        assert all('20261028' not in u and '20261026' not in u for u in calls)
        assert all(e.get('generation_id')==policy.generation(n) for e in events(folder/'collection'/'http.jsonl') if e['event']=='started')
        Store(folder).write('universe.json',rows)
        Store(folder).write('master-receipt.json',dict(status='PASS',asof=policy.ASOF,population_sha256=sha(encoded(rows)),master_rows=len(rows),eligible=3))
        runner.verify_generation(folder,report)
        views=build_observations(folder/'collection',report,rows)
        assert len(views)==3 and views['000720']['name']=='현대건설'
        all_observed.append(views);all_calls+=calls
        tasks=events(folder/'collection'/'tasks.jsonl');hashes.append(engine.identity(tasks[0]['task']))
    assert len(set(hashes))==4 and len(all_calls)==36
    # Identical bytes can recur in fresh HTTP; request/observation generation remains distinct.
    assert all_observed[0]['000720']['generation_id']!=all_observed[1]['000720']['generation_id']


def test_cross_generation_cache_rejected_before_any_http(synthetic,monkeypatch):
    root,rows=synthetic;calls,opener=next_day_transport(rows)
    manual.run(root/'one',policy.control(1),rows,'SYNTHETIC',opener=opener,policy=policy)
    def forbidden(*a,**k):pytest.fail('cross-generation cache cannot issue HTTP')
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[1]+dt.timedelta(seconds=1))
    with pytest.raises(Failure,match='binding'):
        manual.run(root/'one',policy.control(2),rows,'SYNTHETIC',opener=forbidden,policy=policy)
    with pytest.raises(Failure,match='cross_round'):
        engine.replay(Store(root/'one'),1,asof=policy.ASOF,generation_id=policy.generation(2))


def test_same_generation_resume_reuses_success_and_budget_persists(synthetic):
    root,rows=synthetic;calls,opener=next_day_transport(rows,fail_once=True)
    r=manual.run(root/'same',policy.control(1),rows,'SYNTHETIC',opener=opener,policy=policy)
    assert r['status']=='PASS' and r['physical_http']==10
    def forbidden(*a,**k):pytest.fail('recovery cannot resend already successful HTTP')
    second=manual.run(root/'same',policy.control(1),rows,'SYNTHETIC',opener=forbidden,policy=policy)
    assert second['physical_http']==10 and second['physical_http_this_run']==0
    budget=manual.ManualBudget(Store(root/'same'),policy.control(1),Metrics(),'SYNTHETIC',validator=policy.validate_control)
    budget.used=5600
    with pytest.raises(Stop):budget.before('KIS','/oauth2/tokenP','authentication')


def test_before_slot_or_expired_is_zero_http(synthetic,monkeypatch):
    root,rows=synthetic
    def forbidden(*a,**k):pytest.fail('closed window must have zero HTTP')
    for when in (policy.STARTS[0]-dt.timedelta(seconds=1),policy.STARTS[0]+dt.timedelta(minutes=170)):
        monkeypatch.setattr(policy,'utcnow',lambda:when)
        with pytest.raises(Failure,match='window'):manual.run(root/'forbidden',policy.control(1),rows,'SYNTHETIC',opener=forbidden,policy=policy)


def test_exact_numeric_format_null_absence_and_large_values():
    assert normalize('001.000')==normalize(1)==normalize('1.0')
    assert normalize('1,000.00')==normalize(1000)
    assert normalize('123456789012345678901234567890.123456789')!=normalize('123456789012345678901234567890.123456788')
    assert normalize('000720','stck_shrn_iscd')!=normalize('720','stck_shrn_iscd')
    assert normalize(None)!=normalize('')!=normalize(0)
    assert differences({'x':normalize(None)}, {})['x']['after']=={'type':'missing'}
    d=numeric_difference(normalize('3'),normalize('4'))
    assert d['delta']=={'numerator':'1','denominator':'1'} and d['percent']=={'numerator':'100','denominator':'3'}
    assert numeric_difference(normalize(0),normalize(1))['percent'] is None


def test_all_raw_fields_preserved_and_history_not_confused_with_target_day():
    raw=encoded(dict(rt_cd='0',msg1='protocol',output1={'stck_prpr':'100','unknown_new_field':'7.00'},output2=[
        {'stck_bsop_date':'20261027','stck_clpr':'100','new_flow_field':None},
        {'stck_bsop_date':'20261026','stck_clpr':'90'}]))
    full,target=market_view(raw,'price',policy.ASOF)
    assert 'price/output1/unknown_new_field' in full and 'price/target/new_flow_field' in target
    assert not any('20261026' in k or 'msg1' in k for k in full)
    assert full['price/output1/stck_prpr']==normalize(100)


def test_complete_core_partial_widespread_classification_and_metadata_exclusion():
    a=generation_report();b=generation_report(2,value='1.000')
    b['observations']['000720']['metadata']['flow']['observed_at']='SYNTHETIC_2'
    p=compare_pair(a,b)
    assert p['classification']=='A_COMPLETE_IDENTICAL' and p['identical_ratio']==1
    assert p['metadata_comparison'][0]['before']!=p['metadata_comparison'][0]['after']
    b=generation_report(2,extra={'other_market_field':normalize(2)})
    assert compare_pair(a,b)['classification']=='B_CORE_IDENTICAL'
    b=generation_report(2,value='2')
    changed=compare_pair(a,b)
    assert changed['classification']=='D_WIDESPREAD_CHANGE' and changed['category_changed_counts']['foreign']==1
    assert changed['changed_field_counts']['price/close']==1 and changed['largest_absolute_changes']
    # One changed symbol out of two is not a strict majority.
    for g in (a,b):
        g['report']['target_symbols'].append('064400');g['observations']['064400']=sample_view()
    assert compare_pair(a,b)['classification']=='C_PARTIAL_CHANGE'


def test_missing_failed_or_changed_population_never_complete_match():
    a=generation_report();b=generation_report(2,status='FAIL')
    assert not compare_pair(a,b)['complete_population_comparable']
    b=generation_report(2,target=['000720','064400'])
    p=compare_pair(a,b)
    assert p['classification']=='NOT_COMPARABLE_COMPLETE_POPULATION' and p['right_only_population']==['064400']
    b=generation_report(2);b['report']['population_sha256']='CHANGED'
    assert not compare_pair(a,b)['complete_population_comparable']


def test_no_finality_claim_and_later_change_revokes_earlier_equality():
    gs=[generation_report(n) for n in range(1,5)]
    pairs=[compare_pair(gs[a],gs[b]) for a,b in ((0,1),(1,2),(2,3),(0,3))]
    result=stabilization(pairs)
    assert result['recommendation']=='18시 이후 시작 가능' and result['official_finality']=='UNVERIFIED'
    gs[-1]=generation_report(4,value=2)
    pairs=[compare_pair(gs[a],gs[b]) for a,b in ((0,1),(1,2),(2,3),(0,3))]
    result=stabilization(pairs)
    assert result['recommendation']=='현재 실험만으로 판단 불가'
    assert result['categories']['core']['last_changed_pair']=='00시→03시'
    assert 'PENDING_KRX' in markdown([g['report'] for g in gs],pairs,result)


def test_seal_and_zip_manifest_tampering_rejected(synthetic):
    root,_=synthetic;s=Store(root/'sealed');s.write('partial.json',{'status':'FAIL'})
    runner.seal(s.root,'SYNTHETIC')
    assert (s.root/'UPLOAD_ALLOWED').exists()
    assert runtime.verify(s.root)['kind']=='SYNTHETIC'
    (s.root/'partial.json').write_text('{}')
    with pytest.raises(Failure,match='hash'):runtime.verify(s.root)
    archive=root/'artifact.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('partial.json','{}')
    with pytest.raises(Failure,match='digest'):runtime.safe_extract(archive,root/'restore','0'*64)


def test_credentials_block_artifact_and_no_upload_marker(synthetic):
    root,_=synthetic;s=Store(root/'secret');(s.root/'bad.txt').write_text('SYNTHETIC_KIS_APP_KEY')
    with pytest.raises(Failure,match='credential'):runner.seal(s.root,'SYNTHETIC')
    assert not (s.root/'UPLOAD_ALLOWED').exists() and not (s.root/'manifest.json').exists()


def test_workflow_is_finite_sequential_and_shares_auth_lock():
    text=Path('.github/workflows/market-20261027.yml').read_text()
    assert 'schedule:' not in text and 'workflow_dispatch:' not in text
    assert 'KRX_API_KEY' not in text and 'actions: write' not in text
    assert text.count('runner collect --round ')==4 and text.count('group: kis-validation-auth')==5
    for n in range(2,5):assert f'needs: [authorize, master, round{n-1}]' in text
    assert 'needs: [authorize, master, round1, round2, round3, round4]' in text
    assert 'runner compare' in text and 'retention-days: 30' in text
    assert '.github/experiments/market-20261027.json' in text


def test_context_rejects_reruns_before_api(monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY',policy.REPO);monkeypatch.setenv('GITHUB_REF',policy.BRANCH)
    monkeypatch.setenv('GITHUB_EVENT_NAME','push');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2')
    with pytest.raises(Failure,match='context'):runner.context()


def test_authorization_rejects_second_arm_without_calls(monkeypatch):
    c=policy.configuration('SYNTHETIC')
    monkeypatch.setattr(runner,'context',lambda:c)
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[0]-dt.timedelta(hours=1))
    monkeypatch.setenv('GITHUB_RUN_ID','12345')
    def api(path,*a,**k):
        if path.endswith('/12345'):return {'path':'.github/workflows/'+policy.WORKFLOW,'workflow_id':1}
        return {'workflow_runs':[{'id':12345},{'id':12344}]}
    monkeypatch.setattr(runtime,'api',api)
    with pytest.raises(Failure,match='already_armed'):runner.authorize()


def test_new_date_full_population_uses_changed_eligibility_and_full_DB(synthetic):
    root,_=synthetic;rows=universe()
    rows[3].update(eligible=False,exclusion_reasons=['halted'])
    calls,opener=next_day_transport(rows)
    r=manual.run(root/'dynamic-full'/'collection',policy.control(1),rows,'SYNTHETIC_FULL',opener=opener,policy=policy)
    r.update(round=1,generation_id=policy.generation(1))
    assert r['status']=='PASS' and r['target']==r['kis_validated']==2348
    assert r['physical_http']==len(calls)==4699 and r['db_json_match']
    Store(root/'dynamic-full').write('universe.json',rows)
    Store(root/'dynamic-full').write('master-receipt.json',dict(status='PASS',asof=policy.ASOF,population_sha256=sha(encoded(rows)),master_rows=len(rows),eligible=2348))
    runner.verify_generation(root/'dynamic-full',r)


def test_fresh_master_and_raw_restoration_are_oct27_not_oct26(synthetic,monkeypatch):
    from test_collector_v1 import master_bytes
    from collector_v1.providers import Providers,LAYOUTS
    root,_=synthetic
    monkeypatch.setattr(runner,'ROOT',root/'experiment')
    monkeypatch.setattr(runner,'context',lambda:policy.configuration('SYNTHETIC'))
    monkeypatch.setattr(runner,'publish_file',lambda *a,**k:'SYNTHETIC_SHA')
    monkeypatch.setattr(runner,'wait_until',lambda *a,**k:None)
    monkeypatch.setattr('collector_v1.providers.now',lambda:'2026-10-27T08:30:00+00:00')
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[0]-dt.timedelta(minutes=30))
    def master_raw(market):
        n=600 if market=='KOSPI' else 1400
        codes=[f'{i:06}' for i in range(100000 if market=='KOSPI' else 400000,(100000 if market=='KOSPI' else 400000)+n)]
        codes += [code for code,(board,name) in manual.TARGETS.items() if board==market]
        lines=[]
        for code in codes:
            with zipfile.ZipFile(io.BytesIO(master_bytes(market,code,'N'))) as z:
                member=z.namelist()[0];line=z.read(member)
            if code in manual.TARGETS:line=line.replace(b'SYNTHETIC TEST ONLY',manual.TARGETS[code][1].encode('cp949'))
            lines.append(line)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr(member,b'\n'.join(lines))
        return out.getvalue()
    raw={m:master_raw(m) for m in ('KOSPI','KOSDAQ')}
    calls=[]
    class Response:
        status=200
        def __init__(self,raw):self.raw=raw
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return self.raw
    def open_(req,timeout):
        calls.append(req.full_url)
        if req.full_url.endswith('tokenP'):return Response(encoded({'access_token':'SYNTHETIC_ACCESS_ONLY'}))
        if 'chk-holiday' in req.full_url:return Response(encoded({'rt_cd':'0','output':[{'bass_dt':'20261027','opnd_yn':'Y'}]}))
        return Response(next(raw[m] for m in raw if req.full_url==LAYOUTS[m]['url']))
    original=Providers.__init__
    monkeypatch.setattr(Providers,'__init__',lambda self,store,budget,opener=None:original(self,store,budget,open_))
    assert runner.master()==0 and len(calls)==4
    u,r=runner.verify_master(root/'experiment'/'master')
    assert len(u)==2003 and r['asof']=='2026-10-27' and r['eligible']==2003
    assert runtime.verify(root/'experiment'/'master')['asof']=='2026-10-27'
    # Re-entering the master command cannot silently clear its physical ledger.
    with pytest.raises(Failure,match='existing_state'):runner.master()


def test_artifact_restore_requires_zip_digest_and_manifest_identity(synthetic,monkeypatch):
    root,_=synthetic;monkeypatch.setattr(runner,'ROOT',root/'experiment')
    folder=root/'packed';s=Store(folder);s.write('report.json',{'status':'FAIL'})
    runner.seal(folder,'SYNTHETIC_KIND')
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for f in folder.rglob('*'):
            if f.is_file():z.write(f,str(f.relative_to(folder)))
    archive=buf.getvalue()
    def api(path,*a,binary=None,**k):
        if binary:Path(binary).write_bytes(archive);return
        return {'total_count':1,'artifacts':[{'id':123,'name':'SYNTHETIC','expired':False,'digest':'sha256:'+sha(archive)}]}
    monkeypatch.setattr(runtime,'api',api)
    restored=runner.restore('SYNTHETIC',root/'restored','SYNTHETIC_KIND')
    assert restored['zip_sha256']==sha(archive)
    with pytest.raises(Failure,match='identity'):runner.restore('SYNTHETIC',root/'wrong-kind','DIFFERENT_KIND')


def test_automatic_four_round_artifacts_and_final_report_lifecycle(synthetic,monkeypatch):
    import shutil
    root,rows=synthetic;master=root/'source-master';master.mkdir()
    receipt=dict(status='PASS',asof=policy.ASOF,population_sha256=sha(encoded(rows)),master_rows=len(rows),eligible=3)
    sources={'market-20261027-master':master};published={};physical=[]
    monkeypatch.setattr(runner,'context',lambda:policy.configuration('SYNTHETIC'))
    monkeypatch.setattr(runner,'wait_until',lambda *a,**k:None)
    monkeypatch.setattr(runner,'verify_master',lambda _: (rows,receipt))
    def restore(name,target,kind):
        shutil.copytree(sources[name],target)
        return dict(name=name,artifact_id='SYNTHETIC_'+name,zip_sha256='SYNTHETIC')
    monkeypatch.setattr(runner,'restore',restore)
    def publish(name,value,**kwargs):published[name]=value;return 'SYNTHETIC_SHA'
    monkeypatch.setattr(runner,'publish_file',publish)
    original=manual.ManualAPI.__init__
    def init(self,store,budget,metrics,opener=None):
        calls,open_=next_day_transport(rows);physical.append(calls)
        original(self,store,budget,metrics,open_)
    monkeypatch.setattr(manual.ManualAPI,'__init__',init)
    for n in range(1,5):
        job=root/('job'+str(n));monkeypatch.setattr(runner,'ROOT',job)
        monkeypatch.setattr(policy,'utcnow',lambda n=n:policy.STARTS[n-1]+dt.timedelta(seconds=1))
        assert runner.collect(n)==0
        folder=job/'generations'/policy.generation(n)
        assert runtime.verify(folder)['kind']==policy.generation(n)
        sources['market-20261027-'+policy.LABELS[n-1]]=folder
    assert sum(len(c) for c in physical)==36
    job=root/'comparison-job';monkeypatch.setattr(runner,'ROOT',job)
    assert runner.compare_all()==0
    final=Store(job/'comparison').read('final-report.json')
    assert final['status']=='COMPLETE_OBSERVATION_COMPARISON' and final['official_price_status']=='PENDING_KRX'
    assert final['analysis']['recommendation']=='18시 이후 시작 가능'
    assert len(list((job/'comparison').glob('pair-*.json')))==4
    assert 'final-report.json' in published
    assert '⑤ 권장 정기 수집 시간' in (job/'comparison'/'final-report.md').read_text()
    assert runtime.verify(job/'comparison')['kind']=='comparison'


def test_failed_round_is_sealed_as_failed_not_filled_from_another_round(synthetic,monkeypatch):
    import shutil
    root,rows=synthetic;monkeypatch.setattr(runner,'ROOT',root/'job')
    monkeypatch.setattr(runner,'context',lambda:policy.configuration('SYNTHETIC'))
    monkeypatch.setattr(runner,'wait_until',lambda *a,**k:None)
    monkeypatch.setattr(runner,'restore',lambda name,target,kind: dict(name=name))
    receipt=dict(status='PASS',asof=policy.ASOF,population_sha256=sha(encoded(rows)),master_rows=len(rows),eligible=3)
    monkeypatch.setattr(runner,'verify_master',lambda _: (rows,receipt))
    monkeypatch.setattr(runner,'publish_file',lambda *a,**k:'SYNTHETIC_SHA')
    calls,open_=next_day_transport(rows,forbidden=True);original=manual.ManualAPI.__init__
    monkeypatch.setattr(manual.ManualAPI,'__init__',lambda self,store,budget,metrics,opener=None:original(self,store,budget,metrics,open_))
    assert runner.collect(1)==1
    folder=root/'job'/'generations'/policy.generation(1);r=Store(folder).read('report.json')
    assert r['status']=='FAIL' and r['physical_http']==1 and r['kis_validated']==0 and len(calls)==1
    assert r['rows']==[] and len(r['uncollected_symbols'])==3
    assert runtime.verify(folder)['kind']==policy.generation(1)
