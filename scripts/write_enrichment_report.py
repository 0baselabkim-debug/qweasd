"""Render the current evidence-backed coverage report, without live requests."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];out=ROOT/'reports/enrichment'
v=json.loads((out/'coverage.json').read_text());h=json.loads((out/'history-quality.json').read_text())
names={'revenue':'매출액','operating_profit':'영업이익','net_income':'당기순이익','equity':'자본총계','liabilities':'부채총계','ocf':'영업현금흐름','market_cap':'기준일 시가총액','revenue_growth':'매출 성장률','op_growth':'영업이익 성장률','roe':'ROE','debt_ratio':'부채비율','per':'PER','pbr':'PBR'}
lines=['# 데이터 보완·평가 검증 결과','',f"분석 기준일: **{v['asof']}**. 기존 평가 대상 **2,430종목**을 분모로 사용했습니다. 원래의 10월 14일 수급 2,430행은 그대로 보존했습니다. 거래정지 108개·상장폐지 5개는 기존 제외 기준을 유지했습니다.",'','[기존 대시보드](https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/)','', '## 재무 및 시총','', '| 항목 | 확보·계산 가능 | 비율 |','|---|---:|---:|']
for key,label in names.items():
 r=v['metrics'][key];lines.append(f"| {label} | {r['count']:,} | {r['percent']:.2f}% |")
lines+=['',f"영업이익 성장률은 전기 영업이익이 양수인 경우만 계산합니다. 흑자전환 **{v.get('turnaround_symbols',0):,}개**는 성장률을 꾸미지 않고 별도 표시합니다.",
 '', 'OpenDART 다중회사 주요계정 API에 현금흐름표가 없던 것이 주된 누락 원인이었습니다. 동일 공시·연결/별도 범위의 전체계정을 수집하고 표준코드와 정확한 계정명을 대조했습니다. 비12월 결산 27개를 추가로 연결했습니다. 원문에서 직접 확인한 현금흐름 2건은 account_overrides.json에 공시번호·원문 해시·금액을 기록했습니다.',
 '', '재무는 2025년 연간/2024년 비교입니다. 회계기간과 실제 공시일은 별도 저장합니다. 총액·비지배지분 또는 지배·비지배 손익이 같은 공시에 모두 명시된 경우에만 회계 항등식으로 계산하고, 누락을 0으로 가정하지 않습니다. 연결 PER/PBR은 지배주주 손익/자본, ROE는 총순이익/평균 총자본입니다.',
 '', 'KRX 공식 당일 API는 두 시장 모두 HTTP 200, 0행이었습니다. KIS 공식 현재가 API를 GitHub Actions에서 조회해 시총 2,430개를 보완했습니다. 명세의 시총 단위 설명이 비어 있어, 실제 종가×상장주식수와 전 건 대조하여 억원 배율을 검증했습니다. 당일 종가·거래량·당일 종목마스터 주식수 및 마감 후~다음 날 09:00 이전 수신을 모두 확인했습니다. KIS 응답 자체에는 거래일 필드가 없다는 한계는 화면에 명시했습니다.',
 '', '## 과거 외국인 수급','',f"적재 범위: **{h['start']} ~ {h['end']}**, 20거래일. 실제 적재 **{h['flow_rows']:,}행**, 20일이 모두 있는 종목 **{h['full_20d_symbols']:,}개**. 상태: `{h['collection_status']}`.",
 '', '10종목 소규모 검증에서 300행을 수신했고 날짜 중복·필수값 누락·호출 제한 오류는 0건이었습니다. 이후 사용자 승인을 받아 나머지 2,420종목으로 확대했습니다. 기존 10월 14일을 요청하지 않고 조회 종료일을 10월 13일로 고정했습니다. API가 반환한 더 긴 원문은 보관하고 DB에는 필요한 이전 19일만 추가했습니다.',
 '', '금액은 공식 개요의 백만원 단위에 따라 frgn_ntby_tr_pbmn × 1,000,000으로 환산했습니다. 수량은 주입니다. KIS J=KRX를 사용하며 NXT/통합 데이터와 혼합하지 않습니다. 원천 순매수 금액을 보존하고 매수-매도 반올림 차이나 수량×종가로 대체하지 않습니다. 공급원 최종 확정 여부는 여전히 미검증입니다.',
 '',f"원시 날짜 중복: **{h.get('duplicate_raw_dates','검증 대기')}**, DB 중복: **{h['duplicate_keys']}**, 금액 결측: **{h['missing_amount']}**. 미확보 종목·날짜 유형: `{h['missing_types']}`. 상장 전 날짜를 제외한 관측 확보율: **{h['completeness_excluding_prelisting_percent']:.4f}%**.",
 '',f"적재한 과거 금액·수량의 원문 전수 대조: **{h.get('all_imported_historical_amounts_and_quantities_checked',0):,}행**. KIS 부가 시세와 KRX 공식 시세의 차이 **{h.get('kis_krx_price_difference_rows','검증 대기')}행**은 별도 기록하며 가격에는 KRX 공식 값을 유지합니다."]
for mode,r in h['collected_modes'].items():
 lines+=['',f"- {mode}: 성공 {r['success']:,}종목 / 실패 {r['failed']}종목, HTTP 요청 {r['requests']:,}회, {r['elapsed_seconds']/60:.2f}분, 호출 제한 응답 {r['rate_limit_responses']}건. [실제 실행](https://github.com/0baselabkim-debug/qweasd/actions/runs/{r['run_id']})"]
lines+=['','## 업종·테마 및 평가','', '미분류 **232 → 0개**. 기존 종목마스터 코드/이름 매핑에서 빠진 종목을 KIS 공식 업종명 223개, KRX KIND 분류 9개로 보완했습니다. 기존 업종은 그대로 유지했고 KIND의 다른 분류체계는 `표준산업 · …`로 구분했습니다.',
 '', '테마는 AIDC 1개, 전력 인프라 3개, 반도체 2개, 로봇 2개, 방산 2개, 원전 2개로 시작합니다. **11개 기업·12개 소속 관계**이며 두산에너빌리티가 중복 소속입니다. 전부 실제 사업보고서의 사업 내용·공시일·원문 링크·해시를 기록했습니다. 나머지 2,419개는 미검토 상태로 확인 필요이며 임의 분류하지 않았습니다. 합집합 순매수는 종목코드로 중복을 제거합니다.',
 '', '| 종목 평가 항목 | 계산 가능한 점수 | 고정 배점 |','|---|---:|---:|']
for key,label,weight in [('foreign_flow','외국인 수급',30),('growth','실적 성장성',25),('profitability_health','수익성·재무건전성',25),('valuation','가치평가',15),('activity','거래 활성도',5)]:lines.append(f"| {label} | {v['stock_components'][key]:,}종목 | {weight} |")
lines+=['',f"**종목 종합 점수·업종 내 순위: {v['complete_stock_scores']:,}개**. 섹터 종합 점수: **{v['complete_sector_scores']}개**. 가중치는 재분배하지 않았습니다. 업종 특수기업·적자·자본 0 이하·필수 계정 누락·비교 기업 부족은 보류합니다.",
 '', '## 남은 한계와 검증','', '- 섹터 구성의 확인된 시작일은 2026-10-14입니다. 과거 구성·조정주가·시장 기준지수가 확보되지 않아 섹터의 20일 평가와 종합 강세 순위는 보류합니다. 현재 구성으로 과거 지수를 소급하지 않았습니다. KRX 과거 시세의 SECT_TP_NM은 코스닥 소속부 구분이며 업종 이력이 아니므로 대체하지 않았습니다.',
 '- 과거 시세 대조에서 39종목·390행의 종가 차이를 확인했습니다. 315행은 거래량 0, 나머지 75행은 종가와 거래량이 역비율로 달랐고 거래대금은 전 건 일치했습니다. 분할·병합 등 조정과 부합하지만 정확한 조정 규칙은 미확인입니다. 가격·거래대금은 KRX 자료만 사용하며, KIS 과거 수량은 공급원 값으로 보관하고 점수에는 사용하지 않습니다.',
 '- 원화 연간 재무 미확보 15개: USD 재무 2개, 신규상장 등 2025년 연간 자료 부재 13개. 금융업의 매출 정의 차이, 지배지분 미표시, 전기 적자 등으로 일부 지표는 계속 보류합니다. 상세 목록은 financial_missing_diagnosis.json과 coverage.json에 있습니다.',
 '- 삼성전자·SK하이닉스·NAVER와 2개 업종을 별도 산식/SQL로 대조했습니다. 현금흐름·PER·PBR·ROE·성장률·부채비율이 일치했습니다. 기존 적재 재실행에서도 실제 수급·시세·재무·업종·제외 자료가 그대로 유지됐습니다.',
 '- 테스트용 가상 자료는 테스트 파일에만 존재합니다. 배포 DB에는 실제 자료만 있습니다. 기존 API 키 값의 저장소/배포 파일 일치 0건을 검사했습니다.',
 '- 공개 앱에서 배포 후 구버전 계산 모듈이 남는 오류를 발견해 소스·데이터 버전에 따른 모듈 및 캐시 갱신으로 수정했습니다. 실제 외부 브라우저의 업종/테마 선택, 정렬, 상세 조회, CSV 다운로드 검증 기록은 Actions에 남겼습니다.',
 '- 데이터 자동 수집 워크플로는 유지했습니다. 수집 결과를 매일 웹 스냅샷으로 자동 게시하는 연결은 이번에 추가하지 않았으며 화면 기준일은 2026-10-14입니다.']
(out/'REPORT.md').write_text('\n'.join(lines)+'\n')
print(out/'REPORT.md')
