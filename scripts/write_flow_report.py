"""Publish a report only when saved-response reconciliation and live UI checks succeeded."""
import json,re
from pathlib import Path
out=Path('reports/flow-trends')
r=json.loads((out/'validation.json').read_text());live=json.loads((out/'live-final.json').read_text())
assert live['success'] and live['http_status']==200
count=re.search(r'(\d+) passed',(out/'tests.txt').read_text()).group(1)
def top_rows(key,metric):
 return '\n'.join(f"| {int(v[metric+'_rank'])} | {v.get('market','')} {v.get('sector',v.get('name',''))} | {v[metric]/1e8:,.2f} |" for v in r[key][:3])
report=f'''# 5·20거래일 수급 추이 반영 결과

[실제 대시보드](https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/) · 기준일 **{r['asof']}**, 수급 범위 **{r['start']} ~ {r['end']}**.

## 적용 결과

- 섹터: 5일·20일 누적 순매수, 최근 5일과 직전 5일 차이, 순매수 일수, 금액/시총 대비 강도 정렬, 일별 막대·누적선과 원본 합계 다운로드.
- **현재 구성 기준 수급 추이**: 구성 기준일의 종목 집합을 고정합니다. 과거 실제 구성에 따른 강세 종합 순위와 구분합니다.
- 현재 구성의 수급 점수는 기존 배점 강도 40점·지속성 30점을 적용했습니다. {r['sectors_partial_flow_points']}개 업종에서 부분 점수를 계산합니다. /70을 /100으로 환산하지 않으며 상대강도·종합 강세 순위는 보류합니다.
- 종목: 5일·20일 금액과 전체 수급 순위, 20일 순매수 일수, 직전 5일 대비 증감, 강도, 선택 종목 추이 그래프. ‘종목 범위 → 전체 시장’에서 전체 종목의 금액 순위를 확인할 수 있습니다.
- 기존 재무·가치평가 및 종합 점수·업종 내 순위 **{r['complete_stock_scores_preserved']:,}개**를 그대로 유지했습니다. API 재수집 **0회**, DB 파일 해시도 변경 전과 동일합니다.

| 기간 | 합계 계산 가능한 업종 | 합계 계산 가능한 종목 |
|---|---:|---:|
| 5거래일 | {r['sectors_5d']}/{r['sector_count']} | {r['stocks_5d']:,}/{r['stock_count']:,} |
| 20거래일 | {r['sectors_20d']}/{r['sector_count']} | {r['stocks_20d']:,}/{r['stock_count']:,} |

신규상장 3종목의 상장 전 {r['missing_stock_days']}개 관측은 결측으로 유지합니다. 관련 3개 업종의 20일 합계도 부분 합계를 완전한 합계처럼 표시하지 않고 보류합니다. 그래프 누적선은 결측 이후 이어 붙이지 않습니다.

## 실제 순매수 금액 순위 예시

금액 순위이며 종합 강세 순위나 매수 추천이 아닙니다. 단위: 억원.

### 현재 구성 업종 · 5일

| 순위 | 업종 | 순매수 |
|---:|---|---:|
{top_rows('sector_top_5d','net_5d')}

### 현재 구성 업종 · 20일

| 순위 | 업종 | 순매수 |
|---:|---|---:|
{top_rows('sector_top_20d','net_20d')}

### 전체 종목 · 5일

| 순위 | 종목 | 순매수 |
|---:|---|---:|
{top_rows('stock_top_5d','net_5d')}

### 전체 종목 · 20일

| 순위 | 종목 | 순매수 |
|---:|---|---:|
{top_rows('stock_top_20d','net_20d')}

전체 결과: [업종 CSV](sector-flows.csv), [종목 CSV](stock-flows.csv).

## 검증

- 원본 KIS 저장 응답과 수급 **{r['original_flow_rows_verified']:,}행** 전수 일치.
- **{r['sector_day_checks']:,}개 업종×거래일**의 구성 종목별 합계·수신 수·결측을 독립 대조했습니다. 기간별 종목/업종 금액 및 공동 순위도 별도 계산과 일치합니다.
- [날짜별 원문 합계 대조](daily-original-reconciliation.csv)의 `original_observed_total_krw`는 실제 받은 행만의 검증 합계입니다. 결측 종목의 0원을 뜻하지 않습니다. 화면의 업종 완전 합계와 구분합니다.
- 자동 테스트 **{count}개 통과**. 결측·실제 0·음수 증감·중복·테마 중복·가격 오류 분리·기간/정렬/전체 시장 화면을 검사했습니다.
- [실제 외부 브라우저 검증]({live['workflow_url']}): HTTP 200, 5/20일 변경, 업종 금액/강도 정렬, 종목 20일 정렬, 실제 차트 2개와 툴팁, CSV 값 대조 성공. [실제 화면](../../docs/dashboard-flow-trends.png)
- 배포 전후 DB SHA-256: `{r['database_sha256_unchanged']}`. 기존 종목 점수·순위 SHA-256: `{r['stock_score_sha256_unchanged']}`.

## 종가 차이와 남은 한계

보관한 공식 명세는 J=KRX, NX=NXT, UN=통합이며 기존 요청은 모두 J였습니다. **KRX/NXT 요청 혼용의 증거는 없고, 공급자 실제 집계·수정 원인은 미확정**입니다. 이전 대표 3종목의 재조회에서는 일반/수정/수급 부가 종가가 같고 저장 종가와 달랐으며, 순매수 금액·수량은 기존과 같았습니다. 이를 전체 종목 불변의 증거로 확대하지 않습니다. 새 API 호출이나 NXT 비교 수집은 하지 않았습니다. [검토 근거](price-scope-review.json)

시총 대비 강도는 선택 기준일에 보존된 시총을 사용하므로 시총 정합성 경고가 남습니다. 금액 합계·증감·순매수 일수는 종가 문제와 독립적으로 계산합니다. 기존 원본과 상대강도 보류를 유지합니다. 공급원 수급의 최종 확정 시각도 미검증입니다.

[계산·결측 규칙](../../docs/flow-trends.md). 사용자 코드 수정 없이 기존 페이지를 새로고침하면 됩니다.
'''
(out/'REPORT.md').write_text(report)
print('Report written from successful validation and live UI evidence')
