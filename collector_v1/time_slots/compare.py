"""Exact raw-field and core comparisons; observation identity is never a value."""
import datetime as dt
import json
import re
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from ..core import Failure, encoded, sha
from . import policy

CORE_PRICE = ('open', 'high', 'low', 'close')
CORE_VOLUME = ('volume', 'turnover')
CORE_FOREIGN = ('foreign_net_quantity', 'foreign_net_amount')
CORE_INSTITUTION = ('institution_net_quantity', 'institution_amount_raw')
CORE = CORE_PRICE + CORE_VOLUME + CORE_FOREIGN + CORE_INSTITUTION
MISSING = {'type': 'missing'}
NUMERIC = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')


def normalize(value, field=''):
    """No rounding, null/blank/absent are distinct; identifiers retain zeroes."""
    if value is None:
        return {'type': 'null'}
    if type(value) is bool:
        return {'type': 'boolean', 'value': value}
    identifier = any(x in field.lower() for x in ('iscd', 'code', 'date', 'name', 'sign')) or field.endswith('_yn')
    if not identifier and isinstance(value, (int, float, Decimal, str)):
        text = str(value).strip()
        if re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?', text):
            text = text.replace(',', '')
        if len(text) <= 500 and NUMERIC.fullmatch(text):
            d = Decimal(text)
            if not d.is_finite() or abs(d.adjusted()) > 1000:
                raise Failure('unsupported_nonfinite_or_oversized_number')
            # Decimal.normalize() uses context precision; formatting does not.
            number = format(d, 'f')
            if '.' in number:
                number = number.rstrip('0').rstrip('.')
            if d == 0:
                number = '0'
            return {'type': 'number', 'value': number}
    return {'type': 'string', 'value': str(value)}


def flatten(value, prefix=''):
    out = {}
    if isinstance(value, dict):
        if not value:
            out[prefix] = {'type': 'empty_object'}
        for k, v in sorted(value.items()):
            escaped = str(k).replace('~', '~0').replace('/', '~1')
            out.update(flatten(v, prefix+'/'+escaped))
    elif isinstance(value, list):
        if not value:
            out[prefix] = {'type': 'empty_array'}
        for n, v in enumerate(value):
            out.update(flatten(v, prefix+'/'+str(n)))
    else:
        out[prefix] = normalize(value, prefix.rsplit('/', 1)[-1])
    return out


def market_view(raw, kind, asof):
    d = json.loads(raw, parse_float=Decimal)
    result = {}; target = {}
    for name, value in d.items():
        # Protocol status/message and collector metadata are not market data.
        if name in ('rt_cd', 'msg_cd', 'msg1', 'tr_cont'):
            continue
        if name == 'output2':
            dates = [r['stck_bsop_date'] for r in value]
            if len(dates) != len(set(dates)):
                raise Failure('duplicate_comparison_source_date')
            for row in value:
                if row['stck_bsop_date'] == asof.replace('-', ''):
                    result.update(flatten(row, kind+'/output2/'+row['stck_bsop_date']))
                    target.update(flatten(row, kind+'/target'))
        else:
            result.update(flatten(value, kind+'/'+name))
    if not target:
        raise Failure('comparison_target_date_missing')
    return result, target


def build_observations(folder, report, universe):
    from ..core import Store
    from ..experiment import engine
    from ..providers import task
    s = Store(folder); generation_id = report['generation_id']
    cache = engine.replay(s, 1, asof=policy.ASOF, generation_id=generation_id)
    names = {r['symbol']: r for r in universe}
    observations = {}
    for row in report['rows']:
        if row['symbol'] in observations:
            raise Failure('duplicate_comparison_symbol')
        full = {}; target = {}; metadata = {}
        for kind in ('flow', 'price'):
            t = task(kind, policy.ASOF, round=1, symbol=row['symbol'], market=row['market'], generation_id=generation_id)
            e = cache[engine.identity(t)]
            fields, day_fields = market_view(s.raw(e), kind, policy.ASOF)
            full.update(fields); target.update(day_fields)
            metadata[kind] = dict(observed_at=e['collected_at'], source_hash=e['source_hash'], observation_id=sha(encoded(e)), params=e['params'])
        observations[row['symbol']] = dict(symbol=row['symbol'], name=names[row['symbol']].get('name'),
            market=row['market'], asof=policy.ASOF, generation_id=generation_id,
            all_market_fields=full, target_day_fields=target,
            core={k: normalize(row[k], k) for k in CORE}, metadata=metadata,
            scope=dict(stock_market_code='J', exchange='KRX', NXT_included=False,
                output1_date_binding='current_context_not_assumed_target_day', output2_target_date=policy.ASOF))
    return observations


