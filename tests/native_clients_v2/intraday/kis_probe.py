"""Stage-2 offline-only KIS evidence harness. Python 3.12+, standard library only.

There is deliberately no HTTP adapter, scheduler, repository import or live command.
The client is transport-injected; production transport requires a separate live stage.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SYMBOLS = {"000720": "현대건설", "017670": "SK텔레콤", "052690": "한전기술"}
SPECS = {
    "quote": ("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100"),
    "estimate": ("/uapi/domestic-stock/v1/quotations/investor-trend-estimate", "HHPTJ04160200"),
    "daily": ("/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily", "FHPTJ04160001"),
}
AUTH = "/oauth2/tokenP"
WINDOWS = ("09:30", "10:00", "11:20", "13:20", "14:30")
ESTIMATE_FIELDS = ("frgn_fake_ntby_qty", "orgn_fake_ntby_qty", "sum_fake_ntby_qty")
DAILY_FIELDS = ("frgn_ntby_qty", "orgn_ntby_qty")
MAX_BYTES = 2_000_000
PROTECTED = Path("/workspace/qweasd").resolve()


class ProbeError(Exception):
    """Public errors contain fixed codes, never provider messages or credentials."""


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProbeError("duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise ProbeError("non_finite_json")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ProbeError("invalid_json") from exc


def timestamp(text: str) -> datetime:
    try:
        result = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ProbeError("invalid_timestamp") from exc
    if result.tzinfo is None:
        raise ProbeError("naive_timestamp")
    return result


def day(text: str) -> str:
    try:
        if date.fromisoformat(text).isoformat() != text:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ProbeError("invalid_date") from exc
    return text


def number(raw, *, integer=False, nonnegative=False, positive=False) -> str:
    # Do not strip commas, substitute zero, infer a multiplier, or accept bool/float.
    if not isinstance(raw, str) or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw):
        raise ProbeError("invalid_numeric_field")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ProbeError("invalid_numeric_field") from exc
    if not value.is_finite() or integer and value != value.to_integral_value():
        raise ProbeError("invalid_numeric_field")
    if nonnegative and value < 0 or positive and value <= 0:
        raise ProbeError("invalid_numeric_range")
    return str(value)


@dataclass(frozen=True)
class Request:
    kind: str
    symbol: str
    session_date: str
    params: dict[str, str]

    def __post_init__(self):
        if self.kind not in SPECS or self.symbol not in SYMBOLS:
            raise ProbeError("request_outside_allowlist")
        day(self.session_date)
        expected = request_params(self.kind, self.symbol, self.session_date)
        if self.params != expected:
            raise ProbeError("unapproved_request_parameters")

    def metadata(self):
        path, tr_id = SPECS[self.kind]
        return dict(kind=self.kind, symbol=self.symbol, requested_date=self.session_date,
                    endpoint=path, tr_id=tr_id, params=dict(self.params),
                    query_scope="UNVERIFIED" if self.kind == "estimate" else "KRX_J")


def request_params(kind: str, symbol: str, session_date: str) -> dict[str, str]:
    if kind == "estimate":
        return {"MKSC_SHRN_ISCD": symbol}
    result = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
    if kind == "daily":
        result.update(FID_INPUT_DATE_1=session_date.replace("-", ""),
                      FID_ORG_ADJ_PRC="", FID_ETC_CLS_CODE="")
    return result


def request(kind: str, symbol: str, session_date: str) -> Request:
    return Request(kind, symbol, session_date, request_params(kind, symbol, session_date))


@dataclass(repr=False)
class Credentials:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    access_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        if any(not env.get(name) for name in ("KIS_APP_KEY", "KIS_APP_SECRET")):
            raise ProbeError("missing_credentials")
        return cls(env["KIS_APP_KEY"], env["KIS_APP_SECRET"], env.get("KIS_ACCESS_TOKEN"))

    def secrets(self):
        return tuple(x.encode() for x in (self.app_key, self.app_secret, self.access_token) if x)


@dataclass(frozen=True)
class Response:
    status: int
    raw: bytes
    # The offline adapter cannot provide live evidence.
    synthetic: bool = True


class NetworkDisabledTransport:
    def send(self, **_):
        raise ProbeError("stage2_live_disabled")


class FixtureTransport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def send(self, **_):
        self.calls += 1
        try:
            result = next(self.responses)
        except StopIteration as exc:
            raise ProbeError("fixture_exhausted") from exc
        if isinstance(result, Exception):
            raise result
        if not isinstance(result, Response) or not result.synthetic:
            raise ProbeError("synthetic_response_required")
        return result


def exclusive(path: Path, raw: bytes):
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


class EvidenceStore:
    def __init__(self, root: Path, secrets=()):
        root = root.resolve()
        code_root = Path(__file__).resolve().parent
        if root == PROTECTED or PROTECTED in root.parents:
            raise ProbeError("protected_repository_destination")
        if root == code_root or root in code_root.parents:
            raise ProbeError("unsafe_output_destination")
        root.mkdir(parents=True, exist_ok=False)
        (root / "raw").mkdir()
        (root / "observations").mkdir()
        self.root, self.secrets = root, secrets

    def check_secrets(self, raw: bytes):
        # Reject rather than alter the original bytes or silently redact evidence.
        if any(value and value in raw for value in self.secrets):
            raise ProbeError("credential_echo_rejected")

    def journal(self, event: dict):
        raw = encoded(event) + b"\n"
        self.check_secrets(raw)
        with (self.root / "requests.jsonl").open("ab") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

    def preserve(self, raw: bytes, metadata: dict):
        self.check_secrets(raw)
        h = digest(raw)
        path = self.root / "raw" / (h + ".json.gz")
        if path.exists():
            if gzip.decompress(path.read_bytes()) != raw:
                raise ProbeError("existing_raw_corrupt")
        else:
            exclusive(path, gzip.compress(raw, mtime=0))
        observation = dict(metadata, source_sha256=h, source_bytes=len(raw),
                           mode="SYNTHETIC", live_http_calls=0)
        blob = encoded(observation)
        self.check_secrets(blob)
        name = digest(blob) + ".json"
        exclusive(self.root / "observations" / name, blob)
        return observation

    def seal(self, report: dict):
        blob = encoded(report)
        self.check_secrets(blob)
        exclusive(self.root / "report.json", blob)
        files = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                raw = path.read_bytes()
                self.check_secrets(raw) if path.suffix != ".gz" else None
                files.append(dict(path=path.relative_to(self.root).as_posix(),
                                  sha256=digest(raw), bytes=len(raw)))
        manifest = encoded(dict(schema=1, mode="SYNTHETIC", live_http_calls=0,
                                files=files, status="SEALED_OFFLINE_ONLY"))
        exclusive(self.root / "manifest.json", manifest)
        exclusive(self.root / "manifest.sha256", (digest(manifest) + "\n").encode())


class Client:
    def __init__(self, store: EvidenceStore, transport, credentials: Credentials,
                 *, max_calls=97, interval=2.0, max_seconds=8 * 3600,
                 monotonic: Callable = time.monotonic, sleep: Callable = time.sleep,
                 utcnow: Callable = lambda: datetime.now(timezone.utc)):
        if type(max_calls) is not int or not 1 <= max_calls <= 97:
            raise ProbeError("invalid_call_budget")
        if not 2.0 <= interval <= 60 or not 1 <= max_seconds <= 8 * 3600:
            raise ProbeError("invalid_time_budget")
        self.store, self.transport, self.credentials = store, transport, credentials
        self.max_calls, self.interval, self.max_seconds = max_calls, interval, max_seconds
        self.monotonic, self.sleep, self.utcnow = monotonic, sleep, utcnow
        self.started, self.last, self.used = monotonic(), None, 0
        self.token = credentials.access_token
        store.secrets = credentials.secrets()

    def check(self):
        if (self.store.root / "STOP").exists():
            raise ProbeError("operator_stop")
        if self.used >= self.max_calls:
            raise ProbeError("call_budget_exhausted")
        if self.monotonic() - self.started >= self.max_seconds:
            raise ProbeError("time_budget_exhausted")

    def exchange(self, endpoint, tr_id=None, params=None, body=None):
        if endpoint not in {AUTH, *(s[0] for s in SPECS.values())}:
            raise ProbeError("endpoint_outside_allowlist")
        self.check()
        if self.last is not None:
            self.sleep(max(0.0, self.interval - (self.monotonic() - self.last)))
        self.check()
        self.last = self.monotonic()
        self.used += 1
        sequence = self.used
        started = self.utcnow().isoformat()
        self.store.journal(dict(event="started", sequence=sequence, endpoint=endpoint,
                                started_at=started, mode="SYNTHETIC"))
        headers = {"Content-Type": "application/json"}
        if tr_id:
            headers.update(authorization="Bearer " + self.token,
                           appkey=self.credentials.app_key, appsecret=self.credentials.app_secret,
                           custtype="P", tr_id=tr_id)
        tick = self.monotonic()
        try:
            response = self.transport.send(endpoint=endpoint, headers=headers,
                                           params=params or {}, body=body)
            if not isinstance(response, Response) or not response.synthetic:
                raise ProbeError("synthetic_response_required")
            if len(response.raw) > MAX_BYTES:
                raise ProbeError("response_too_large")
        except Exception:
            self.store.journal(dict(event="finished", sequence=sequence,
                                    ended_at=self.utcnow().isoformat(), status="transport_rejected",
                                    elapsed_seconds=self.monotonic() - tick))
            raise ProbeError("transport_rejected") from None
        end = self.utcnow().isoformat()
        duration = self.monotonic() - tick
        self.store.journal(dict(event="finished", sequence=sequence, ended_at=end,
                                http_status=response.status, elapsed_seconds=duration))
        return response, dict(sequence=sequence, started_at=started, received_at=end,
                              elapsed_seconds=duration, http_status=response.status)

    def authenticate(self):
        if self.token:
            return
        # No persistence of authentication response, headers, credentials or token.
        response, _ = self.exchange(AUTH, body=encoded(dict(grant_type="client_credentials",
            appkey=self.credentials.app_key, appsecret=self.credentials.app_secret)))
        try:
            value = strict_json(response.raw)
            token = value.get("access_token") if isinstance(value, dict) else None
            if response.status != 200 or not isinstance(token, str) or not token:
                raise ProbeError("authentication_rejected")
        except ProbeError:
            raise ProbeError("authentication_rejected") from None
        self.token = token
        self.store.secrets += (token.encode(),)

    def collect(self, task: Request):
        # Revalidate mutable params even after Request construction.
        Request(task.kind, task.symbol, task.session_date, dict(task.params))
        self.authenticate()
        endpoint, tr_id = SPECS[task.kind]
        response, timing = self.exchange(endpoint, tr_id, task.params)
        observation = self.store.preserve(response.raw, dict(task.metadata(), **timing))
        result = validate(response.raw, task, response.status)
        return dict(observation=observation, validation=result)


def validate(raw: bytes, task: Request, http_status=200) -> dict:
    base = dict(status="HELD", findings=[], values=None, provider_finality="UNVERIFIED",
                unit_status="DOCUMENT_REFERENCE_ONLY_LIVE_UNVERIFIED",
                market_scope="UNVERIFIED" if task.kind == "estimate" else "REQUEST_J_NOT_INDEPENDENTLY_VERIFIED",
                source_updated_at=None, freshness_status="SOURCE_TIME_UNVERIFIED",
                trade_date_verified=False)
    if http_status != 200:
        return dict(base, findings=["http_error_no_automatic_retry"])
    try:
        payload = strict_json(raw)
        if not isinstance(payload, dict) or payload.get("rt_cd") != "0":
            raise ProbeError("provider_response_rejected")
        if task.kind == "quote":
            row = payload.get("output")
            if not isinstance(row, dict):
                raise ProbeError("quote_schema")
            if row.get("stck_shrn_iscd") != task.symbol:
                raise ProbeError("symbol_missing_or_mismatch")
            values = {"stck_prpr": number(row.get("stck_prpr"), positive=True),
                      "acml_vol": number(row.get("acml_vol"), integer=True, nonnegative=True),
                      "acml_tr_pbmn": number(row.get("acml_tr_pbmn"), nonnegative=True)}
            base["raw_status"] = {key: row.get(key) for key in ("temp_stop_yn", "iscd_stat_cls_code")}
        else:
            rows = payload.get("output2")
            if not isinstance(rows, list):
                raise ProbeError("rows_schema")
            if not rows:
                raise ProbeError("empty_rows_not_zero")
            if not all(isinstance(row, dict) for row in rows):
                raise ProbeError("row_schema")
            if task.kind == "estimate":
                values, seen = [], set()
                for row in rows:
                    label = row.get("bsop_hour_gb")
                    if not isinstance(label, str) or not label or label in seen:
                        raise ProbeError("missing_or_duplicate_input_group")
                    seen.add(label)
                    # Keep ALL input groups. Their order/time mapping is not established.
                    values.append(dict(bsop_hour_gb=label, **{key: number(row.get(key), integer=True)
                                                           for key in ESTIMATE_FIELDS}))
            else:
                target = task.session_date.replace("-", "")
                seen = set()
                for row in rows:
                    label = row.get("stck_bsop_date")
                    if not isinstance(label, str) or not re.fullmatch(r"\d{8}", label):
                        raise ProbeError("daily_date_missing")
                    date.fromisoformat(label)
                    if label in seen or label > target:
                        raise ProbeError("duplicate_or_future_daily_date")
                    seen.add(label)
                matched = [row for row in rows if row["stck_bsop_date"] == target]
                if len(matched) != 1:
                    raise ProbeError("requested_daily_date_missing")
                row = matched[0]
                values = dict(stck_bsop_date=target, **{key: number(row.get(key), integer=True)
                                                       for key in DAILY_FIELDS})
                # No undocumented institution/foreign money multiplier.
                base["amounts_raw_unconverted"] = {key: row.get(key) for key in
                    ("frgn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn")}
                base["trade_date_verified"] = True
        return dict(base, status="STRUCTURE_CHECKED_ONLY", values=values,
                    findings=["accuracy_sign_unit_scope_and_update_time_require_live_evidence"])
    except (ProbeError, ValueError) as exc:
        code = str(exc) if isinstance(exc, ProbeError) else "invalid_calendar_date"
        return dict(base, findings=[code])


def compare(before: dict, after: dict) -> dict:
    a, b = before["observation"], after["observation"]
    identity = ("kind", "symbol", "requested_date", "query_scope", "params")
    if any(a.get(key) != b.get(key) for key in identity):
        return dict(status="NOT_COMPARABLE", reason="request_identity_or_scope_mismatch")
    ta, tb = timestamp(a["received_at"]), timestamp(b["received_at"])
    if ta >= tb:
        return dict(status="NOT_COMPARABLE", reason="non_increasing_observation_time")
    va, vb = before["validation"], after["validation"]
    if va["values"] is None or vb["values"] is None:
        return dict(status="HELD", reason="missing_or_invalid_input", deltas=None)
    common = dict(elapsed_seconds=(tb - ta).total_seconds(),
                  source_update_verified=False, provider_finality="UNVERIFIED")
    if va["values"] == vb["values"]:
        return dict(common, status="UNCHANGED_UPDATE_UNVERIFIED", deltas=None)
    if a["kind"] == "estimate":
        av = {r["bsop_hour_gb"]: r for r in va["values"]}
        bv = {r["bsop_hour_gb"]: r for r in vb["values"]}
        deltas = {label: {key: str(Decimal(bv[label][key]) - Decimal(av[label][key]))
                           for key in ESTIMATE_FIELDS} for label in sorted(av.keys() & bv.keys())}
        return dict(common, status="ESTIMATE_CHANGED_NOT_EXECUTED_FLOW", deltas=deltas,
                    added_input_groups=sorted(bv.keys() - av.keys()),
                    removed_input_groups=sorted(av.keys() - bv.keys()))
    keys = ("stck_prpr", "acml_vol", "acml_tr_pbmn") if a["kind"] == "quote" else DAILY_FIELDS
    deltas = {key: str(Decimal(vb["values"][key]) - Decimal(va["values"][key])) for key in keys}
    correction = a["kind"] == "quote" and any(Decimal(deltas[k]) < 0 for k in ("acml_vol", "acml_tr_pbmn"))
    return dict(common, status="CUMULATIVE_DECREASE_REVIEW" if correction else "OBSERVED_CHANGE_ONLY",
                deltas=deltas)


def daily_reference(estimate: dict, daily: dict) -> dict:
    # Numerical agreement cannot establish venue scope or official finality.
    a, b = estimate["observation"], daily["observation"]
    if a["kind"] != "estimate" or b["kind"] != "daily" or a["symbol"] != b["symbol"]:
        raise ProbeError("reference_identity_mismatch")
    return dict(status="REFERENCE_ONLY_NOT_EQUALITY_TEST",
                estimate=estimate["validation"]["values"], daily=daily["validation"]["values"],
                comparable_after_scope_unit_population_verification=[
                    ["frgn_fake_ntby_qty", "frgn_ntby_qty"],
                    ["orgn_fake_ntby_qty", "orgn_ntby_qty"]],
                reasons=["intraday_estimate_vs_daily_observation", "estimate_market_scope_unverified",
                         "foreign_and_institution_population_unverified", "money_not_derived_from_quantity"],
                provider_finality="UNVERIFIED")


def pair_observations(quote: dict, estimate: dict, max_skew_seconds=90) -> dict:
    a, b = quote["observation"], estimate["observation"]
    if (a["kind"], b["kind"]) != ("quote", "estimate") or any(
            a[key] != b[key] for key in ("symbol", "requested_date")):
        raise ProbeError("pair_identity_mismatch")
    skew = abs((timestamp(a["received_at"]) - timestamp(b["received_at"])).total_seconds())
    held = (skew > max_skew_seconds or quote["validation"]["values"] is None
            or estimate["validation"]["values"] is None)
    return dict(status="PAIR_HELD" if held else "RECEIPT_PAIR_ONLY",
                receipt_skew_seconds=skew, simultaneous_source_snapshot_verified=False,
                findings=["market_scope_alignment_unverified", "source_update_times_unverified"],
                threshold_note="90s is a test-design limit, not a provider SLA")


def plan(session_date: str) -> dict:
    day(session_date)
    slots = []
    for hhmm in WINDOWS:
        center = datetime.fromisoformat(session_date + "T" + hhmm + ":00").replace(tzinfo=KST)
        for offset in (-2, 2, 7):
            slots.append(dict(window=hhmm, offset_minutes=offset,
                              scheduled_at=(center + timedelta(minutes=offset)).isoformat(),
                              quote_calls=3, estimate_calls=3))
    return dict(mode="PLAN_ONLY_NOT_SCHEDULED", symbols=SYMBOLS, slots=slots,
                expected_market_calls=96, expected_auth_calls=1, expected_total=97,
                minimal_two_observation_total=67, per_slot_process_auth_total=113,
                retries=0, calendar_api_calls=0,
                daily_reference_calls=6, auth_assumption="single_token_valid_for_entire_trial",
                date_status="UNVERIFIED_TRADING_DAY", actual_live_http_calls=0,
                requirement="fresh provider policy, trading calendar, schedules and credential isolation preflight")


class FakeClock:
    def __init__(self, at: datetime):
        self.at, self.tick = at, 0.0

    def monotonic(self):
        return self.tick

    def now(self):
        return self.at

    def sleep(self, seconds):
        self.advance(seconds)

    def advance(self, seconds):
        self.tick += seconds
        self.at += timedelta(seconds=seconds)


def synthetic_response(kind, symbol, session_date, stage):
    if kind == "quote":
        body = dict(output=dict(stck_shrn_iscd=symbol, stck_prpr=str(10000 + stage),
                                acml_vol=str(100000 + 10 * stage),
                                acml_tr_pbmn=str(1000000000 + 100000 * stage), temp_stop_yn="N"))
    elif kind == "estimate":
        body = dict(output2=[dict(bsop_hour_gb="SYNTHETIC_GROUP_NOT_OFFICIAL_CODE",
                                 frgn_fake_ntby_qty=str(10 + stage),
                                 orgn_fake_ntby_qty=str(-20 - stage), sum_fake_ntby_qty="-10")])
    else:
        body = dict(output2=[dict(stck_bsop_date=session_date.replace("-", ""),
                                 frgn_ntby_qty="123", orgn_ntby_qty="-456",
                                 frgn_ntby_tr_pbmn="UNIT_UNVERIFIED",
                                 orgn_ntby_tr_pbmn="UNIT_UNVERIFIED")])
    return Response(200, encoded(dict(rt_cd="0", msg_cd="SYNTHETIC_ONLY", **body)))


def demo(destination: Path) -> dict:
    session_date, prior_date = "2026-10-28", "2026-10-27"  # labels, not real observations
    p = plan(session_date)
    tasks = [(request("daily", s, prior_date), 0, "before_session") for s in SYMBOLS]
    for i, slot in enumerate(p["slots"]):
        # T+2 and T+7 repeat deliberately; this does not prove a late provider update.
        stage = i // 3 + (0 if i % 3 == 0 else 1)
        for symbol in SYMBOLS:
            for kind in ("quote", "estimate"):
                tasks.append((request(kind, symbol, session_date), stage, slot["scheduled_at"]))
    tasks += [(request("daily", s, session_date), 0, "after_close_reference") for s in SYMBOLS]
    responses = [Response(200, encoded(dict(access_token="SYNTHETIC_TOKEN_NEVER_SAVED")))]
    responses += [synthetic_response(t.kind, t.symbol, t.session_date, stage) for t, stage, _ in tasks]
    credentials = Credentials("SYNTHETIC_KEY_NEVER_SAVED", "SYNTHETIC_SECRET_NEVER_SAVED")
    clock = FakeClock(datetime(2026, 10, 28, 9, 0, tzinfo=KST))
    store = EvidenceStore(destination)
    client = Client(store, FixtureTransport(responses), credentials, monotonic=clock.monotonic,
                    sleep=clock.sleep, utcnow=clock.now)
    records, comparisons, previous = [], [], {}
    for task, _, slot in tasks:
        if slot not in ("before_session", "after_close_reference"):
            target = timestamp(slot)
            if target > clock.at:
                clock.advance((target - clock.at).total_seconds())
        elif slot == "after_close_reference":
            target = datetime(2026, 10, 28, 16, 10, tzinfo=KST)
            clock.advance((target - clock.at).total_seconds()) if target > clock.at else None
        record = client.collect(task)
        record["slot"] = slot
        records.append(record)
        key = (task.kind, task.symbol, task.session_date)
        if key in previous:
            comparisons.append(dict(symbol=task.symbol, kind=task.kind,
                                    before=previous[key]["observation"]["sequence"],
                                    after=record["observation"]["sequence"],
                                    result=compare(previous[key], record)))
        previous[key] = record
    refs = [dict(symbol=s, **daily_reference(previous[("estimate", s, session_date)],
                         previous[("daily", s, session_date)])) for s in SYMBOLS]
    pairs = [dict(symbol=s, **pair_observations(previous[("quote", s, session_date)],
                          previous[("estimate", s, session_date)])) for s in SYMBOLS]
    report = dict(schema=1, mode="SYNTHETIC", provider_accuracy_verified=False,
                  live_http_calls=0, simulated_transport_calls=client.used,
                  synthetic_market_responses=len(records), records=records,
                  comparisons=comparisons, daily_references=refs, receipt_pairs=pairs,
                  elapsed_simulated_seconds=clock.tick,
                  interpretation="Only implementation behavior is tested; all market values are synthetic.")
    store.seal(report)
    return report


def verify(root: Path, expected_manifest_sha256: str | None = None) -> dict:
    root = root.resolve()
    raw = (root / "manifest.json").read_bytes()
    if digest(raw) != (root / "manifest.sha256").read_text().strip():
        raise ProbeError("manifest_hash_mismatch")
    if expected_manifest_sha256 is not None and digest(raw) != expected_manifest_sha256:
        raise ProbeError("trusted_manifest_hash_mismatch")
    manifest = strict_json(raw)
    if manifest.get("mode") != "SYNTHETIC" or manifest.get("live_http_calls") != 0:
        raise ProbeError("offline_manifest_required")
    expected = {"manifest.json", "manifest.sha256"}
    for item in manifest["files"]:
        name = item["path"]
        path = root / name
        if name in expected or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ProbeError("unsafe_inventory_path")
        expected.add(name)
        data = path.read_bytes()
        if len(data) != item["bytes"] or digest(data) != item["sha256"]:
            raise ProbeError("inventory_hash_mismatch")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != expected:
        raise ProbeError("unexpected_or_missing_files")
    observations = []
    for path in sorted((root / "observations").glob("*.json")):
        data = path.read_bytes()
        if path.stem != digest(data):
            raise ProbeError("observation_hash_mismatch")
        entry = strict_json(data)
        if entry.get("mode") != "SYNTHETIC" or entry.get("live_http_calls") != 0:
            raise ProbeError("observation_mode_mismatch")
        h = entry["source_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", h):
            raise ProbeError("invalid_source_hash")
        with gzip.open(root / "raw" / (h + ".json.gz"), "rb") as handle:
            original = handle.read(MAX_BYTES + 1)
        if digest(original) != h or len(original) != entry["source_bytes"] or len(original) > MAX_BYTES:
            raise ProbeError("raw_hash_mismatch")
        observations.append(entry)
    journal_raw = (root / "requests.jsonl").read_bytes()
    if not journal_raw.endswith(b"\n"):
        raise ProbeError("incomplete_journal")
    events = [strict_json(line) for line in journal_raw.splitlines()]
    starts = [e for e in events if e["event"] == "started"]
    ends = [e for e in events if e["event"] == "finished"]
    sequence = list(range(1, len(starts) + 1))
    if ([e["sequence"] for e in starts] != sequence or [e["sequence"] for e in ends] != sequence
            or len(events) != 2 * len(starts)):
        raise ProbeError("incomplete_request_accounting")
    report = strict_json((root / "report.json").read_bytes())
    if (report.get("mode") != "SYNTHETIC" or report.get("live_http_calls") != 0
            or report.get("provider_accuracy_verified") is not False):
        raise ProbeError("report_mode_or_accuracy_claim_rejected")
    observed_sequences = {o["sequence"] for o in observations}
    market_sequences = {e["sequence"] for e in starts if e["endpoint"] != AUTH}
    if (observed_sequences != market_sequences or len(observations) != len(market_sequences)
            or report["simulated_transport_calls"] != len(starts)
            or report["synthetic_market_responses"] != len(observations)):
        raise ProbeError("report_accounting_mismatch")
    report_entries = [record["observation"] for record in report["records"]]
    if (len(report_entries) != len(observations)
            or {o["sequence"] for o in report_entries} != observed_sequences):
        raise ProbeError("report_missing_or_duplicate_observations")
    observation_bytes = {encoded(o) for o in observations}
    starts_by_sequence = {e["sequence"]: e for e in starts}
    ends_by_sequence = {e["sequence"]: e for e in ends}
    for record in report["records"]:
        entry = record["observation"]
        if encoded(entry) not in observation_bytes:
            raise ProbeError("report_observation_mismatch")
        task = request(entry["kind"], entry["symbol"], entry["requested_date"])
        if any(entry[key] != value for key, value in task.metadata().items()):
            raise ProbeError("observation_request_binding_mismatch")
        start, end = starts_by_sequence[entry["sequence"]], ends_by_sequence[entry["sequence"]]
        if (start["endpoint"] != entry["endpoint"] or start["started_at"] != entry["started_at"]
                or end["ended_at"] != entry["received_at"] or end["http_status"] != entry["http_status"]
                or timestamp(entry["received_at"]) < timestamp(entry["started_at"])):
            raise ProbeError("observation_ledger_binding_mismatch")
        original = gzip.decompress((root / "raw" / (entry["source_sha256"] + ".json.gz")).read_bytes())
        if validate(original, task, entry["http_status"]) != record["validation"]:
            raise ProbeError("report_validation_mismatch")
    rebuilt, previous = [], {}
    for record in report["records"]:
        entry = record["observation"]
        key = (entry["kind"], entry["symbol"], entry["requested_date"])
        if key in previous:
            rebuilt.append(dict(symbol=entry["symbol"], kind=entry["kind"],
                                before=previous[key]["observation"]["sequence"],
                                after=entry["sequence"], result=compare(previous[key], record)))
        previous[key] = record
    if rebuilt != report["comparisons"]:
        raise ProbeError("comparison_replay_mismatch")
    for reference in report["daily_references"]:
        symbol = reference["symbol"]
        candidates = [key for key in previous if key[0] == "estimate" and key[1] == symbol]
        if not candidates:
            raise ProbeError("reference_source_missing")
        # Derive the date from preserved estimate identities; never from the host clock.
        latest_key = max(candidates, key=lambda key: previous[key]["observation"]["sequence"])
        rebuilt_reference = dict(symbol=symbol, **daily_reference(previous[latest_key],
                                 previous[("daily", symbol, latest_key[2])]))
        if reference != rebuilt_reference:
            raise ProbeError("daily_reference_replay_mismatch")
    for pair in report["receipt_pairs"]:
        symbol = pair["symbol"]
        candidates = [key for key in previous if key[0] == "estimate" and key[1] == symbol]
        latest_key = max(candidates, key=lambda key: previous[key]["observation"]["sequence"])
        rebuilt_pair = dict(symbol=symbol, **pair_observations(
            previous[("quote", symbol, latest_key[2])], previous[latest_key]))
        if pair != rebuilt_pair:
            raise ProbeError("receipt_pair_replay_mismatch")
    return dict(status="PASS_OFFLINE_INTEGRITY_ONLY", files=len(manifest["files"]),
                observations=len(observations), simulated_transport_calls=len(starts), live_http_calls=0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan").add_argument("--date", required=True)
    sub.add_parser("demo").add_argument("--out", type=Path, required=True)
    verification = sub.add_parser("verify")
    verification.add_argument("--root", type=Path, required=True)
    verification.add_argument("--sha256", help="Expected manifest hash from an independent trusted record")
    # A named live command fails even if credentials are present.
    sub.add_parser("live")
    args = parser.parse_args(argv)
    if args.command == "live":
        print(json.dumps(dict(status="BLOCKED", reason="stage2_live_disabled", live_http_calls=0)))
        return 2
    try:
        if args.command == "plan":
            result = plan(args.date)
        elif args.command == "demo":
            r = demo(args.out)
            result = {key: r[key] for key in ("mode", "live_http_calls", "simulated_transport_calls", "synthetic_market_responses")}
        else:
            result = verify(args.root, args.sha256)
        print(encoded(result).decode())
        return 0
    except (ProbeError, OSError, KeyError, ValueError, TypeError):
        print(json.dumps(dict(status="FAIL", reason="offline_operation_rejected", live_http_calls=0)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
