"""Standalone escaped HTML. The fixed-column price table is the final section."""
from html import escape

from .contracts import value

PRICE_COLUMNS = (
    ('종목명·보유', 165), ('기준가·평단', 150), ('눌림매수 구간', 170), ('추세·돌파 구간', 180),
    ('지지 예상·확인', 220), ('1차 관찰가', 130), ('2차 관찰가', 130), ('도달 시 행동', 240),
    ('수익 보호선', 150), ('추세 훼손 경고선', 170), ('최종 방어선', 150), ('논리 무효화 조건', 340), ('매수·보유·비중 의견', 280))


def text(x):
    return escape('확인 불가' if x is None else str(x), quote=True)


def zone(plan):
    z = plan.get('entry_zone')
    return text(z['low'])+' ~ '+text(z['high'])+' ('+text(plan['status'])+')' if z else 'UNAVAILABLE'


def render(dataset, assessments, judgments, context, portfolio, candidate_codes, selected_codes=None):
    by_code = {r['stock_code']: r for r in dataset['stocks']}
    held = portfolio.get('holdings', {})
    selected = selected_codes or []
    codes = list(dict.fromkeys([*held, *candidate_codes]))
    html = ['<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>독립 투자 판단 브리핑</title><style>body{font-family:sans-serif;line-height:1.5} .table-scroll{overflow-x:auto;max-width:100%}',
        'table{table-layout:fixed;border-collapse:separate;border-spacing:0}th,td{padding:10px;border:1px solid #ddd;vertical-align:top;overflow-wrap:normal}',
        '.number{white-space:nowrap;word-break:keep-all}th:first-child,td:first-child{position:sticky;left:0;background:#fff;z-index:2}',
        'thead th:first-child{z-index:3}.wrap{white-space:normal;word-break:keep-all}</style></head><body>',
        '<h1>독립 투자 판단 브리핑</h1><section id="data"><h2>1. 데이터 기준과 검증 상태</h2>',
        '<p>거래일 '+text(dataset['asof'])+' · dataset '+text(dataset['dataset_id'])+' · 원천 '+text(dataset['source_manifest_sha256'])+'</p>',
        '<p>공급자 최종성 '+text(dataset.get('provider_finality'))+' · 내부 검증과 공식 최종 확정은 별개입니다. 과거 관측의 소급 변경과 자동주문은 없습니다.</p></section>',
        '<section id="macro"><h2>2. 국내외 시장 및 매크로 환경</h2>']
    if context['facts']:
        html.append('<ul>')
        for fact in context['facts']:
            if fact['kind'] in ('macro', 'event', 'industry'):
                html.append('<li>'+text(fact['metric'])+': '+text(fact['value'])+' '+text(fact['unit'])+' · 공개 '+text(fact.get('published_at'))+' · 출처 '+text(fact['source_sha256'])+'</li>')
        html.append('</ul>')
    html.append('<p>외부 연동 상태 '+text(context['status'])+' · 미제공 지표·뉴스는 생성하지 않았습니다.</p></section>')
    html.append('<section id="holdings"><h2>3. 보유 종목별 대응</h2>')
    if not held:
        html.append('<p>NOT_RUN: 사용자 확인 보유 정보 없음.</p>')
    for code, p in held.items():
        html.append('<p>'+text(p['stock_name'])+' · 수량 '+text(p['quantity'])+' · 평단 '+text(p['average_cost_krw'])+' · '+text(assessments.get(code, {}).get('holding_action'))+'</p>')
    html.append('</section><section id="candidates"><h2>4. 신규 관심 후보 선정</h2>')
    html.append('<p>검토 후보 '+str(len(candidate_codes))+'개, GPT 가설 검증을 통과한 우선 후보 '+str(len(selected))+'개. 자동 매수 목록이 아닙니다.</p>')
    for code in candidate_codes:
        row, a = by_code[code], assessments[code]
        result = judgments.get(code, dict(status='NOT_RUN'))
        j = result.get('judgment') or {}
        html.append('<article><h3>'+text(row['stock_name'])+'</h3><p>부분 점수 '+text(a['scorecard']['partial_score'])+'/'+text(a['scorecard']['assessed_maximum'])+
                    ' · 종합 '+text(a['scorecard']['total_score'])+' · GPT '+text(result['status'])+' · 등급 '+text(j.get('grade'))+'</p>')
        for field, label in (('thesis', '투자 가설'), ('counter_thesis', '반대 가설'), ('risks', '위험')):
            for item in j.get(field, []):
                html.append('<p>'+label+'(가설): '+text(item['text'])+' · 근거 '+text(', '.join(item['fact_ids']))+'</p>')
        html.append('</article>')
    html.append('</section><section id="comparison"><h2>5. 기존 종목과 신규 후보 비교</h2>')
    comparisons = [c for result in judgments.values() for c in (result.get('judgment') or {}).get('holding_comparisons', [])]
    html.append('<p>'+(' · '.join(text(c['stock_code'])+': '+text(c['relative_attractiveness']) for c in comparisons) if comparisons else 'NOT_RUN: 검증된 GPT 상대 비교 없음.')+'</p></section>')
    html.append('<section id="portfolio"><h2>6. 포트폴리오 종합 판단</h2><p>상태 '+text(portfolio['status'])+' · 현금 포함 자산 '+text(portfolio.get('assets_krw'))+
                ' · 전체 계획 위험 비율 '+text(portfolio.get('total_planned_risk_ratio'))+'</p><p>보유 최대 네 종목, 기본 비중 20~30%, 확신 30~35%, 최대 40%. 계좌 입력이 없으면 비중이나 체결을 추정하지 않습니다.</p></section>')
    # Nothing explanatory is appended after this final price table.
    html.append('<section id="prices"><h2>7. 종목별 가격대 통합표</h2><div class="table-scroll"><table aria-label="종목별 가격대" style="width:'+str(sum(w for _, w in PRICE_COLUMNS))+'px"><colgroup>')
    html.extend('<col style="width:'+str(w)+'px">' for _, w in PRICE_COLUMNS)
    html.append('</colgroup><thead><tr>'+''.join('<th scope="col">'+text(name)+'</th>' for name, _ in PRICE_COLUMNS)+'</tr></thead><tbody>')
    for code in codes:
        row = by_code.get(code)
        if not row:
            # No current market input for a confirmed holding: retain an explicit row.
            cells = [text(held[code]['stock_name'])+' · 보유']+['UNAVAILABLE']*(len(PRICE_COLUMNS)-1)
        else:
            a = assessments[code]; p = a['strategies']['trend_confirmation']; pull = a['strategies']['pullback']; br = a['strategies']['breakout']
            supports = a['technical_structure']['support_candidates']
            expected = supports[0]['price'] if supports else None
            confirmed = a['technical_structure']['support_test_confirmed']
            old = held.get(code, {})
            defense = old.get('defense', {}).get('price_krw') if old.get('defense') else p['defense']
            close = value(row, 'price_trend_metrics', 'close')
            j = judgments.get(code, {}).get('judgment') or {}
            protection = None
            if old.get('defense') and old.get('average_cost_krw') is not None:
                from .contracts import number
                if number(old['defense']['price_krw']) > number(old['average_cost_krw']):
                    protection = old['defense']['price_krw']
            cells = [text(row['stock_name'])+(' · 보유' if code in held else ' · 관심'),
                text(close)+' / '+text(old.get('average_cost_krw')), zone(pull), zone(p)+' / '+zone(br),
                text(expected)+' / '+('가격 테스트 관측' if confirmed is True else '미확인' if confirmed is None else '테스트 미관측'),
                text(p['upside_observation_1']), text(p['upside_observation_2']), '추세 재평가·유지 시 보유 검토',
                text(protection), text(expected), (('기존: ' if old.get('defense') else '구조 후보: ')+text(defense)) if defense is not None else 'UNAVAILABLE',
                text(' · '.join(p['invalidation_conditions'])), text(j.get('action') or a['holding_action'])+' · 비중 '+text(old.get('weight_ratio'))]
        html.append('<tr>'+''.join('<td class="'+('wrap' if i in (4, 7, 11, 12) else 'number')+'">'+v+'</td>' for i, v in enumerate(cells))+'</tr>')
    if not codes:
        html.append('<tr><td colspan="'+str(len(PRICE_COLUMNS))+'">검증된 후보·사용자 확인 보유 정보 없음. 가격을 임의 생성하지 않았습니다.</td></tr>')
    html.append('</tbody></table></div></section></body></html>')
    return ''.join(html)
