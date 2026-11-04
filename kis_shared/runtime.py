"""Explicit production session; mocks do not install a real session or network."""
from contextvars import ContextVar
import datetime as dt
import hashlib
import hmac
import json
import os
import time
import threading
from pathlib import Path
from zoneinfo import ZoneInfo
from .lease import Blocked, Lease, stamp, utcnow, REPOSITORIES, LEGACY_REPOSITORIES
from .git_backend import GitBackend

_current = ContextVar('kis_shared_session', default=None)


def review_local_evidence(config, repository):
    """Production activation is bound to actual native proofs and deployed code."""
    root=Path(__file__).resolve().parents[1]
    try:
        path=(root/config['native_proof_file']).resolve()
        if not path.is_relative_to(root):raise ValueError()
        raw=path.read_bytes()
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(),config['native_proof_sha256']):raise ValueError()
        bundle=json.loads(raw)
        if config.get('schema')==2 or bundle.get('proof_kind')=='actual_source_trees':
            required={str(i).zfill(2) for i in range(1,33)}
            rows=bundle['scenarios'];source=bundle['runtime_source_hashes']
            if (bundle.get('proof_kind')!='actual_source_trees' or bundle['status']!='PASS' or bundle['actual_KIS_HTTP']!=0 or bundle['oauth_issuance']!=0
                    or bundle['production_ledger_mutations']!=0 or set(source)!=set(REPOSITORIES)
                    or not required <= {row['scenario'][:2] for row in rows}
                    or any(row['status']!='PASS' or row['actual_KIS_HTTP']!=0 for row in rows)):
                raise ValueError()
            hashes=source[repository]
        else:
            native=bundle['native']
            if set(native)!=set(LEGACY_REPOSITORIES):raise ValueError()
            for repo,proof in native.items():
                if (proof['repository']!=repo or proof['phase']!='PASS'
                        or proof['request_id']!=bundle['request_id'] or proof['market_http']!=0
                        or proof['oauth_issuance']!=0 or proof['final_owner'] is not None
                        or proof['mutual_exclusion_both_directions'] is not True or proof['final_sequence']!=4):raise ValueError()
            hashes=native[repository]['source']['runtime_source_hashes']
        if not hashes or hashes!=config['runtime_source_hashes']:raise ValueError()
        for name,expected in hashes.items():
            source=(root/name).resolve()
            if not source.is_relative_to(root) or hashlib.sha256(source.read_bytes()).hexdigest()!=expected:raise ValueError()
        return bundle
    except (OSError,ValueError,KeyError,TypeError):
        raise Blocked('shared_native_evidence_missing_changed_or_source_not_reviewed') from None


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
        self.dispatch_ticket = None
        self.network_inflight = False
        self.transport_pending = None
        self.reservation_lock = threading.RLock()

    def review(self):
        c = self.config
        if getattr(self.backend,'native_proof',False):
            if c.get('mode')!='native-proof' or self.env.get('EXECUTION_MODE')!='NATIVE_PROOF' or any(self.env.get(k)!='SYNTHETIC_ONLY' for k in ('KIS_APP_KEY','KIS_APP_SECRET')):
                raise Blocked('native_proof_must_use_synthetic_credentials')
            if self.env.get('GITHUB_REPOSITORY') not in REPOSITORIES:raise Blocked('native_actor_invalid')
            if self.lease is not None and any(self.env.get(k)!=self.lease.identity[v] for k,v in (('GITHUB_REPOSITORY','repository'),('GITHUB_RUN_ID','run_id'),('GITHUB_SHA','commit'))):raise Blocked('shared_runtime_actor_changed')
            return
        if self.env.get('GITHUB_REPOSITORY') not in (REPOSITORIES if c.get('schema')==2 else LEGACY_REPOSITORIES):
            raise Blocked('shared_participant_not_activated_for_policy_version')
        if c.get('schema')==2:
            if c.get('live_activation_approved') is not True or c.get('participants')!=list(REPOSITORIES) or not c.get('activation_reference'):
                raise Blocked('v2_three_party_activation_not_approved')
        if c.get('enabled') is not True or c.get('mode') != 'exclusive-shared-account':
            raise Blocked('shared_rollout_not_approved')
        required = (('all_participants_deployed','all_account_actors_enrolled','github_access_tested','rate_policy_reviewed','token_policy_reviewed','native_lock_proof_passed','operator_account_coordination_approved') if c.get('schema')==2 else ('both_repositories_deployed', 'all_account_actors_enrolled', 'github_access_tested', 'rate_policy_reviewed', 'token_policy_reviewed', 'real_kis_validation_approved_and_passed'))
        if any(c.get(k) is not True for k in required):
            raise Blocked('shared_rollout_evidence_incomplete')
        if not all(isinstance(c.get(k), str) and c[k].strip() for k in
                   ('review_reference', 'rate_reference', 'token_reference', 'permissions_reference')):
            raise Blocked('shared_rollout_references_missing')
        if not stamp(c['reviewed_at']) <= self.clock() < stamp(c['expires_at']):
            raise Blocked('shared_review_expired')
        if stamp(c['expires_at'])-stamp(c['reviewed_at']) > dt.timedelta(days=90):
            raise Blocked('shared_review_too_long')
        if self.backend is None:
            review_local_evidence(c,self.env.get('GITHUB_REPOSITORY'))
        if c.get('runtime_source_hashes'):
            root=Path(__file__).resolve().parents[1]
            for name, expected in c['runtime_source_hashes'].items():
                path=(root/name).resolve()
                if (not path.is_relative_to(root) or not path.is_file()
                        or not hmac.compare_digest(hashlib.sha256(path.read_bytes()).hexdigest(),expected)):
                    raise Blocked('shared_runtime_source_not_reviewed')
        if not self.env.get('KIS_APP_KEY') or not self.env.get('KIS_APP_SECRET'):
            raise Blocked('shared_existing_credentials_missing')
        binding = hashlib.sha256(json.dumps([self.env['KIS_APP_KEY'],self.env['KIS_APP_SECRET']]).encode()).digest()
        if self.binding is not None and not hmac.compare_digest(binding,self.binding):
            raise Blocked('shared_credential_binding_changed')
        if self.lease is not None and any(self.env.get(k) != self.lease.identity[v] for k,v in
                (('GITHUB_REPOSITORY','repository'),('GITHUB_RUN_ID','run_id'),('GITHUB_SHA','commit'))):
            raise Blocked('shared_runtime_actor_changed')

    def acquire_lease(self, seconds=11400, *, purpose=None):
        """Acquire the account lease independently of the entrypoint context."""
        self.review()
        if self.lease is not None:
            raise Blocked('shared_account_lease_already_created')
        self.binding = hashlib.sha256(json.dumps([self.env['KIS_APP_KEY'],self.env['KIS_APP_SECRET']]).encode()).digest()
        if self.env.get('KIS_ACCESS_TOKEN'):
            try:self.token_expiry(self.env['KIS_ACCESS_TOKEN_EXPIRES_AT'])
            except Exception:
                raise Blocked('shared_existing_token_expiry_unverified') from None
        backend = self.backend or GitBackend(Path(self.env['RUNNER_TEMP'])/'kis-account-lease.git')
        self.lease = Lease(backend, self.env['GITHUB_REPOSITORY'], self.env['GITHUB_RUN_ID'],
                           self.env['GITHUB_SHA'], clock=self.clock, scope_id=self.env.get('KIS_VALIDATION_REQUEST_ID'))
        self.lease.acquire(seconds=seconds,purpose=purpose)
        return self

    def __enter__(self):
        if self.env.get('GITHUB_REPOSITORY')==LEGACY_REPOSITORIES[1] and not getattr(self.backend,'native_proof',False):
            from .admission import check_live_admission
            check_live_admission()
        if _current.get() is not None:
            raise Blocked('shared_nested_session_rejected')
        self.acquire_lease()
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
        with self.reservation_lock:
            return self._reserve_http(auth=auth,interval=interval)

    def check_http_approval(self):
        # Account enrollment/metadata coordination is independently approved.
        # The third participant still requires a separate live-call grant.
        if (not getattr(self.backend,'native_proof',False) and self.config.get('schema')==2
                and self.env.get('GITHUB_REPOSITORY')==REPOSITORIES[2]
                and self.config.get('operator_live_validation_approved') is not True):
            raise Blocked('shared_new_participant_live_HTTP_not_approved')

    def _reserve_http(self, *, auth=False, interval=1.05):
        self.check_http_approval()
        if self.network_inflight or self.uncertain or self.dispatch_ticket is not None:
            raise Blocked('shared_prior_HTTP_outcome_unresolved')
        self.review()
        if self.lease is None:
            raise Blocked('shared_session_not_acquired')
        if self.token_deadline is not None and self.clock()+dt.timedelta(seconds=35) >= self.token_deadline:
            raise Blocked('shared_token_expired_no_refresh')
        if auth and self.auth_used:
            raise Blocked('shared_oauth_one_attempt_no_refresh')
        self.lease.reserve(auth=auth, interval=interval, sleep=getattr(self.clock,"sleep",time.sleep))
        self.dispatch_ticket = bool(auth)
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
            zone = self.config.get('token_expiry_timezone')
            if self.config.get('token_expiry_policy') == 'CONSERVATIVE_EARLIEST_CIVIL_OFFSET':
                seconds = value['expires_in']
                if type(seconds) is not int or not 35 < seconds <= 86400:
                    raise ValueError()
                naive = dt.datetime.strptime(value['access_token_token_expired'], '%Y-%m-%d %H:%M:%S')
                earliest = naive.replace(tzinfo=dt.timezone.utc)-dt.timedelta(hours=14)
                self.token_expiry(min(earliest, self.clock()+dt.timedelta(seconds=seconds-35)).isoformat())
                return
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
        if kind is None and not self.uncertain and not self.network_inflight:
            self.token_deadline = None
            self.lease.release()

    def http_response_closed(self):
        """Only called after the synchronous response is known closed."""
        with self.reservation_lock:
            self.network_inflight = False
            self.dispatch_ticket = None
            self.transport_pending = None

    def authorize_network(self,auth):
        self.check_http_approval()
        if not auth and self.token_deadline is None:raise Blocked('shared_market_token_expiry_unverified')
        self.check()
        if not self.network_inflight:
            if self.dispatch_ticket is None:
                self.before_http(auth=auth)
            if self.dispatch_ticket is not bool(auth):
                raise Blocked("shared_dispatch_ticket_kind_mismatch")
            self.dispatch_ticket=None
            self.network_inflight=True
        self.check()


class AccountSession(Session):
    """Account coordination for an existing bounded entrypoint, not data approval."""
    pending_http = False

    def __enter__(self):
        return self.acquire_lease(seconds=300)

    def before_http(self, *, auth=False, interval=1.05):
        if self.pending_http:
            raise Blocked('shared_prior_HTTP_outcome_unresolved')
        super().before_http(auth=auth, interval=interval)
        self.pending_http = True

    def http_response_closed(self):
        self.pending_http = False
        super().http_response_closed()

    def __exit__(self, kind, value, traceback):
        # A controlled validation failure does not leave an account permanently
        # locked after every synchronous response has closed. Hard kills never
        # reach here, and unresolved transport outcomes retain ownership.
        if not self.uncertain and not self.pending_http:
            self.token_deadline = None
            self.lease.release()


def configured(path, **kwargs):
    return Session(json.loads(Path(path).read_bytes()), **kwargs)
