# KIS 독립 수동 수집 실행기

기존 세 실험 워크플로의 중단 상태를 유지한다. 신규 `kis-manual-validation.yml`에는 cron이 없고 기능 브랜치의 신규 제어 파일 단독 커밋에서만 실행된다. 일반 코드 push로 API를 호출하지 않는다. KRX 키와 endpoint는 사용하지 않는다. main 및 snapshot을 수정하지 않는다.

## 승인 범위와 실제 표본

사용자 승인: 2026-10-26의 현대건설 000720, LG씨엔에스 064400, 플리토 300080 및 KOSPI/KOSDAQ 지수. KIS 인증·실패·재인증·재시도를 합해 표본 **누적 12회**. 정상 기본 9회(인증 1+지수 2+수급 3+가격 3), 나머지 3회는 복구 여유다. 한 실행만의 제한이 아니라 아티팩트로 이어받는 전체 표본 누적 제한이다.

신규 잠정 경로가 제어 파일의 독립 수동 시간 창을 검사한다. 기존 `Providers.fetch()`와 3종목 익일 검증 가드를 변경하지 않는다. KIS 날짜/종목/수급-가격 종가·거래량·거래대금/기관 수량 검사를 유지하고, 공식 가격 상태는 계속 PENDING_KRX다.

`collector_v1.kis_manual`은 표본과 전체시장에 같은 호출·저장·복구 코드를 사용한다. 성공 원문은 그대로 재사용하며, 프로그램 수정으로 파싱할 수 있게 된 기존 응답은 네트워크 호출 없이 다시 검증한다. 최초 인증 오류는 중단하고, 일시 오류만 원장/시간 한도 안에서 재시도한다. 전체시장에는 제공처 연속 오류 회로 차단과 제한적 실패 작업 복구가 있다.

## 수동 실행 방법

실행 코드/테스트를 먼저 커밋한다. 이후 `.github/trials/kis-manual-20261026.json`만 단독 변경·커밋·push한다. `request_id`는 고유해야 하고 `previous_run`은 이 신규 워크플로의 가장 최근 완료 Run ID여야 한다(최초는 null). `trial_id`, mode별 승인 문자열, 날짜 및 예산이 맞지 않으면 새 HTTP 전에 중단한다. GitHub 재실행 버튼의 attempt 2는 허용하지 않는다.

```bash
git add .github/trials/kis-manual-20261026.json
git commit -m 'Authorize bounded manual KIS execution'
git push origin HEAD:feat/market-data-collector-v1
```

실제 Actions 내 명령은 `python -m collector_v1.kis_manual_actions run`이다. 이 명령은 기능 브랜치/Repository/제어 파일 단독 push 맥락을 검증하므로, 임의 환경에서 같은 명령을 실행해 안전장치를 생략할 수 없다.

## 원문·원장 복원과 결과 확인

고정 마스터 Run 40289601356, artifact 12636170227의 ZIP SHA256/파일 11개/기준일/원장 4회를 재검증하며 종목 마스터 API를 재수집하지 않는다. 전 종목 3,950/적격 2,349 및 지정 종목의 시장·이름·적격 여부를 검사한다.

새 원천 상태는 `kis-manual-state-20261026` Actions 아티팩트(14일)에 보존한다. 다음 실행은 가장 최근 상태의 ZIP digest와 manifest를 검증해 복원한다. 상태나 원장이 유실되면 새 root로 12회를 초기화하지 않는다. 원장/모든 원문/DB/JSON을 재검증한다. 실패 단계의 원문도 보존하되 인증 응답의 토큰 본문은 저장하지 않는다.

결과는 Job Summary와 `reports/kis-manual-20261026/<Run ID>.json`에 남긴다. `contents: write` 권한은 **기능 브랜치의 이 결과 JSON 한 파일**을 보존하는 데 사용하며, workflow를 enable/disable하거나 main을 갱신하는 코드가 없다. 시장 API 인증 값은 출력하지 않고, 아티팩트 및 공개 결과를 바인딩된 값으로 유출 검사한다.

## 전체시장 준비와 실행 조건

표본 PASS를 원문·원장·DuckDB/JSON으로 다시 검증한 뒤 전체시장 계획을 자동 생성한다. 전체시장 HTTP는 이번 작업에서 승인되지 않았다.

- 대상: 같은 공식 마스터 적격 **2,349종목 전부**. 표본 데이터를 복사하지 않고 새로 조회한다.
- 정상 기본: `2×2349 + 인증1 + 지수2 = 4,701회`.
- 상한: 실패/인증 포함 **5,600회**, 시작 간격 최소 1.05초.
- 간격만의 하한: 약 **82.25분**. 실제 표본에서 측정한 endpoint 응답 시간으로 예상치를 함께 계산하며, 세 종목 추정이라는 한계를 표시한다.
- 회차 실행 창 최대 170분, Actions job 최대 175분. 남은 시간에 모집단 처리가 불가능하면 대규모 호출 전에 중단한다.
- 중단·재개는 같은 full 상태 원장과 성공 원문을 복원한다. 표본과 full 예산은 별도이며 표본 12회를 초과하는 우회에 full 모드를 사용할 수 없다.
- 전체시장 성능/처리량·관측 시각·원장·실패·재시도와 원문 및 DuckDB/JSON 결과를 같은 보고서에 남긴다.

향후 **별도 전체시장 승인 후** 제어 파일에 mode=`full`, trial_id=`kis-full-20261026`, max_total_http=5600, approval=`USER_SEPARATELY_APPROVED_FULL_KIS_COLLECTION`, 최신 previous_run, 고유 request_id, 새 not_before/expires_at(170분 이하)를 적용하고 위 단독 커밋 명령을 실행한다. 현재 작업에서는 이 full 승인 제어 파일을 만들거나 push하지 않는다.

오프라인 계획만 확인할 때는 최신 상태 아티팩트를 `data/kis-manual-20261026`에 정확히 복원한 뒤 `python -m collector_v1.kis_manual_actions plan-full`을 사용한다. 이 명령은 API를 호출하지 않는다. KRX는 전체시장 KIS 수집의 선행조건이 아니다.
