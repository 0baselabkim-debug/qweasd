"""A lease is never stolen on timeout. Every transition appends a new Git commit.

No tokens/keys enter the ledger. Exclusive sessions deliberately serialize OAuth
and data until a reviewed shared token/rate broker exists.
"""
import copy
import datetime as dt
import re
import uuid

REPOSITORIES = ('0baselabkim-debug/qweasd', '0baselabkim-debug/kis-intraday-monitor')
UTC = dt.timezone.utc


class Blocked(RuntimeError):
    pass


def utcnow():
    return dt.datetime.now(UTC)


def stamp(value):
    result = dt.datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise Blocked('shared_timestamp_timezone_missing')
    return result


def initial():
    return dict(schema=1, policy='EXCLUSIVE_SESSION_NO_EXPIRY_TAKEOVER', sequence=0,
                owner=None, last_auth=None, last_http=None, auth_not_before=None, nonce=uuid.uuid4().hex)


def validate(state):
    try:
        if (state['schema'] != 1 or state['policy'] != 'EXCLUSIVE_SESSION_NO_EXPIRY_TAKEOVER'
                or type(state['sequence']) is not int or state['sequence'] < 0
                or not re.fullmatch('[0-9a-f]{32}', state['nonce'])):
            raise ValueError()
        for key in ('last_auth', 'last_http', 'auth_not_before'):
            if state[key] is not None:
                stamp(state[key])
        o = state['owner']
        if o is not None:
            if (o['repository'] not in REPOSITORIES or not o['run_id'].isdigit()
                    or not re.fullmatch('[0-9a-f]{40}', o['commit'])
                    or not re.fullmatch('[0-9a-f]{32}', o['id'])
                    or not 0 < (stamp(o['expires_at'])-stamp(o['acquired_at'])).total_seconds() <= 11400):
                raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise Blocked('shared_ledger_invalid') from None
    return state


class Lease:
    def __init__(self, backend, repository, run_id, commit, *, clock=utcnow):
        if repository not in REPOSITORIES or not str(run_id).isdigit() or not re.fullmatch('[0-9a-f]{40}', commit):
            raise Blocked('shared_actor_invalid')
        self.backend, self.clock = backend, clock
        self.identity = dict(repository=repository, run_id=str(run_id), commit=commit, id=uuid.uuid4().hex)
        self.held = False
        self.last_local_http = None
        self.confirmed_transitions = 0

    def _read(self):
        revision, state = self.backend.read()
        validate(state)
        return revision, state

    def _write(self, revision, state):
        state = copy.deepcopy(state)
        state['sequence'] += 1
        state['nonce'] = uuid.uuid4().hex
        validate(state)
        if not self.backend.compare_and_append(revision, state):
            raise Blocked('shared_lock_race_lost_or_write_unconfirmed')
        # A lost/ambiguous response must never authorize API requests.
        _, actual = self._read()
        if actual != state:
            raise Blocked('shared_lock_write_readback_mismatch')
        self.confirmed_transitions += 1
        return actual

    def acquire(self, seconds=11400):
        if type(seconds) is not int or not 30 <= seconds <= 11400:
            raise Blocked('shared_lease_duration_invalid')
        revision, state = self._read()
        if state['owner'] is not None:
            reason = 'shared_lock_expired_requires_review' if stamp(state['owner']['expires_at']) <= self.clock() else 'shared_lock_busy'
            raise Blocked(reason)
        at = self.clock()
        state['owner'] = dict(self.identity, acquired_at=at.isoformat(), expires_at=(at+dt.timedelta(seconds=seconds)).isoformat())
        self._write(revision, state)
        self.held = True
        return self.check()

    def check(self, request_seconds=35):
        _, state = self._read()
        owner = state['owner']
        if not self.held or not owner or any(owner.get(k) != v for k,v in self.identity.items()):
            raise Blocked('shared_lock_owner_changed')
        at = self.clock()
        if at < stamp(owner['acquired_at']) or at+dt.timedelta(seconds=request_seconds) >= stamp(owner['expires_at']):
            raise Blocked('shared_lock_expired_or_request_would_overrun')
        return state

    def reserve(self, *, auth=False, interval=1.05, sleep=None):
        # Persist BEFORE dispatch. Unknown HTTP outcomes still consume cooldown.
        if interval < 1.05:
            raise Blocked('shared_rate_interval_cannot_be_lowered')
        state = self.check()
        at = self.clock()
        previous = self.last_local_http or (stamp(state['last_http']) if state['last_http'] else None)
        wait = max(0, interval-(at-previous).total_seconds()) if previous else 0
        if auth and state['last_auth']:
            wait = max(wait, 60-(at-stamp(state['last_auth'])).total_seconds())
        if auth and state['auth_not_before']:
            wait = max(wait, (stamp(state['auth_not_before'])-at).total_seconds())
        if wait > 0 and sleep is not None:
            if wait > 60:
                raise Blocked('shared_clock_or_cooldown_invalid')
            sleep(wait)
            return self.reserve(auth=auth, interval=interval)  # one wait, no HTTP retry
        if previous and (at-previous).total_seconds() < interval:
            raise Blocked('shared_http_cooldown_not_elapsed')
        if auth and state['last_auth'] and (at-stamp(state['last_auth'])).total_seconds() < 60:
            raise Blocked('shared_auth_minute_cooldown_not_elapsed')
        if auth and state['auth_not_before'] and at < stamp(state['auth_not_before']):
            raise Blocked('shared_auth_handover_quarantine_not_elapsed')
        if auth:
            revision, state = self._read()
            if not state['owner'] or state['owner']['id'] != self.identity['id']:
                raise Blocked('shared_lock_owner_changed')
            state['last_auth'] = at.isoformat()
            state['last_http'] = at.isoformat()
            self._write(revision, state)
        self.last_local_http = at
        if auth:
            self.check()

    def release(self):
        # Caller must have joined all request workers and discarded all tokens.
        self.check(request_seconds=0)
        revision, state = self._read()
        if state['owner']['id'] != self.identity['id']:
            raise Blocked('shared_lock_owner_changed')
        state['owner'] = None
        state['last_http'] = self.clock().isoformat()
        # Reserve at least a minute after all requests have returned. This does
        # not relabel the reservation timestamp as an actual OAuth response.
        state['auth_not_before'] = (self.clock()+dt.timedelta(seconds=60)).isoformat()
        self._write(revision, state)
        self.held = False

    def recover(self, expected_revision, verify_terminal):
        """Explicit operator action only. Verifier must query actual owner run.

        Never invoked by acquire, the workflow, a retry or a timeout callback.
        Other account actors and in-flight network timeout must also be reviewed.
        """
        revision, state = self._read()
        owner = state['owner']
        if revision != expected_revision or not owner:
            raise Blocked('shared_recovery_revision_changed')
        if self.clock() < stamp(owner['expires_at'])+dt.timedelta(seconds=60):
            raise Blocked('shared_recovery_quarantine_not_elapsed')
        proof = verify_terminal(copy.deepcopy(owner))
        if (proof.get('repository'), str(proof.get('run_id')), proof.get('commit'), proof.get('status'),
                proof.get('all_workers_stopped'), proof.get('external_issuers_stopped')) != (
                owner['repository'], owner['run_id'], owner['commit'], 'completed', True, True):
            raise Blocked('shared_recovery_terminal_owner_unverified')
        state['owner'] = None
        self._write(revision, state)
