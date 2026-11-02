"""KIS stage-3: two read-only GET APIs, one fixed six-request trial, no token issuance.

Only execute() may use HTTP. Preflight is a recent operator/runtime evidence record,
not an authorization grant or a cross-runner lock. Missing evidence blocks all I/O.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, time as daytime
from decimal import Decimal, InvalidOperation
import gzip
import json
import os
from pathlib import Path
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kis_shared.boundary import install as _install_account_boundary
_install_account_boundary()
import kis_probe as offline

ROOT = Path(__file__).resolve().parent
TRIAL_ROOT = ROOT / "initial-six-call-trial"
TRIAL_ID = "stage3-initial-three-symbols-six-gets-v1"
BASE = "https://openapi.koreainvestment.com:9443"
HOST = "openapi.koreainvestment.com"
KINDS = ("quote", "estimate")
TASKS = tuple((kind, symbol) for symbol in offline.SYMBOLS for kind in KINDS)
MAX_CALLS = 6
MAX_SECONDS = 300
TIMEOUT_SECONDS = 20
MAX_BYTES = 2_000_000
REQUIRED_EVIDENCE = (
    "network_policy_current_and_permits_kis", "credentials_ready_in_selected_runtime",
    "token_app_key_binding_verified", "token_reuse_policy_verified",
    "account_specific_rate_policy_verified", "existing_jobs_and_schedules_reviewed",
    "exclusive_window_without_existing_changes_verified", "trading_day_verified",
    "regular_session_hours_verified",
)


class Blocked(offline.ProbeError):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def write_new(path: Path, raw: bytes):
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def policy_allows_kis(path=Path("/etc/codex/network-policy.json")):
    try:
        value = offline.strict_json(path.read_bytes())
        policy = value["http_network_policy"]
        if value.get("version") != 1:
            return False
        if policy.get("type") == "unrestricted":
            return True
        return policy.get("type") == "restricted" and any(
            rule.get("host") == HOST for rule in policy.get("egress_rules", []))
    except (OSError, ValueError, KeyError, TypeError, offline.ProbeError):
        return False


def credential_presence(env=None):
    env = os.environ if env is None else env
    return {name: bool(env.get(name)) for name in
            ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCESS_TOKEN", "KIS_ACCESS_TOKEN_EXPIRES_AT")}


def preflight_reasons(evidence, now, *, env=None, network_allowed=False):
    reasons = []
    if not isinstance(evidence, dict):
        return ["preflight_schema_missing"]
    if evidence.get("trial_id") != TRIAL_ID or evidence.get("data_call_cap") != 6:
        reasons.append("trial_identity_or_cap_not_verified")
    if evidence.get("symbols") != list(offline.SYMBOLS) or evidence.get("api_ids") != [
            offline.SPECS[k][1] for k in KINDS]:
        reasons.append("preflight_request_scope_mismatch")
    for key in REQUIRED_EVIDENCE:
        if evidence.get(key) is not True:
            reasons.append(key + "_missing")
    try:
        checked, expires = offline.timestamp(evidence["checked_at"]), offline.timestamp(evidence["expires_at"])
        if not timedelta(0) <= now - checked <= timedelta(minutes=5) or not now < expires <= checked + timedelta(minutes=5):
            reasons.append("preflight_expired_or_clock_invalid")
    except (KeyError, TypeError, offline.ProbeError):
        reasons.append("preflight_times_missing")
    try:
        start, end = offline.timestamp(evidence["window_start"]), offline.timestamp(evidence["window_end"])
        if not start <= now < end or end - start > timedelta(minutes=5):
            reasons.append("outside_exclusive_trial_window")
    except (KeyError, TypeError, offline.ProbeError):
        reasons.append("exclusive_window_missing")
    local = now.astimezone(offline.KST)
    if evidence.get("trade_date") != local.date().isoformat() or not daytime(9) <= local.time() < daytime(15, 30):
        reasons.append("outside_verified_current_regular_session")
    try:
        rate = Decimal(str(evidence["verified_account_requests_per_second"]))
        interval = Decimal(str(evidence["minimum_interval_seconds"]))
        if not rate.is_finite() or not interval.is_finite() or not 0 < rate <= 18 or not 2 <= interval <= 60 or interval < 1 / rate:
            reasons.append("unsafe_or_unverified_rate")
    except (KeyError, InvalidOperation, ValueError, ZeroDivisionError):
        reasons.append("account_rate_evidence_missing")
    if not network_allowed:
        reasons.append("local_network_policy_does_not_permit_kis")
    presence = credential_presence(env)
    reasons.extend(name + "_missing" for name, present in presence.items() if not present)
    env = os.environ if env is None else env
    if presence["KIS_ACCESS_TOKEN_EXPIRES_AT"]:
        try:
            if offline.timestamp(env["KIS_ACCESS_TOKEN_EXPIRES_AT"]) <= now + timedelta(seconds=MAX_SECONDS + 30):
                reasons.append("reusable_token_expiry_not_safe")
        except offline.ProbeError:
            reasons.append("reusable_token_expiry_not_verified")
    return sorted(set(reasons))


@dataclass(frozen=True)
class Receipt:
    status: int
    raw: bytes
    body_complete: bool


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpAdapter:
    """One opener.open per send: no retries, redirects, auth, pagination or orders."""

    def __init__(self, credentials, *, guard, opener=None, progress=None):
        self.credentials, self.guard, self.progress = credentials, guard, progress
        self._shared_required = opener is None
        self.dispatched = set()
        # Keep inherited proxy and default CA trust. Never unset proxy or disable TLS.
        self.opener = opener or urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def send(self, task, before_open):
        if task.kind not in KINDS or task.symbol not in offline.SYMBOLS:
            raise Blocked("request_not_in_six_call_scope")
        task = offline.Request(task.kind, task.symbol, task.session_date, dict(task.params))
        identity = (task.kind, task.symbol)
        if identity in self.dispatched or len(self.dispatched) >= MAX_CALLS:
            raise Blocked("adapter_duplicate_or_call_cap")
        self.guard()
        key, secret, token = self.credentials.app_key, self.credentials.app_secret, self.credentials.access_token
        if not all(isinstance(x, str) and x and x.isascii() and not any(ord(c) < 32 or ord(c) == 127 for c in x)
                   for x in (key, secret, token)):
            raise Blocked("credential_header_invalid")
        endpoint, tr_id = offline.SPECS[task.kind]
        url = BASE + endpoint + "?" + urllib.parse.urlencode(task.params)
        req = urllib.request.Request(url, method="GET", headers={
            "Content-Type": "application/json", "authorization": "Bearer " + token,
            "appkey": key, "appsecret": secret, "custtype": "P", "tr_id": tr_id,
        })
        # Durable reservation must succeed BEFORE the only possible network operation.
        if self._shared_required:
            from kis_shared.admission import check_live_admission
            check_live_admission()
            from kis_shared.runtime import current
            shared = current()
            if hasattr(shared, "validate_request"):
                shared.validate_request(req)
            shared.before_http(interval=2)
        before_open()
        self.dispatched.add(identity)
        if self.progress:
            self.progress("actual_http_dispatch_attempt", kind=task.kind, symbol=task.symbol)
        try:
            response = self.opener.open(req, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as error:
            response = error  # Retain error response body without logging URL/messages.
        except Exception:
            raise offline.ProbeError("http_transport_failed") from None
        try:
            with response:
                raw = response.read(MAX_BYTES + 1)
                status = response.status if hasattr(response, "status") else response.code
            if self._shared_required:
                from kis_shared.runtime import current
                current().http_response_closed()
            if not isinstance(raw, bytes):
                raise offline.ProbeError("invalid_response_bytes")
            if self.progress:
                self.progress("http_response_received", http_status=status, body_complete=len(raw)<=MAX_BYTES)
            return Receipt(status, raw, len(raw) <= MAX_BYTES)
        except Exception:
            raise offline.ProbeError("http_body_read_failed") from None


class LiveEvidence:
    def __init__(self, root, credentials):
        root = Path(root).resolve()
        if root == offline.PROTECTED or offline.PROTECTED in root.parents or root in offline.PROTECTED.parents:
            raise Blocked("protected_repository_destination")
        root.mkdir(parents=True, exist_ok=False)
        os.chmod(root, 0o700)
        (root / "raw").mkdir()
        (root / "observations").mkdir()
        write_new(root / "requests.jsonl", b"")
        self.root, self.secrets = root, credentials.secrets()

    def check(self, raw):
        if any(secret and secret in raw for secret in self.secrets):
            raise offline.ProbeError("credential_echo_preservation_blocked")

    def append(self, entry):
        raw = offline.encoded(entry) + b"\n"
        self.check(raw)
        with (self.root / "requests.jsonl").open("ab") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

    def preserve(self, receipt, metadata):
        self.check(receipt.raw)
        h = offline.digest(receipt.raw)
        path = self.root / "raw" / (h + ".json.gz")
        if path.exists():
            if gzip.decompress(path.read_bytes()) != receipt.raw:
                raise offline.ProbeError("existing_original_corrupt")
        else:
            write_new(path, gzip.compress(receipt.raw, mtime=0))
        entry = dict(metadata, source_sha256=h, source_bytes=len(receipt.raw),
                     body_complete=receipt.body_complete,
                     source_hash_scope="complete_body" if receipt.body_complete else "received_prefix_only",
                     mode="LIVE", provider_finality="UNVERIFIED")
        blob = offline.encoded(entry)
        self.check(blob)
        write_new(self.root / "observations" / (offline.digest(blob) + ".json"), blob)
        return entry

    def seal(self, report):
        blob = offline.encoded(report)
        self.check(blob)
        write_new(self.root / "result.json", blob)
        files = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                raw = path.read_bytes()
                files.append(dict(path=path.relative_to(self.root).as_posix(),
                                  bytes=len(raw), sha256=offline.digest(raw)))
        raw = offline.encoded(dict(schema=1, mode="LIVE", trial_id=TRIAL_ID, files=files))
        write_new(self.root / "manifest.json", raw)
        write_new(self.root / "manifest.sha256", (offline.digest(raw) + "\n").encode())


def blank_results(status="NOT_CALLED"):
    return [dict(symbol=symbol, name=name, quote=dict(status=status, values=None),
                 estimate=dict(status=status, values=None)) for symbol, name in offline.SYMBOLS.items()]


def blocked_report(reasons, now=None):
    return dict(status="BLOCKED", trial_id=TRIAL_ID, checked_at=(now or utcnow()).isoformat(),
                reasons=sorted(set(reasons)), physical_http_attempts=0, authentication_attempts=0,
                data_successes=0, data_failures=0, skipped_data_calls=6,
                collection_elapsed_seconds=None, collection_started_at=None,
                collection_completed_at=None, symbols=blank_results("NOT_CALLED_BLOCKED"),
                source_objects=[], original_store_created=False,
                provider_finality="UNVERIFIED", existing_collector_impact="NO_KIS_CALLS_OR_EXISTING_MUTATIONS")


def collect_six(evidence, *, root, env=None, network_allowed=False, opener=None,
                now=utcnow, monotonic=time.monotonic, sleep=time.sleep, external_guard=None):
    """Production CLI fixes root to TRIAL_ROOT; root/opener/clock injection is for tests."""
    env = os.environ if env is None else env
    reasons = preflight_reasons(evidence, now(), env=env, network_allowed=network_allowed)
    if reasons:
        return blocked_report(reasons, now())
    credentials = offline.Credentials.from_env(env)
    # Environment cannot cause token issuance. A verified pre-existing token is required.
    try:
        store = LiveEvidence(root, credentials)
    except FileExistsError:
        return blocked_report(["trial_already_claimed_no_rerun"], now())
    except (OSError, Blocked):
        return blocked_report(["evidence_store_unavailable_or_protected"], now())
    start, started_at = monotonic(), now().isoformat()
    report = dict(status="PARTIAL", trial_id=TRIAL_ID, physical_http_attempts=0,
                  authentication_attempts=0, data_successes=0, data_failures=0,
                  skipped_data_calls=6, collection_started_at=started_at,
                  symbols=blank_results(), source_objects=[], provider_finality="UNVERIFIED",
                  interpretation="Intraday estimates; unit/sign/source-time/venue meaning unverified",
                  existing_collector_impact="EXCLUSIVE_WINDOW_ATTESTED_NOT_INDEPENDENTLY_PROVEN")
    used, last, seen = 0, None, set()

    def guard():
        if external_guard is not None:
            external_guard()
        reasons = preflight_reasons(evidence, now(), env=env, network_allowed=network_allowed)
        if reasons:
            raise Blocked("preflight_no_longer_valid")
        if (store.root / "STOP").exists():
            raise Blocked("operator_stop")
        if used >= MAX_CALLS or monotonic() - start >= MAX_SECONDS:
            raise Blocked("trial_budget_exhausted")

    adapter = HttpAdapter(credentials, guard=guard, opener=opener)
    symbol_results = {item["symbol"]: item for item in report["symbols"]}
    for kind, symbol in TASKS:
        task = offline.request(kind, symbol, evidence["trade_date"])
        record = symbol_results[symbol][kind]
        attempted = False
        started = now().isoformat()
        tick = monotonic()
        sequence = used + 1
        try:
            guard()
            if last is not None:
                sleep(max(0.0, float(evidence["minimum_interval_seconds"]) - (monotonic() - last)))
            guard()
            started, tick = now().isoformat(), monotonic()

            def reserve():
                nonlocal used, last, attempted
                guard()
                identity = (kind, symbol)
                if identity in seen:
                    raise Blocked("duplicate_data_query_forbidden")
                store.append(dict(event="attempt_started", sequence=sequence, trial_id=TRIAL_ID,
                                  **task.metadata(), started_at=started))
                used += 1
                seen.add(identity)
                last, attempted = monotonic(), True

            receipt = adapter.send(task, reserve)
            entry = store.preserve(receipt, dict(task.metadata(), sequence=sequence,
                    started_at=started, received_at=now().isoformat(), elapsed_seconds=monotonic() - tick,
                    http_status=receipt.status))
            # Read after write and verify uncompressed original, before trusting parsing.
            original = gzip.decompress((store.root / "raw" / (entry["source_sha256"] + ".json.gz")).read_bytes())
            if offline.digest(original) != entry["source_sha256"] or original != receipt.raw:
                raise offline.ProbeError("source_roundtrip_failed")
            validation = offline.validate(original, task, receipt.status)
            valid = receipt.body_complete and validation["status"] == "STRUCTURE_CHECKED_ONLY"
            record.update(status="RECEIVED_STRUCTURE_CHECKED" if valid else "FAIL", values=validation["values"],
                          observation=entry, validation=validation, raw_bytes_verified=True)
            report["source_objects"].append(entry)
            store.append(dict(event="attempt_finished", sequence=sequence,
                              ended_at=now().isoformat(), http_status=receipt.status,
                              status="structure_checked" if valid else "response_rejected",
                              source_sha256=entry["source_sha256"], body_complete=receipt.body_complete))
            if not valid:
                report["data_failures"] += 1
                report["status"] = "FAIL"
                break
            report["data_successes"] += 1
        except Blocked as error:
            record.update(status="NOT_CALLED_BLOCKED" if not attempted else "FAIL", reason=str(error))
            report.update(status="PARTIAL" if report["data_successes"] else "BLOCKED", stop_reason=str(error))
            if attempted:
                report["data_failures"] += 1
                report["status"] = "FAIL"
                try:
                    store.append(dict(event="attempt_finished", sequence=sequence, ended_at=now().isoformat(),
                                      status="blocked_after_dispatch"))
                except Exception:
                    report["ledger_finish_write_failed"] = True
            break
        except Exception:
            record.update(status="FAIL" if attempted else "NOT_CALLED_BLOCKED",
                          reason="transport_or_preservation_or_validation_failed")
            report["data_failures"] += int(attempted)
            report.update(status="FAIL" if attempted else ("PARTIAL" if report["data_successes"] else "BLOCKED"),
                          stop_reason="transport_or_preservation_or_validation_failed")
            if attempted:
                try:
                    store.append(dict(event="attempt_finished", sequence=sequence, ended_at=now().isoformat(),
                                      status="failed_no_retry"))
                except Exception:
                    report["ledger_finish_write_failed"] = True
            break
    report.update(physical_http_attempts=used, skipped_data_calls=6 - used,
                  collection_completed_at=now().isoformat(), collection_elapsed_seconds=monotonic() - start,
                  original_store_created=True, source_store=str(store.root),
                  origin_received_request_count="UNVERIFIED_ON_TRANSPORT_ERRORS")
    try:
        store.seal(report)
    except Exception:
        report.update(status="FAIL", evidence_seal_status="FAILED",
                      stop_reason="evidence_seal_failed_counts_retained")
    return report


def verify_trial(root, expected_manifest_sha256=None):
    root = Path(root).resolve()
    raw = (root / "manifest.json").read_bytes()
    h = offline.digest(raw)
    if h != (root / "manifest.sha256").read_text().strip() or expected_manifest_sha256 is not None and h != expected_manifest_sha256:
        raise offline.ProbeError("manifest_hash_mismatch")
    manifest = offline.strict_json(raw)
    if manifest.get("trial_id") != TRIAL_ID or manifest.get("mode") != "LIVE":
        raise offline.ProbeError("wrong_trial_manifest")
    paths = {"manifest.json", "manifest.sha256"}
    for entry in manifest["files"]:
        name = entry["path"]
        path = root / name
        if name in paths or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise offline.ProbeError("unsafe_manifest_path")
        paths.add(name)
        value = path.read_bytes()
        if len(value) != entry["bytes"] or offline.digest(value) != entry["sha256"]:
            raise offline.ProbeError("file_hash_mismatch")
    if {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} != paths:
        raise offline.ProbeError("inventory_mismatch")
    ledger_raw = (root / "requests.jsonl").read_bytes()
    if ledger_raw and not ledger_raw.endswith(b"\n"):
        raise offline.ProbeError("incomplete_ledger")
    events = [offline.strict_json(line) for line in ledger_raw.splitlines()]
    starts = [e for e in events if e["event"] == "attempt_started"]
    ends = [e for e in events if e["event"] == "attempt_finished"]
    ids = [(e["kind"], e["symbol"]) for e in starts]
    sequence = list(range(1, len(starts) + 1))
    if (len(starts) > 6 or len(ids) != len(set(ids)) or not set(ids) <= set(TASKS)
            or [e["sequence"] for e in starts] != sequence
            or [e["sequence"] for e in ends] != sequence or len(events) != len(starts) * 2):
        raise offline.ProbeError("request_ledger_mismatch")
    result = offline.strict_json((root / "result.json").read_bytes())
    if result["physical_http_attempts"] != len(starts) or result["authentication_attempts"] != 0:
        raise offline.ProbeError("call_counts_mismatch")
    if (result["skipped_data_calls"] != 6 - len(starts)
            or result["data_successes"] + result["data_failures"] != len(starts)):
        raise offline.ProbeError("result_counts_mismatch")
    observations = []
    for path in (root / "observations").glob("*.json"):
        value = path.read_bytes()
        if path.stem != offline.digest(value):
            raise offline.ProbeError("metadata_hash_mismatch")
        entry = offline.strict_json(value)
        sh = entry["source_sha256"]
        if not isinstance(sh, str) or len(sh) != 64 or any(c not in "0123456789abcdef" for c in sh):
            raise offline.ProbeError("invalid_source_hash")
        with gzip.open(root / "raw" / (sh + ".json.gz"), "rb") as handle:
            original = handle.read(MAX_BYTES + 2)
        if offline.digest(original) != sh or len(original) != entry["source_bytes"]:
            raise offline.ProbeError("source_hash_mismatch")
        task = offline.request(entry["kind"], entry["symbol"], entry["requested_date"])
        if task.kind not in KINDS or any(entry[key] != val for key, val in task.metadata().items()):
            raise offline.ProbeError("request_metadata_mismatch")
        start = next((s for s in starts if s["sequence"] == entry["sequence"]), None)
        if not start or any(start[key] != entry[key] for key in task.metadata()) or start["started_at"] != entry["started_at"]:
            raise offline.ProbeError("observation_ledger_mismatch")
        record = next(s for s in result["symbols"] if s["symbol"] == entry["symbol"])[entry["kind"]]
        if record.get("observation") != entry or record["validation"] != offline.validate(original, task, entry["http_status"]):
            raise offline.ProbeError("result_reparse_mismatch")
        observations.append(entry)
    records = [r[k] for r in result["symbols"] for k in KINDS]
    if (len(result["symbols"]) != 3 or {r["symbol"] for r in result["symbols"]} != set(offline.SYMBOLS)
            or sum(r["status"] == "RECEIVED_STRUCTURE_CHECKED" for r in records) != result["data_successes"]):
        raise offline.ProbeError("symbol_result_counts_mismatch")
    if sorted(offline.encoded(e) for e in observations) != sorted(offline.encoded(e) for e in result["source_objects"]):
        raise offline.ProbeError("source_inventory_mismatch")
    return dict(status="INTEGRITY_VERIFIED_ONLY", physical_http_attempts=len(starts),
                authentication_attempts=0, response_objects=len(observations), manifest_sha256=h,
                provider_finality="UNVERIFIED", data_meaning_verified=False)


def read_preflight(path, expected_hash):
    if not expected_hash:
        raise Blocked("independent_preflight_hash_required")
    try:
        raw = Path(path).read_bytes()
        if offline.digest(raw) != expected_hash:
            raise Blocked("preflight_hash_mismatch")
        return offline.strict_json(raw)
    except (OSError, ValueError, TypeError, offline.ProbeError):
        raise Blocked("preflight_read_or_hash_failed") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--preflight", type=Path, required=True)
    run.add_argument("--preflight-sha256", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--root", type=Path, default=TRIAL_ROOT)
    verify.add_argument("--manifest-sha256")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            report = verify_trial(args.root, args.manifest_sha256)
        else:
            evidence = read_preflight(args.preflight, args.preflight_sha256)
            report = collect_six(evidence, root=TRIAL_ROOT, network_allowed=policy_allows_kis())
        print(offline.encoded(report).decode())
        return 0 if report["status"] in ("PASS", "INTEGRITY_VERIFIED_ONLY") else 2
    except Blocked as error:
        print(offline.encoded(blocked_report([str(error)])).decode())
        return 2
    except Exception:
        # An unexpected post-dispatch persistence failure must never be reported as zero calls.
        print(offline.encoded(dict(status="FAIL", reason="local_operation_failed_counts_require_ledger_review",
                                   physical_http_attempts=None, authentication_attempts=0)).decode())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
