import json,tempfile,unittest,urllib.error
from pathlib import Path
from capture import capture
C=dict(asof='2026-11-02',markets=['KOSPI','KOSDAQ'],max_http=2,request_id='official-review-20261102-v1',purpose='OFFICIAL_SIX_FIELD_READ_ONLY_REVIEW')
class Reply:
    status=200
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,n):return b'{"OutBlock_1":[]}'
class TestCapture(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'out'
    def tearDown(self):self.tmp.cleanup()
    def test_exact_two_no_KIS_and_empty_not_success(self):
        calls=[]
        def fetch(req,timeout):calls.append(req.full_url);return Reply()
        r=capture(self.root,C,'TEST_SECRET',opener=fetch,pause=lambda _:None)
        self.assertEqual(len(calls),2);self.assertEqual(r['physical_KRX_HTTP'],2);self.assertEqual(r['status'],'PENDING_KRX_EMPTY_RESPONSE')
        self.assertTrue(all(u.startswith('https://data-dbg.krx.co.kr/') for u in calls))
        self.assertNotIn(b'TEST_SECRET',b''.join(p.read_bytes() for p in self.root.iterdir()))
    def test_missing_secret_zero_requests(self):self.assertEqual(capture(self.root,C,None)['physical_KRX_HTTP'],0)
    def test_control_cannot_expand(self):
        with self.assertRaises(ValueError):capture(self.root,{**C,'max_http':3},'TEST_SECRET')
    def test_transport_unknown_stops(self):
        def fetch(*a,**k):raise TimeoutError()
        r=capture(self.root,C,'TEST_SECRET',opener=fetch)
        self.assertEqual(r['physical_KRX_HTTP'],1);self.assertEqual(r['status'],'KRX_transport_outcome_unknown_no_retry')
    def test_no_overwrite(self):
        self.root.mkdir()
        with self.assertRaises(ValueError):capture(self.root,C,'TEST_SECRET')
