"""Write the final report only after real source, publication and browser evidence exists."""
import json,re
from pathlib import Path

root=Path(__file__).resolve().parents[1]
def read(name):return json.loads((root/name).read_text())

if __name__=='__main__':
    support=read('reports/sector/support-validation.json');themes=read('reports/sector/theme-validation.json')
    live=read('reports/sector/live-final.json');manifest=read('snapshot/manifest.json')
    ledger=read('snapshot/publication-ledger.json');daily=read('snapshot/daily-validation.json');security=read('reports/sector/security.json')
    assert live['success'] and live['http_status']==200 and manifest['automatic_snapshot_sync']
    assert support['stock_score_sha256_before']==support['stock_score_sha256_after']
    assert daily['preservation_verified'] and security['credential_value_matches']==0
    count=re.search(r'(\d+) passed',(root/'reports/sector/tests.txt').read_text()).group(1)
    publication=ledger[manifest['asof']];url='https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/'
    theme_counts=' · '.join(f'{k} {v}개' for k,v in themes['theme_counts'].items())
    text=f'''# 섹터 자료·테마 확대·일일 자동 반영 결과

분석 기준일 **{support['asof']}**. [실제 대시보드]({url})

| 작업 | 결과 | 확인 내용 |
|---|---|---|
| 수정주가·기준지수 | 수신 성공·수정주가 평가 보류 | 수정주가 {support['adjusted_symbols']:,}종목·{support['adjusted_rows']:,}행, KOSPI·KOSDAQ 지수 {support['benchmark_rows']:,}행 |
| 과거 업종 구성 | 미확보 | KRX 공개 페이지 및 업종 화면 접근 403. 허용된 기본정보 API의 소속부는 업종이 아님 |
| 섹터 종합 강세 순위 | 보류 | 당시 업종 구성 없이 현재 구성을 과거에 적용하지 않음 |
| 테마 확대 | 부분 검증 범위 확대 완료 | 11 → {themes['unique_companies']}개 기업, {themes['memberships']}개 소속 관계. 전부 사업보고서 원문 확인 |
| 일일 수집 → 웹 자동 게시 | 실제 자료 재처리·게시 성공 | 정상 {daily['records']:,}개와 근거 있는 제외 {daily['excluded']:,}개를 처리하고 기존 DB 보존 검증 |
| 종목 평가 보존 | 성공 | 종합 점수·업종 내 순위 {support['complete_stock_scores']:,}개. 전체 종목 주요 지표·점수·순위 해시가 변경 전과 일치 |

## 실제 데이터와 산식 검증

- 수정주가는 KIS 공식 `J=KRX`, `FID_ORG_ADJ_PRC=0`을 사용했습니다. 원시 종가를 수정주가로 간주하지 않았습니다. KOSPI `0001`, KOSDAQ `1001`의 공식 지수를 수신했습니다. 실제 수집 시간 {support['elapsed_seconds']/60:.2f}분, 호출 제한 응답 {support['rate_limit_responses']}건. [수집 실행](https://github.com/0baselabkim-debug/qweasd/actions/runs/{support['source_run']})
- **종가 재조회 차이 {support['latest_close_conflict_symbols']:,}종목**: 새 수정주가 관측은 별도로 보관하되 해당 날짜를 포함하는 섹터 상대강도 계산에서 제외합니다. 기존 종가·시총·종목 점수는 덮어쓰지 않았습니다. 3개 대표 종목을 추가 호출한 결과 현재 수정주가·일반 종가·수급 응답의 종가는 서로 같고, 기존 저장 종가와 달랐습니다. 세 종목의 순매수 금액·수량은 기존과 같았습니다. 공급자 값의 변경 원인이나 전체 종목의 수급 불변 여부는 확정하지 않았습니다. [재조회 근거](adjustment-consistency-probe.json)
- 기존 수급 48,567행, 10월 14일 정상 종목 2,430개, 재무 2,415개를 유지했습니다. 기존 원시 시세·재무·업종·제외 자료를 전수 비교했습니다. 수정주가·지수는 별도 출처를 기록해 추가했습니다. 과거 일별 시총은 기존 KRX 공식 60,698행과 당일 KIS 2,430행을 그대로 사용하며 현재 시총을 과거에 소급 적용하지 않습니다.
- 종목 평가 가중치 30/25/25/15/5와 섹터 40/30/20/10을 유지했습니다. 기존 3개 종목·2개 업종의 독립 계산 검사도 유지합니다. 섹터 종합 점수는 {support['complete_sector_scores']}개이며 부족한 항목의 배점을 재분배하지 않습니다.
- 과거 구성의 확인된 시작일은 2026-10-14입니다. KRX 과거 기본정보의 `SECT_TP_NM`은 우량기업부·벤처기업부 등으로, 산업 분류를 대체할 수 없습니다. KIS 현재 마스터에는 날짜별 구성 조회 기능이 없으므로 최신 마스터를 소급 적용하지 않았습니다. 10월 15일 실제 마스터도 별도로 조회했지만 14일 구성으로 사용하지 않았습니다.

## 테마

{theme_counts}. 확인한 기업은 **{themes['unique_companies']}개**, 중복 소속 관계는 **{themes['overlap_memberships']}건**입니다. 두산에너빌리티·비에이치아이·SNT에너지가 전력 인프라와 원전에 중복 소속됩니다. 전체 테마 합집합 순매수 {themes['union_net_krw']:,}원은 종목별 한 번만 합산했습니다.

원전 범위는 실제 설계·주요 설비·계측제어·주설비 건설·정비로 명시했습니다. 단순 기대 수혜나 이름 유사성으로 분류하지 않았습니다. 사업보고서의 공시일·원문 링크·발췌·파일 해시를 저장했습니다. SNT에너지의 복수기 사업처럼 해당 연도 매출이 별도 확인되지 않는 경우도 근거 설명에 표시했습니다. 미검토 {2430-themes['unique_companies']:,}개 기업은 임의 분류하지 않습니다. 화면의 **부분 검증 테마**는 전체 시장 업종 순위와 구분됩니다.

## 자동 반영과 보존

한국시간 평일 **18:30** 수집 시도, **21:00** 미완료분 재시도입니다. 공식 휴장일 API를 실제 조회해 검증했고, 거래일당 정상 게시 1회를 기록합니다. 보존 자료 재처리는 기존 날짜·종목·수급·원시 시세를 변경할 수 없으며, 재조회 차이가 있는 수정주가는 평가 보류 표시를 유지합니다. 신규 날짜의 정상 수집에서는 종가 불일치가 있으면 게시를 막습니다. 성공 종목과 정상 제외 자료는 체크포인트를 재사용합니다. API 오류·날짜/단위/중복 오류·미확보가 있으면 이전 정상 스냅샷을 유지합니다.

[자동 게시 검증 실행](https://github.com/0baselabkim-debug/qweasd/actions/runs/{publication['run_id']})은 **보존된 실제 자료를 재처리한 검증**입니다. 전체 수급을 다시 수집하지 않았습니다. 새 거래일의 전체 수집 및 3거래일 연속 운영은 아직 수행되지 않았습니다. 예약 지연과 실제 시작·완료·게시 시각을 기록하도록 연결했습니다. 자세한 동작은 [일일 운영 문서](../../docs/daily-publication.md)에 있습니다.

## 테스트와 남은 문제

- 자동 테스트 **{count}개 통과**. 누락·단위 오류·잘못된 날짜·기존 값 변경·업종 소실·상장폐지 후 재상장 구분·거래정지 가격 보존을 검사했습니다.
- [실제 공개 앱 검증]({live['workflow_url']})에서 HTTP 200, 자동 게시 상태 표시, 부분 테마 {themes['unique_companies']}개 기업, 업종/테마 선택, 정렬, 상세 조회, CSV 다운로드를 확인했습니다.
- 저장소 파일 {security['tracked_files_checked']}개에서 기존 비밀키 값 일치 **0건**, `.env` 미추적을 확인했습니다. 테스트용 가상 값은 배포 DB에 넣지 않았습니다.
- **섹터 종합 순위는 미완료**입니다. 과거 업종 구성에 접근할 수 있는 공식 원천이 추가로 필요합니다. 앞으로는 날짜별 실제 구성을 저장하고, 기간별 모든 필수 자료가 충족된 섹터부터 평가합니다. 거래 활성도에는 25거래일이 필요하며, 단순히 날짜가 지났다고 완료 처리하지 않습니다.
- 공급원 수급의 최종 확정 시각은 계속 **미검증**입니다. 18:30을 확정 시각이라고 표시하지 않습니다. 기존 재무 결측·평가 보류 기준도 유지합니다.

사용자가 코드를 수정할 필요는 없습니다. 기존 대시보드를 새로고침하면 됩니다.
'''
    (root/'reports/sector/REPORT.md').write_text(text)
    print('Final report written from successful real evidence')