def build_indices(folder, report):
    from ..core import Store
    from ..experiment import engine
    from ..providers import task
    s=Store(folder);g=report['generation_id'];cache=engine.replay(s,1,asof=policy.ASOF,generation_id=g);out={}
    for market in ('KOSPI','KOSDAQ'):
        t=task('index',policy.ASOF,round=1,market=market,generation_id=g)
        e=cache.get(engine.identity(t))
        if e:
            fields,_=market_view(s.raw(e),'index',policy.ASOF)
            out[market]=dict(fields=fields,observed_at=e['collected_at'],source_hash=e['source_hash'])
    return out


def numeric_difference(a, b):
    if a.get('type') != 'number' or b.get('type') != 'number':
        return dict(delta=None, percent=None, reason='non_numeric_or_missing')
    x, y = Fraction(a['value']), Fraction(b['value'])
    delta = y-x
    def exact(n):
        return dict(numerator=str(n.numerator), denominator=str(n.denominator))
    return dict(delta=exact(delta), percent=exact(100*delta/abs(x)) if x else None,
                reason=None if x else 'zero_baseline_percent_undefined')


def differences(a, b):
    return {k: dict(before=a.get(k, MISSING), after=b.get(k, MISSING),
                    **numeric_difference(a.get(k, MISSING), b.get(k, MISSING)))
            for k in sorted(set(a) | set(b)) if a.get(k, MISSING) != b.get(k, MISSING)}


def category_flags(a, b, core_diff):
    target_diff = differences(a['target_day_fields'], b['target_day_fields'])
    return dict(price=any(k in core_diff for k in CORE_PRICE),
        volume_turnover=any(k in core_diff for k in CORE_VOLUME),
        foreign=any(k in core_diff for k in CORE_FOREIGN) or any('frgn_' in k for k in target_diff),
        institution=any(k in core_diff for k in CORE_INSTITUTION) or any('orgn_' in k for k in target_diff))


