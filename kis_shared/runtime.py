"""Explicit production session; mocks do not install a real session or network."""
from contextvars import ContextVar
import datetime as dt
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo
from .lease import Blocked, Lease, stamp, utcnow
from .git_backend import GitBackend

_current = ContextVar('kis_shared_session', default=None)


def current():
    session = _current.get()
    if session is None:
        raise Blocked('shared_session_not_installed_no_live_http')
    return session


class Session:
    def __init__(self, config, *, backend=None, env=None, clock=utcnow):
        self.config, self.env, self.clock = config, env if env is not None else os.environ, clock
        self.backend = backend
        self.lease = None
        self.auth_used = False
        self.token_deadline = None
        self.binding = None
        self.uncertain = False

    def review(self):
        c = self.config
        if c.get('enabled') is not True or c.get('mode') != 'exclusive-shared-account':
            raise Blocked('shared_rollout_not_approved')
        required = ('both_repositories_deployed', 'all_account_actors_enrolled', 'github_access_tested',
                    'rate_policy_reviewed', 'token_policy_reviewed', 'real_kis_validation_approved_and_passed')
        if any(c.get(k) is not True for k in required):
            raise Blocked('shared_rollout_evidence_incomplete')
        if not all(isinstance(c.get(k), str) and c[k].strip() for k in
                   ('review_reference', 'rate_reference', 'token_reference', 'permissions_reference')):
            raise Blocked('shared_rollout_references_missing')
        if not stamp(c['reviewed_at']) <= self.clock() < stamp(c['expires_at']):
            raise Blocked('shared_review_expired')
        if stamp(c['expires_at'])-stamp(c['reviewed_at']) > dt.timedelta(days=90):
            raise Blocked('shared_review_too_long')
        if not self.env.get('KIS_APP_KEY') or not self.env.get('KIS_APP_SECRET'):
            raise Blocked('shared_existing_credentials_missing')
        binding = hashlib.sha256(json.dumps([self.env['KIS_APP_KEY'],self.env['KIS_APP_SECRET']]).encode()).digest()
        if self.binding is not None and not hmac.compare_digest(binding,self.binding):
            raise Blocked('shared_credential_binding_changed')
        if self.lease is not None and any(self.env.get(k) != self.lease.identity[v] for k,v in
                (('GITHUB_REPOSITORY','repository'),('GITHUB_RUN_ID','run_id'),('GITHUB_SHA','commit'))):
            raise Blocked('shared_runtime_actor_changed')

    def __enter__(self):
        self.review()
        self.binding = hashlib.sha256(json.dumps([self.env['KIS_APP_KEY'],self.env['KIS_APP_SECRET']]).encode()).digest()
        if _current.get() is not None:
            raise Blocked('shared_nested_session_rejected')
        if self.env.get('KIS_ACCESS_TOKEN'):
            try:self.token_expiry(self.env['KIS_ACCESS_TOKEN_EXPIRES_AT'])
            except Exception:
                raise Blocked('shared_existing_token_expiry_unverified') from None
        backend = self.backend or GitBackend(Path(self.env['RUNNER_TEMP'])/'kis-account-lease.git')
        self.lease = Lease(backend, self.env['GITHUB_REPOSITORY'], self.env['GITHUB_RUN_ID'],
                           self.env['GITHUB_SHA'], clock=self.clock)
        self.lease.acquire()
        self.marker = _current.set(self)
        return self

    def check(self):
        self.review()
        if self.lease is None:
            raise Blocked('shared_session_not_acquired')
        if self.token_deadline is not None and self.clock()+dt.timedelta(seconds=35) >= self.token_deadline:
            raise Blocked('shared_token_expired_no_refresh')
        return self.lease.check()

    def before_http(self, *, auth=False, interval=1.05):
        self.review()
        if self.lease is None:
            raise Blocked('shared_session_not_acquired')
        if self.token_deadline is not None and self.clock()+dt.timedelta(seconds=35) >= self.token_deadline:
            raise Blocked('shared_token_expired_no_refresh')
        if auth and self.auth_used:
            raise Blocked('shared_oauth_one_attempt_no_refresh')
        self.lease.reserve(auth=auth, interval=interval, sleep=time.sleep)
        if auth:
            self.auth_used = True  # failure and unknown outcome count too

    def token_expiry(self, expires_at):
        deadline = stamp(expires_at)
        if deadline <= self.clock()+dt.timedelta(seconds=35):
            raise Blocked('shared_token_lifetime_insufficient')
        self.token_deadline = deadline

    def token_response(self, value):
        """Both duration and actual server expiry: cached lifetime is not guessed."""
        try:
            zone = self.config['token_expiry_timezone']
            if zone not in ('Asia/Seoul', 'UTC'):
                raise ValueError()
            seconds = value['expires_in']
            if type(seconds) is not int or not 35 < seconds <= 86400:
                raise ValueError()
            official = dt.datetime.strptime(value['access_token_token_expired'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo(zone))
            self.token_expiry(min(official, self.clock()+dt.timedelta(seconds=seconds-35)).isoformat())
        except (KeyError, ValueError, TypeError):
            raise Blocked('shared_token_response_expiry_unverified') from None

    def __exit__(self, kind, value, traceback):
        _current.reset(self.marker)
        # No worker thread is created by these synchronous clients. On exception
        # retain ownership for explicit stopped-owner review; never guess outcome.
        if kind is None and not self.uncertain:
            self.token_deadline = None
            self.lease.release()


def configured(path):
    return Session(json.loads(Path(path).read_bytes()))
