import copy,json,tempfile,unittest,urllib.error,io
from pathlib import Path
from capture import capture,CONTROL,PLAN,actor,REPO,BRANCH
class Reply:
    status=200
    def __init__(self,raw=b'{"OutBlock_1":[]}'):self.raw=raw
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,n):return self.raw
class Tests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'out';self.calls=[]
    def tearDown(self):self.tmp.cleanup()
    def fetch(self,req,timeout):self.calls.append(req.full_url);return Reply()
    def run_capture(self,**kw):return capture(self.root,CONTROL,'TEST_SECRET',opener=kw.get('opener',self.fetch),pause=lambda _:None)
    def test_empty_is_not_verified_and_exact_four_requests(self):
        r=self.run_capture();self.assertEqual(len(self.calls),4);self.assertEqual(r['status'],'PENDING_KRX_EMPTY_RESPONSE');self.assertEqual(r['official_prices_approved'],0)
        self.assertEqual([(s['asof'],s['market']) for s in r['sources']],[tuple(p) for p in PLAN])
        self.assertNotIn(b'TEST_SECRET',b''.join(p.read_bytes() for p in self.root.iterdir()))
    def test_no_secret_no_request(self):self.assertEqual(capture(self.root,CONTROL,None,opener=self.fetch)['physical_KRX_HTTP'],0)
    def test_budget_or_date_cannot_expand(self):
        for key,val in [('max_http',5),('asof','2026-11-05'),('request_id','another')]:
            with self.assertRaises(ValueError):capture(self.root,{**CONTROL,key:val},'TEST_SECRET',opener=self.fetch)
    def test_no_overwrite(self):
        self.root.mkdir()
        with self.assertRaises(ValueError):self.run_capture()
    def test_unknown_transport_stops_and_charges(self):
        def fail(*a,**k):raise TimeoutError()
        r=self.run_capture(opener=fail);self.assertEqual(r['physical_KRX_HTTP'],1);self.assertIn('unknown_no_retry',r['status'])
    def test_HTTP_error_preserved_stops(self):
        def fail(req,**k):raise urllib.error.HTTPError(req.full_url,403,'blocked',{},io.BytesIO(b'forbidden'))
        r=self.run_capture(opener=fail);self.assertEqual(r['physical_KRX_HTTP'],1);self.assertEqual(r['status'],'KRX_HTTP_403');self.assertEqual(len(list(self.root.glob('*.bin'))),1)
    def test_secret_echo_never_persisted(self):
        r=self.run_capture(opener=lambda *a,**k:Reply(b'TEST_SECRET'));self.assertEqual(r['status'],'credential_echo_not_preserved');self.assertEqual(list(self.root.glob('*.bin')),[])
    def test_wrong_market_date_or_duplicate_stops(self):
        row=dict(ISU_CD='000720',BAS_DD='20261104',MKT_NM='KOSPI')
        r=self.run_capture(opener=lambda *a,**k:Reply(json.dumps(dict(OutBlock_1=[row])).encode()));self.assertEqual(r['physical_KRX_HTTP'],1);self.assertIn('mismatch',r['status'])
    def test_malformed_JSON_stops(self):
        r=self.run_capture(opener=lambda *a,**k:Reply(b'not JSON'));self.assertEqual(r['physical_KRX_HTTP'],1);self.assertEqual(r['status'],'KRX_non_JSON_response')
    def test_nonempty_capture_is_not_price_approval(self):
        seq=iter(PLAN)
        def good(*a,**k):
            day,market=next(seq);return Reply(json.dumps(dict(OutBlock_1=[dict(ISU_CD='000720',BAS_DD=day.replace('-',''),MKT_NM=market)])).encode())
        r=self.run_capture(opener=good);self.assertEqual(r['status'],'TARGET_RESPONSES_PRESERVED_NOT_YET_COMPARED');self.assertEqual(r['official_prices_approved'],0)
    def test_actor_attempt_and_history_fail_closed(self):
        env=dict(GITHUB_REPOSITORY=REPO,GITHUB_REF='refs/heads/'+BRANCH,GITHUB_EVENT_NAME='push',GITHUB_RUN_ATTEMPT='1',GITHUB_RUN_ID='1',GITHUB_SHA='a'*40)
        runs=dict(total_count=1,workflow_runs=[dict(id=1,head_sha='a'*40,head_branch=BRANCH)])
        actor(env,runs)
        for bad in [dict(total_count=2,workflow_runs=runs['workflow_runs']*2),dict(total_count=0,workflow_runs=[])]:
            with self.assertRaises(ValueError):actor(env,bad)
        with self.assertRaises(ValueError):actor({**env,'GITHUB_RUN_ATTEMPT':'2'},runs)