def compare_pair(left, right):
    lr, lo = left['report'], left.get('observations', {})
    rr, ro = right['report'], right.get('observations', {})
    lt, rt = set(lr.get('target_symbols', [])), set(rr.get('target_symbols', []))
    common = sorted(set(lo) & set(ro) & lt & rt)
    comparable = [s for s in common if (lo[s]['market'], lo[s]['asof']) == (ro[s]['market'], ro[s]['asof']) == (lo[s]['market'], policy.ASOF)]
    identity_mismatches = sorted(set(common)-set(comparable))
    complete = (lr.get('status') == rr.get('status') == 'PASS' and lt == rt and bool(lt)
                and set(comparable) == lt and lr.get('population_sha256') == rr.get('population_sha256'))
    details = []; unchanged = []; core_unchanged = []; metadata = []; field_counts = Counter(); category_codes = {k: [] for k in ('price', 'volume_turnover', 'foreign', 'institution')}
    for symbol in comparable:
        a, b = lo[symbol], ro[symbol]
        all_diff = differences(a['all_market_fields'], b['all_market_fields'])
        core_diff = differences(a['core'], b['core'])
        flags = category_flags(a, b, core_diff)
        for k, changed in flags.items():
            if changed:
                category_codes[k].append(symbol)
        if not all_diff:
            unchanged.append(symbol)
        if not core_diff and not flags['foreign'] and not flags['institution']:
            core_unchanged.append(symbol)
        if all_diff or core_diff:
            details.append(dict(symbol=symbol, name=a.get('name'), market=a['market'],
                all_market_changes=all_diff, core_changes=core_diff, categories=flags,
                before_metadata=a['metadata'], after_metadata=b['metadata']))
            field_counts.update(all_diff.keys())
        metadata.append(dict(symbol=symbol, before=a['metadata'], after=b['metadata']))
    n = len(comparable); core_changed = n-len(core_unchanged)
    if not complete:
        classification = 'NOT_COMPARABLE_COMPLETE_POPULATION'
    elif len(unchanged) == n and not core_changed:
        classification = 'A_COMPLETE_IDENTICAL'
    elif core_changed == 0:
        classification = 'B_CORE_IDENTICAL'
    elif core_changed*2 > n:
        classification = 'D_WIDESPREAD_CHANGE'
    else:
        classification = 'C_PARTIAL_CHANGE'
    rankings = []
    for d in details:
        for field, change in d['all_market_changes'].items():
            if change['delta'] is not None:
                rankings.append(dict(symbol=d['symbol'], name=d['name'], field=field, **change))
    rankings.sort(key=lambda d: abs(Fraction(int(d['delta']['numerator']), int(d['delta']['denominator']))), reverse=True)
    indices={}
    for market in sorted(set(left.get('indices',{}))|set(right.get('indices',{}))):
        before=left.get('indices',{}).get(market);after=right.get('indices',{}).get(market)
        indices[market]=dict(comparable=bool(before and after), changes=differences(before['fields'],after['fields']) if before and after else None, before_metadata=before,after_metadata=after)
    return dict(index_comparison=indices,left_generation=lr.get('generation_id'), right_generation=rr.get('generation_id'),
        complete_population_comparable=complete, comparable_symbols=n, identical_symbols=len(unchanged),
        identical_ratio=len(unchanged)/n if n else None, changed_symbols=len(details),
        changed_ratio=len(details)/n if n else None, core_identical_symbols=len(core_unchanged),
        core_identical_ratio=len(core_unchanged)/n if n else None, classification=classification,
        widespread_rule='strict majority (>50%) of comparable symbols with a core change',
        category_changed_counts={k:len(v) for k,v in category_codes.items()}, category_changed_symbols=category_codes,
        changed_field_counts=dict(field_counts), unchanged_symbols=unchanged, core_unchanged_symbols=core_unchanged,
        left_only_population=sorted(lt-rt), right_only_population=sorted(rt-lt), identity_mismatches=identity_mismatches,
        left_missing=sorted(lt-set(lo)), right_missing=sorted(rt-set(ro)),
        changes=details, largest_absolute_changes=rankings[:50], metadata_comparison=metadata,
        percent_semantics='100*(after-before)/abs(before), exact rational; zero baseline undefined',
        all_market_semantics='all output1 context and every field of target-day output2; historical rows retained raw, excluded from target-day equality',
        target_day_core_semantics='validated OHLCV/turnover/net flows plus all raw target-day foreign/institution fields',
        official_price_status='PENDING_KRX')


def stabilization(pairs):
    adjacent = pairs[:3]
    labels = ('18시→21시', '21시→00시', '00시→03시')
    result = {}
    for category in ('all', 'core', 'price', 'volume_turnover', 'foreign', 'institution'):
        flags = []
        for p in adjacent:
            if not p['complete_population_comparable']:
                flags.append(None)
            elif category == 'all':
                flags.append(p['identical_symbols'] == p['comparable_symbols'])
            elif category == 'core':
                flags.append(p['core_identical_symbols'] == p['comparable_symbols'])
            else:
                flags.append(p['category_changed_counts'][category] == 0)
        suffix = len(flags)
        while suffix and flags[suffix-1] is True:
            suffix -= 1
        result[category] = dict(first_unchanged_pair=next((labels[i] for i,v in enumerate(flags) if v is True), None),
            last_changed_pair=next((labels[i] for i in range(2,-1,-1) if flags[i] is False), None),
            sustained_observed_unchanged_from=policy.LABELS[suffix] if suffix < 3 else None,
            adjacent_unchanged=flags)
    ready = all(p['complete_population_comparable'] for p in adjacent)
    start = result['core']['sustained_observed_unchanged_from'] if ready else None
    recommendations = {'1800':'18시 이후 시작 가능','2100':'21시 이후 시작 권장','0000':'00시 이후 시작 권장'}
    return dict(categories=result, recommendation=recommendations.get(start, '현재 실험만으로 판단 불가'),
        recommendation_basis='Observed unchanged suffix only; use actual per-symbol observation ranges and completion times, not nominal start instants',
        official_finality='UNVERIFIED', official_price_status='PENDING_KRX',
        limitations=['Four rounds cannot prove an official finalization instant.',
            'J is KRX scope; no NX/UN calls, so NXT inclusion/causality is not established.',
            'A full run spans many observation times; nominal 18:00 is not one point-in-time snapshot.',
            'output1 current quote context and historical output2 rows are retained raw and excluded from target-day equality.'])


