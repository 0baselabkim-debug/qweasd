"""No sockets or credentials: adversarial exclusive-session state transitions."""
import copy
import datetime as dt
import threading
import unittest
from kis_shared.lease import Blocked, Lease, initial, REPOSITORIES, UTC
from kis_shared.runtime import Session, current


class Backend:
    def __init__(self):
        self.state, self.revision = initial(), 0
        self.mutex = threading.Lock()
        self.fail_read = self.fail_write = False
    def read(self):
        if self.fail_read:raise Blocked('read_unavailable')
        with self.mutex:return str(self.revision), copy.deepcopy(self.state)
    def compare_and_append(self, expected, state):
        if self.fail_write:return False
        with self.mutex:
            if str(self.revision) != expected:return False
            self.revision += 1;self.state = copy.deepcopy(state);return True


class SharedTests(unittest.TestCase):
    def setUp(self):
        self.at = dt.datetime(2026,10,29,9,tzinfo=UTC)
        self.backend = Backend()
        self.a = Lease(self.backend,REPOSITORIES[0],'100','a'*40,clock=lambda:self.at)
        self.b = Lease(self.backend,REPOSITORIES[1],'200','b'*40,clock=lambda:self.at)
    def test_race_exactly_one_winner(self):
        barrier=threading.Barrier(2);wins=[];fail=[]
        def worker(lease):
            barrier.wait()
            try:lease.acquire();wins.append(lease)
            except Blocked:fail.append(lease)
        threads=[threading.Thread(target=worker,args=(x,)) for x in (self.a,self.b)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual((len(wins),len(fail)),(1,1))
    def test_loser_has_no_auth_permission(self):
        self.a.acquire()
        with self.assertRaises(Blocked):self.b.acquire()
        with self.assertRaises(Blocked):self.b.reserve(auth=True)
        self.assertIsNone(self.backend.state['last_auth'])
    def test_two_repositories_handover(self):
        self.a.acquire();self.a.reserve(auth=True);self.a.release()
        self.at+=dt.timedelta(seconds=61)
        self.b.acquire();self.b.reserve(auth=True)
        self.assertEqual(self.backend.state['owner']['repository'],REPOSITORIES[1])
    def test_expiration_never_steals(self):
        self.a.acquire(seconds=60);self.at+=dt.timedelta(seconds=61)
        with self.assertRaisesRegex(Blocked,'requires_review'):self.b.acquire()
        with self.assertRaises(Blocked):self.a.reserve()
    def test_near_expiry_blocks_request(self):
        self.a.acquire(seconds=60);self.at+=dt.timedelta(seconds=26)
        with self.assertRaises(Blocked):self.a.reserve()
    def test_read_failure_blocks(self):
        self.a.acquire();self.backend.fail_read=True
        with self.assertRaises(Blocked):self.a.reserve(auth=True)
    def test_write_failure_blocks_auth(self):
        self.a.acquire();self.backend.fail_write=True
        with self.assertRaises(Blocked):self.a.reserve(auth=True)
    def test_changed_owner_blocks(self):
        self.a.acquire();self.backend.state['owner']['id']='0'*32
        with self.assertRaises(Blocked):self.a.reserve()
    def test_cooldown_across_sessions(self):
        self.a.acquire();self.a.reserve(auth=True);self.a.release();self.b.acquire()
        with self.assertRaises(Blocked):self.b.reserve(auth=True)
    def test_spacing_and_no_speed_increase(self):
        self.a.acquire();self.a.reserve()
        with self.assertRaises(Blocked):self.a.reserve()
        self.at+=dt.timedelta(seconds=1.05);self.a.reserve()
        with self.assertRaises(Blocked):self.a.reserve(interval=.1)
    def test_single_bounded_wait_not_http_retry(self):
        self.a.acquire();self.a.reserve()
        sleeps=[]
        def wait(seconds):sleeps.append(seconds);self.at+=dt.timedelta(seconds=seconds)
        self.a.reserve(sleep=wait)
        self.assertEqual(sleeps,[1.05])
    def test_recovery_requires_actual_terminal_owner(self):
        self.a.acquire(seconds=60);self.at+=dt.timedelta(seconds=121)
        revision,_=self.backend.read()
        with self.assertRaises(Blocked):self.b.recover(revision,lambda o:dict(status='in_progress'))
        self.assertIsNotNone(self.backend.state['owner'])
    def test_recovery_after_quarantine_preserves_auth_history(self):
        self.a.acquire(seconds=60);self.a.reserve(auth=True);at=self.backend.state['last_auth']
        self.at+=dt.timedelta(seconds=121);revision,_=self.backend.read()
        self.b.recover(revision,lambda o:dict(repository=o['repository'],run_id=o['run_id'],commit=o['commit'],
             status='completed',all_workers_stopped=True,external_issuers_stopped=True))
        self.assertIsNone(self.backend.state['owner']);self.assertEqual(self.backend.state['last_auth'],at)
    def test_recovery_revision_race_blocks(self):
        self.a.acquire(seconds=60);self.at+=dt.timedelta(seconds=121)
        with self.assertRaises(Blocked):self.b.recover('0',lambda o:{})
    def config(self):
        return dict(enabled=True,mode='exclusive-shared-account',both_repositories_deployed=True,
            all_account_actors_enrolled=True,github_access_tested=True,rate_policy_reviewed=True,
            token_policy_reviewed=True,real_kis_validation_approved_and_passed=True,
            review_reference='SYNTHETIC',rate_reference='SYNTHETIC',token_reference='SYNTHETIC',
            permissions_reference='SYNTHETIC',reviewed_at=(self.at-dt.timedelta(seconds=1)).isoformat(),
            expires_at=(self.at+dt.timedelta(days=1)).isoformat())
    def session(self,config=None):
        env=dict(GITHUB_REPOSITORY=REPOSITORIES[0],GITHUB_RUN_ID='100',GITHUB_SHA='a'*40,
                 KIS_APP_KEY='SYNTHETIC_KEY',KIS_APP_SECRET='SYNTHETIC_SECRET')
        return Session(config or self.config(),backend=self.backend,env=env,clock=lambda:self.at)
    def test_verified_alone_cannot_enable(self):
        with self.assertRaises(Blocked):
            with self.session(dict(enabled=True,verified=True,mode='exclusive-shared-account')):pass
        self.assertIsNone(self.backend.state['owner'])
    def test_absent_session_blocks(self):
        with self.assertRaises(Blocked):current()
    def test_auth_failure_cannot_retry(self):
        with self.session() as s:
            s.before_http(auth=True);self.at+=dt.timedelta(seconds=61)
            with self.assertRaises(Blocked):s.before_http(auth=True)
    def test_token_expiry_no_refresh(self):
        with self.session() as s:
            s.token_expiry((self.at+dt.timedelta(seconds=100)).isoformat())
            self.at+=dt.timedelta(seconds=66)
            with self.assertRaises(Blocked):s.before_http()
    def test_crash_keeps_owner(self):
        with self.assertRaises(RuntimeError):
            with self.session():raise RuntimeError('synthetic crash')
        self.assertIsNotNone(self.backend.state['owner'])
    def test_429_or_5xx_does_not_release_unknown_session(self):
        for status in (429,503):
            self.setUp()
            with self.assertRaises(RuntimeError):
                with self.session() as s:
                    s.before_http();raise RuntimeError(str(status))
            with self.assertRaises(Blocked):self.b.acquire()
    def test_no_credentials_in_state(self):
        with self.session() as s:s.before_http(auth=True)
        self.assertNotIn('SYNTHETIC',str(self.backend.state))
    def test_corrupt_state_rejected(self):
        self.backend.state['sequence']='0'
        with self.assertRaises(Blocked):self.a.acquire()
    def test_clock_rollback_blocks(self):
        self.a.acquire();self.at-=dt.timedelta(seconds=1)
        with self.assertRaises(Blocked):self.a.reserve()
    def test_credential_change_blocks_without_reauth(self):
        with self.session() as s:
            s.env['KIS_APP_KEY']='OTHER_SYNTHETIC_KEY'
            with self.assertRaisesRegex(Blocked,'binding_changed'):s.before_http()
            s.env['KIS_APP_KEY']='SYNTHETIC_KEY'
    def test_runtime_actor_change_blocks(self):
        with self.session() as s:
            s.env['GITHUB_RUN_ID']='101'
            with self.assertRaisesRegex(Blocked,'actor_changed'):s.before_http()
            s.env['GITHUB_RUN_ID']='100'
    def test_cached_token_expiry_uses_actual_server_expiry(self):
        with self.session() as s:
            s.config['token_expiry_timezone']='UTC'
            s.token_response(dict(expires_in=86400,access_token_token_expired=(self.at+dt.timedelta(seconds=100)).strftime('%Y-%m-%d %H:%M:%S')))
            self.at+=dt.timedelta(seconds=66)
            with self.assertRaisesRegex(Blocked,'expired_no_refresh'):s.before_http()
    def test_unknown_server_expiry_timezone_not_guessed(self):
        with self.session() as s:
            with self.assertRaisesRegex(Blocked,'expiry_unverified'):
                s.token_response(dict(expires_in=86400,access_token_token_expired='2026-10-30 09:00:00'))
    def test_failed_report_cannot_silently_release_owner(self):
        with self.session() as s:s.uncertain=True
        self.assertIsNotNone(self.backend.state['owner'])


if __name__=='__main__':unittest.main()
