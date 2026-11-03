"""One Actions process: memory-only tokens, <=1 OAuth POST, zero retries.

No credential files, cache/artifacts/Secrets persistence, token logging or orders.
Reissuance policy must be independently reviewed before the only possible POST.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import ssl
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from stage3 import live_probe as live

k = live.offline
TOKEN_URL = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
MAX_AUTH_BYTES = 65536


def header_value(value):
    return (isinstance(value, str) and 0 < len(value) <= 4096 and value.isascii()
            and not any(ord(c) <= 32 or ord(c) == 127 for c in value))


@dataclass(repr=False)
class MemoryToken:
    value: str = field(repr=False)
    expires_at: datetime
    binding: str = field(repr=False)


class Broker:
    minimum_remaining_seconds = 330

    def __init__(self, *, opener=None, now=live.utcnow, monotonic=time.monotonic):
        self.opener = opener
        self.now, self.monotonic = now, monotonic
        self._binding_key = secrets.token_bytes(32)
        self._session = None
        self._sensitive_values = []
        self.attempts = self.successes = self.failures = self.reuses = 0
        self.outcome_unknown = False
        self.last_started = None

    def __repr__(self):
        return "<ActionsTokenBroker credentials=REDACTED>"

    def sensitive_values(self):
        return tuple(self._sensitive_values)

    def _binding(self, env, token, expires):
        raw = k.encoded([env.get("KIS_APP_KEY"), env.get("KIS_APP_SECRET"), token, expires.isoformat()])
        return hmac.new(self._binding_key, raw, hashlib.sha256).hexdigest()

    def usable(self, env):
        session = self._session
        return bool(session and session.expires_at > self.now()+timedelta(seconds=self.minimum_remaining_seconds)
                    and hmac.compare_digest(session.binding, self._binding(env, session.value, session.expires_at)))

    def guard_session(self, env):
        if not self.usable(env):
            raise live.Blocked("actions_memory_token_expired_or_binding_changed")

    def _append(self, path, event):
        with path.open("ab") as handle:
            handle.write(k.encoded(event)+b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def preflight(self, config, env):
        if not all(header_value(env.get(name)) for name in ("KIS_APP_KEY", "KIS_APP_SECRET")):
            raise live.Blocked("actions_app_credentials_missing_or_invalid")
        if self._session is not None:
            if self.usable(env):
                return "reuse"
            # No refresh, including after token expiry or credential replacement.
            raise live.Blocked("actions_memory_token_expired_or_binding_changed")
        if config.get("token_mode") != "reuse_or_issue_once":
            raise live.Blocked("no_trusted_memory_token_reuse_only")
        self.check_issuance_policy(config, env)
        if self.attempts >= 1 or type(config.get("auth_call_cap")) is not int or config["auth_call_cap"] != 1:
            raise live.Blocked("oauth_authentication_budget_exhausted")
        return "issue_once"

    def check_issuance_policy(self, config, env):
        flags = ("token_issuance_policy_verified", "token_reissue_effect_verified",
                 "token_expiry_timezone_verified", "account_auth_limit_verified")
        if (any(config.get(name) is not True for name in flags)
                or config.get("token_reissue_effect") != "PREVIOUS_TOKENS_REMAIN_VALID"
                or not all(isinstance(config.get(name), str) and config[name].strip()
                           for name in ("issuance_policy_reference", "reissue_effect_record_id", "expiry_timezone_reference"))
                or config.get("token_expiry_timezone") not in ("Asia/Seoul", "UTC")):
            raise live.Blocked("oauth_issuance_or_previous_token_effect_unverified")

    def response_expiry(self, value, config, seconds):
        expiry = datetime.strptime(value["access_token_token_expired"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=ZoneInfo(config["token_expiry_timezone"]))
        return min(expiry, self.now()+timedelta(seconds=seconds))

    def acquire(self, config, env, *, root, guard):
        if self.preflight(config, env) == "reuse":
            guard()
            self.guard_session(env)
            self.reuses += 1
            return self._session
        request = urllib.request.Request(TOKEN_URL, method="POST",
            headers={"Content-Type": "application/json; charset=UTF-8"},
            data=k.encoded(dict(grant_type="client_credentials", appkey=env["KIS_APP_KEY"], appsecret=env["KIS_APP_SECRET"])))
        opener = self.opener or urllib.request.build_opener(live.NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        ledger = Path(root)/"authentication.jsonl"
        guard()
        if self.opener is None:
            from kis_shared.admission import check_live_admission
            check_live_admission()
            from kis_shared.runtime import current
            shared = current()
            if hasattr(shared, "validate_request"):
                shared.validate_request(request)
            shared.before_http(auth=True, interval=2)
        # A durable reservation plus the shared initial-six GitHub tag prevents reruns.
        live.write_new(ledger, k.encoded(dict(event="authentication_started", sequence=1,
            started_at=self.now().isoformat(), endpoint="/oauth2/tokenP", physical_cap=1))+b"\n")
        self.attempts = 1
        self.last_started = self.monotonic()
        status, outcome = None, "FAIL"
        try:
            try:
                response = opener.open(request, timeout=20)
            except urllib.error.HTTPError as error:
                response = error
            except Exception:
                self.outcome_unknown = True
                raise live.Blocked("oauth_transport_outcome_unknown_no_retry") from None
            try:
                with response:
                    status = response.status
                    raw = response.read(MAX_AUTH_BYTES+1)
                if self.opener is None:
                    from kis_shared.runtime import current
                    current().http_response_closed()
            except Exception:
                self.outcome_unknown = True
                raise live.Blocked("oauth_body_transport_outcome_unknown_no_retry") from None
            if len(raw) > MAX_AUTH_BYTES:
                raise live.Blocked("oauth_response_size_rejected")
            value = k.strict_json(raw)
            token = value.get("access_token") if isinstance(value, dict) else None
            if isinstance(token, str) and token:
                self._sensitive_values.append(token)
            if (status != 200 or not isinstance(value, dict) or not header_value(token)
                    or value.get("token_type") != "Bearer" or value.get("error")
                    or value.get("rt_cd", "0") != "0"):
                raise live.Blocked("oauth_response_rejected_no_retry")
            seconds = value["expires_in"]
            if type(seconds) is not int or not 0 < seconds <= 86400:
                raise live.Blocked("oauth_expiry_seconds_rejected")
            expiry = self.response_expiry(value, config, seconds)
            if expiry <= self.now()+timedelta(seconds=self.minimum_remaining_seconds):
                raise live.Blocked("oauth_token_expiry_not_safe")
            session = MemoryToken(token, expiry, self._binding(env, token, expiry))
            if self.opener is None:
                from kis_shared.runtime import current
                current().token_response(value)
            # Guard again after the POST; an overlap blocks data even after successful authentication.
            guard()
            self._session = session
            self.successes = 1
            outcome = "AUTHENTICATED_DATA_PERMISSION_UNVERIFIED"
        except Exception:
            self.failures = 1
            raise live.Blocked("oauth_failed_or_conflict_no_retry") from None
        finally:
            self._append(ledger, dict(event="authentication_finished", sequence=1,
                completed_at=self.now().isoformat(), http_status=status, outcome=outcome,
                server_outcome_unknown=self.outcome_unknown))
        return self._session

    def metrics(self):
        return dict(physical_attempts=self.attempts, successes=self.successes, failures=self.failures,
                    outcome_unknown=self.outcome_unknown, memory_reuses=self.reuses, retries=0,
                    persistence="MEMORY_ONLY_NO_CROSS_RUN_TOKEN_CACHE")
