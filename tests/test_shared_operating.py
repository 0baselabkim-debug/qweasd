"""Account coordination safety; no network, tokens, or production state."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kis_shared import runtime
from kis_shared.lease import Blocked, REPOSITORIES
import test_kis_shared as shared_fixtures


class AccountOperatingTests(unittest.TestCase):
    def setUp(self):
        self.at=dt.datetime(2026,10,30,7,tzinfo=dt.timezone.utc)
        self.backend=shared_fixtures.Backend()
        f=shared_fixtures.SharedTests();f.at=self.at;self.config=f.config()
        self.env=dict(GITHUB_REPOSITORY=REPOSITORIES[1],GITHUB_RUN_ID='101',GITHUB_SHA='b'*40,
            KIS_APP_KEY='SYNTHETIC',KIS_APP_SECRET='SYNTHETIC_SECRET')

    def account(self):
        return runtime.AccountSession(self.config,backend=self.backend,env=self.env,clock=lambda:self.at)

    def test_monitor_owner_prevents_collector_authentication(self):
        with self.account() as owner:
            other_env={**self.env,'GITHUB_REPOSITORY':REPOSITORIES[0],'GITHUB_RUN_ID':'100'}
            other=runtime.AccountSession(self.config,backend=self.backend,env=other_env,clock=lambda:self.at)
            with self.assertRaisesRegex(Blocked,'busy'):other.__enter__()
            self.assertIsNone(self.backend.state['last_auth'])
            self.assertEqual(self.backend.state['owner']['run_id'],'101')
        self.assertIsNone(self.backend.state['owner'])

    def test_unknown_response_retains_owner_even_on_normal_entrypoint_return(self):
        with self.account() as account:account.before_http(auth=True)
        self.assertIsNotNone(self.backend.state['owner'])
        self.at+=dt.timedelta(minutes=10)
        with self.assertRaisesRegex(Blocked,'requires_review'):self.account().__enter__()

    def test_closed_response_allows_clean_handover(self):
        with self.account() as account:
            account.before_http(auth=True);account.http_response_closed()
        self.assertIsNone(self.backend.state['owner'])

    def test_next_request_is_forbidden_while_previous_outcome_unknown(self):
        with self.account() as account:
            account.before_http()
            self.at+=dt.timedelta(seconds=5)
            with self.assertRaisesRegex(Blocked,'unresolved'):account.before_http()

    def test_conservative_expiry_never_guesses_server_timezone(self):
        self.config['token_expiry_policy']='CONSERVATIVE_EARLIEST_CIVIL_OFFSET'
        with self.account() as account:
            account.token_response(dict(expires_in=86400,
                access_token_token_expired=(self.at+dt.timedelta(hours=16)).strftime('%Y-%m-%d %H:%M:%S')))
            self.assertEqual(account.token_deadline,self.at+dt.timedelta(hours=2))
            self.assertNotIn('token_expiry_timezone',self.config)

    def test_duration_can_only_shorten_conservative_bound(self):
        self.config['token_expiry_policy']='CONSERVATIVE_EARLIEST_CIVIL_OFFSET'
        with self.account() as account:
            account.token_response(dict(expires_in=3600,
                access_token_token_expired=(self.at+dt.timedelta(hours=16)).strftime('%Y-%m-%d %H:%M:%S')))
            self.assertEqual(account.token_deadline,self.at+dt.timedelta(seconds=3565))

    def test_cached_or_bad_expiry_is_not_extended_by_expires_in(self):
        self.config['token_expiry_policy']='CONSERVATIVE_EARLIEST_CIVIL_OFFSET'
        with self.account() as account:
            with self.assertRaises(Blocked):
                account.token_response(dict(expires_in=86400,access_token_token_expired=self.at.strftime('%Y-%m-%d %H:%M:%S')))

    def test_flags_alone_cannot_authorize_production_without_native_evidence(self):
        account=runtime.Session(self.config,env=self.env,clock=lambda:self.at)
        with self.assertRaisesRegex(Blocked,'native_evidence'):account.review()

    def test_reviewed_source_or_receipt_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'transport.py';source.write_bytes(b'SYNTHETIC_SOURCE')
            hashes={'transport.py':hashlib.sha256(source.read_bytes()).hexdigest()}
            proof=dict(request_id='SYNTHETIC',native={repo:dict(repository=repo,phase='PASS',request_id='SYNTHETIC',
                market_http=0,oauth_issuance=0,final_owner=None,mutual_exclusion_both_directions=True,
                final_sequence=4,source=dict(runtime_source_hashes=hashes)) for repo in REPOSITORIES})
            raw=json.dumps(proof).encode();(root/'proof.json').write_bytes(raw)
            cfg=dict(native_proof_file='proof.json',native_proof_sha256=hashlib.sha256(raw).hexdigest(),runtime_source_hashes=hashes)
            with patch.object(runtime,'__file__',str(root/'kis_shared/runtime.py')):
                self.assertEqual(runtime.review_local_evidence(cfg,REPOSITORIES[0]),proof)
                source.write_bytes(b'SYNTHETIC_CHANGED')
                with self.assertRaises(Blocked):runtime.review_local_evidence(cfg,REPOSITORIES[0])
                source.write_bytes(b'SYNTHETIC_SOURCE');(root/'proof.json').write_bytes(raw+b' ')
                with self.assertRaises(Blocked):runtime.review_local_evidence(cfg,REPOSITORIES[0])


if __name__=='__main__':unittest.main()