def markdown(reports, pairs, analysis):
    lines = ['# 2026-10-27 KIS 시간대별 관측 실험', '',
        '가격·수급은 잠정 관측값이며 공식 확정 상태는 PENDING_KRX입니다. 아래 시각은 KST입니다.', '',
        '## ① 전체 수집 결과', '', '|항목|18시|21시|00시|03시|', '|---|---|---|---|---|']
    def show(v):
        if v is None:return 'NOT_RUN'
        if isinstance(v,str):
            try:return dt.datetime.fromisoformat(v).astimezone(policy.KST).isoformat()
            except ValueError:pass
        return str(v)
    for title, key in [('상태','status'),('실제 시작 시각','actual_started_at'),('완료 시각','completed_at'),('수집 종목 수','received_symbols'),('내부 검증 통과율','final_validation_rate'),('HTTP 요청 수','physical_http'),('전체 소요시간(초)','elapsed_seconds')]:
        lines.append('|'+title+'|'+'|'.join(show(r.get(key)) for r in reports)+'|')
    lines.append('|오류·재시도 수|'+'|'.join(str(r.get('failed_http_requests','NOT_RUN'))+'/'+str(r.get('physical_retries','NOT_RUN')) for r in reports)+'|')
    lines += ['', '## ② 회차별 데이터 동일성', '', '|비교 구간|전체 동일 종목 비율|가격 변경|거래량·거래대금 변경|외국인 수급 변경|종합 판단|','|---|---|---|---|---|---|']
    for label, p in zip(('18시→21시','21시→00시','00시→03시','18시→03시'), pairs):
        ratio = str(p['identical_ratio']) if p['identical_ratio'] is not None else '비교 불가'
        c = p['category_changed_counts']
        lines.append(f"|{label}|{ratio}|{c['price']}|{c['volume_turnover']}|{c['foreign']}|{p['classification']}|")
    lines += ['', '## ③ 주요 변경 종목 및 필드', '',
        '전체 변경 목록·필드별 정확한 변화량/변화율·이전 회차와 동일한 종목·지수 비교·관측 메타데이터는 pair-*.json에 보존합니다. 변화율은 반올림하지 않은 분수(분자/분모)입니다.']
    for label,p in zip(('18→21','21→00','00→03','18→03'),pairs):
        lines.append(f"- {label}: 변경 {p['changed_symbols']}/{p['comparable_symbols']}종목, 외국인 변경 {p['category_changed_counts']['foreign']}종목")
        for x in p['largest_absolute_changes'][:3]:
            lines.append(f"  - {x['symbol']} {x['field']}: {x['before'].get('value')} → {x['after'].get('value')}; delta={x['delta']}")
    lines += ['', '## ④ 데이터 확정 시점 분석', '', '```json', json.dumps(analysis['categories'],ensure_ascii=False,indent=2), '```',
        '변경이 관측되지 않은 구간은 공식 확정 시점의 증거가 아닙니다. KRX 대조는 미실시이며 PENDING_KRX를 유지합니다. J=KRX 범위이므로 NXT 거래 반영 여부는 이 실험만으로 판정할 수 없습니다.',
        '', '## ⑤ 권장 정기 수집 시간', '', analysis['recommendation'],
        '', '권고는 비교 가능한 회차의 핵심 데이터 무변경 구간에 한정됩니다. 표의 실제 시작·완료 시각과 종목별 수급/가격 관측 시각을 함께 해석해야 합니다. 운영 예약은 생성하지 않았습니다.']
    return '\n'.join(lines)+'\n'
