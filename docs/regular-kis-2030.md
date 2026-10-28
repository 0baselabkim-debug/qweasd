# KIS 정규 전체시장 잠정 관측 — KRX 거래일 20:30 KST

## 실행과 등록

전용 workflow `.github/workflows/kis-market-2030.yml`은 UTC `30 11 * * 1-5`만 예약한다. 대상은 KOSPI·KOSDAQ의 **당일 마스터 적격 전체 종목**이다. 23:30 추가 회차·실패 후 별도 재수집 예약·watchdog·누락 보완·날짜별 실험 재무장은 없다. 장시간 대기 job도 없다.

GitHub schedule은 기본 브랜치의 최신 커밋에서 실행된다. `main` 반영, 실제 workflow active 상태, 기본 브랜치 cron 원본을 확인하기 전에는 등록 완료가 아니다. main 코드 push / 수동 dispatch는 **무호출 배포 검증만** 수행하며 슬롯을 차지하지 않는다. 시장 수집은 schedule 이벤트만 가능하다. GitHub 지연·누락·큐 초과·공개 저장소 60일 비활동 비활성화가 가능하며 20:30 정각 시작을 보장하지 않는다.

전역 `kis-validation-auth`, `cancel-in-progress: false`, `queue: max`를 사용한다. 공식 문서는 max에서 대기 100개까지 허용한다고 설명한다. 기본 single 큐는 기존 pending을 교체하므로 사용하지 않는다. 최신 actionlint 1.7.12도 queue 확장을 모르는 제한이 있어, 나머지 문법 검사와 확장값 검사를 구분하고 **실제 GitHub의 workflow 수락**을 별도로 확인한다. 기존 workflow를 enable/cancel/수정하는 명령은 사용하지 않는다.

## 10월 28일 실험 근거

`feat/market-data-collector-v1` 최신 확인 커밋 `bb8edb9d858f443cb17d6c8e180d0fc5c8d38f3b`, 실제 Run [40596424279](https://github.com/0baselabkim-debug/qweasd/actions/runs/40596424279). 두 회차 모두 당일 **2,348종목 내부 PASS, 물리 HTTP 4,699회, 실패·재시도 0**, 비교 대상 값은 동일했다. 원본 시장 응답 4,698개의 과거 이력 포함 byte 동일성은 사용자가 확인한 독립 검증 결과다. 이번 환경에서는 원문 ZIP 전체를 다시 내려받아 독립 재검증하지 않았으므로 이를 새로 검증했다고 표시하지 않는다.

20:30 회차는 20:30:01~22:13:38 KST, 23:30 회차는 23:30:01~익일 01:00:17 KST에 걸친 관측이다. 모든 거래일의 20:30 공식 확정을 뜻하지 않는다. 실제 source provider는 KIS, 주식 요청 코드는 J(KRX 범위), 지수는 U이며 NXT/통합 범위를 새로 요청하지 않는다. 내부 PASS와 UNVERIFIED/PENDING_KRX를 유지한다.

## 날짜 및 거래일

- schedule Run의 **created_at UTC 날짜**로 20:30 KST 슬롯·asof·만료시각·generation_id를 처음에 고정한다. 큐 대기 후 job 시작 날짜로 바꾸지 않는다. GitHub payload에는 정확한 원래 예정 일자를 복구할 별도 필드가 없으므로 임의의 과거 슬롯 복원은 하지 않는다. 이벤트 생성 시각이 해당 UTC 날짜의 20:30 KST 슬롯~170분 창 밖이면 차단한다.
- 기존 **170분 실행 창**을 유지한다. 모든 물리 HTTP 직전에 동일 context·날짜·창·예산을 다시 확인한다. 따라서 23:20 이후 요청은 차단되며, 자정/다음 날 09:00 이후 전일분을 새로 시작하는 것도 허용되지 않는다. 지연분을 다음 날 데이터로 대체하지 않는다.
- 주말은 민간 달력의 토/일 규칙으로 0 HTTP 생략한다. 평일에는 **KIS `CTCA0903R chk-holiday`의 날짜가 일치하는 단일 `opnd_yn=Y/N` 응답**을 보존·검증한다. 이는 **공급자 거래일 응답이며 KRX 공식 휴장 공고라고 표현하지 않는다**. 조회 시각·원문 해시·응답 행을 기록한다.
- N이면 종목 마스터/전체시장 수집을 하지 않는다. 없는 날짜·중복 날짜·오류 응답·확인 불가·검증 가능한 별도 KRX 자료와 충돌 시 전체 수집을 차단한다. 현재 KRX 공식 일정 원본을 새로 수집하는 경로는 없으며, 검토한 공식 일별 reference가 제공되는 경우의 충돌 검사만 지원한다.
- 공유 자격 안전성이 확인되지 않으면 **거래일 조회용 인증부터 차단**한다. 실제 거래일을 확인하지 못한 해당 실행을 휴장일로 추정하지 않는다.

## 실행권과 영속 원장

기존 저장소의 별도 `kis-regular-state` 브랜치만 원장에 사용한다. 새로운 외부 저장소/서비스/계정은 만들지 않는다. 초기화 marker `FORMAT.json`이 맞지 않거나 상태 브랜치가 없으면 초기화/리셋을 자동으로 하지 않고 차단한다. 상태 브랜치는 운영 snapshot을 복사한 보관소가 아니라 소량의 원장 metadata 전용이다.

`ledger/YYYY.json`의 `slots[YYYY-MM-DD/2030]`를 GitHub Contents API의 SHA 조건부 생성/갱신(CAS)으로 확보한다. 같은 파일에 슬롯 상태·append-only history·연도별 `latest_complete`를 한 번에 기록하므로 완료 세대와 후속 읽기 포인터가 따로 성공하지 않는다. 원장 소유자는 Run ID·코드 SHA·고정 context로 바인딩되고 변경할 수 없다. 실행 프로세스의 실제 repository/ref/event/attempt/Run ID/SHA도 매 요청 직전 그 context와 일치해야 하므로 다른 Run이나 수동 이벤트가 context를 복사해 사용할 수 없다. 실패·중단·완료 이력은 삭제하지 않는다. 연간 원장 900KB 한도에 이르면 자동 절단하지 않고 검토를 요구한다. 원문·전 종목 상세는 Git 원장에 누적하지 않는다.

동일 슬롯이 이미 존재하면 상태가 FAILED/BLOCKED여도 자동 재수집하지 않는다. 최초 소유권을 고르기 전에 in_progress/queued/requested/waiting/pending의 실제 Run을 확인하고 더 이른 슬롯 소유 후보가 있으면 물러난다. 나중에 대기 중인 중복이 최초 소유자를 중단시키지는 않는다. CAS 자체가 최종 중복 방어이며 concurrency만으로 중복 제거됐다고 주장하지 않는다. KIS 첫 호출 직전에도 최신 소유 원장·현재 실제 Run·대기 상태를 다시 확인한다. 기존 Run은 취소하지 않는다.

허용 상태는 CLAIMED→PREPARING→COLLECTING→INTERNAL_PASS→COMPLETE_PROVISIONAL이며 중간 오류는 FAILED/BLOCKED/ARTIFACT_UNCONFIRMED, 휴장은 SKIPPED_CLOSED, 주말은 SKIPPED_WEEKEND다. terminal 상태를 reset하는 함수는 없다. 강제 종료로 terminal 영수증을 못 쓴 진행 슬롯도 다음 Run이 새 세대로 재사용하지 않는다. 그 경우 원장과 실제 owner Run 상태를 통해 INTERRUPTED/미확인 상태를 진단해야 하며 성공/0호출로 단정하지 않는다.

## 저장소 간 KIS 충돌 — 현재 차단 사항

읽기 검토한 `0baselabkim-debug/kis-intraday-monitor` main은 `e27bc63fd420a899501c13f6cd1b05c13108fc82`이다. README는 감독된 공유 계정 사용을 고려하지만 기술적 상호 잠금은 없다고 명시한다. `actions-auth-approval.json`의 독점/계정 정책 확인도 false다. 비활성 approval 파일이나 순간적인 Run 부재는 모든 자격 사용자가 통제된다는 증거가 아니다. 같은 concurrency 이름은 다른 저장소까지 잠그지 않는다. 기존 main에도 여러 KIS 수동 workflow가 있어 이름만으로 전체 계정 충돌 방지가 보장되지 않는다.

새 workflow는 기존 `KIS_APP_KEY/SECRET` Secrets를 교체하지 않고, **별도 승인 후 독립 계정임을 확인한 경우에만** 사용할 전용 `KIS_REGULAR_APP_KEY` / `KIS_REGULAR_APP_SECRET` 바인딩 경로를 준비했다. 내부 검증된 Providers에는 표준 환경변수명으로 별칭을 전달하지만 기존 저장소 Secrets 자체는 변경하지 않는다. 현재 이 자격을 생성/복사/설정하지 않았다.

`.github/config/kis-regular-access.json`은 **UNVERIFIED_SHARED_ACCOUNT / verified=false**다. 실제 API는 여기서 차단된다. 단순히 verified를 true로 바꾸어도 통과하지 않는다. 별도 계정 분리·rate/token 정책·검토자·비밀이 아닌 증거 reference·검토 유효기간·실제 앱키와 일치하는 SHA256 바인딩을 확인해야 한다. 앱키/Secret/토큰과 계산한 fingerprint를 로그에 출력하지 않는다. 이 증거는 GitHub의 실제 Secret을 자동 확인한 것처럼 가정하지 않는다.

추가 승인 대상은 **독립 계정/자격의 발급 또는 사용과 전용 Secrets 연결**이다. 공유 계정을 그대로 사용하려면 대신 다른 저장소와 모든 발급자까지 참여하는 원자적 계정 lease 설계/권한/코드 변경을 별도로 검토·승인해야 한다. 그 전역 lease는 이번에 구현됐다고 주장하지 않는다. 모니터 저장소 코드나 자격·기존 실행은 변경하지 않았다. 배포 검증에서 다른 저장소 Actions 조회 권한이 없으면 UNREADABLE로 기록하며 임의 권한 확장도 하지 않는다.

## 검증된 수집 범위와 호출 정책

실험의 필수 의존 모듈 12개만 선별해 재사용한다. 날짜 고정 실험 workflow/control·감시/복구 controller·관련 없는 기능 브랜치 내용은 main에 통째로 병합하지 않는다. 원래 익일 가드는 수정하지 않는다. 기존 `kis_manual.run(..., policy=...)` 잠정 경로를 쓰며 종목당 수급 1회·원주가 OHLCV/거래대금 1회와 KOSPI/KOSDAQ 지수를 유지한다.

독립 자격을 사용할 수 있는 경우 달력+마스터 준비는 최대 4회(인증1/달력1/마스터2), 전체 수집은 기존 최대 5,600회·요청간격 1.05초·요청 타임아웃·제한적 요청 복구를 유지한다. 준비와 전체 수집 경계에도 1.05초를 준수한다. 준비에서 발급한 **동일 프로세스·동일 자격 토큰**만 메모리로 전달해 불필요한 두 번째 인증을 피한다. 이를 위한 optional memory_session 인자 외에는 재사용 모듈의 기본 호출 동작을 바꾸지 않는다. 토큰 파일/캐시/새 Secret 저장은 하지 않는다.

당일 적격 N종목에 대해 보수적인 full 예상 `2*N+3 <= 5600`을 먼저 확인한다. 동일 프로세스 토큰 재사용 시 실제 준비 포함 기본 요청은 `2*N+6`이다. 대상 증가 시 예산을 올리지 않고 상한/남은 실행시간을 넘으면 차단한다. 인증/실패/재시도도 원장에 포함한다. 자동 전체시장 재수집 Run이나 23:30 일정은 만들지 않는다. 한 Run 내부의 실패 요청 제한 복구만 기존 알고리즘대로 수행한다.

주식 원문·마스터·관측 시각·원천 SHA256·물리 요청 원장·수급·원주가·두 지수·DuckDB·JSON·검증 결과를 보존한다. 모든 적격 종목의 코드/시장/기준일, 원문/DB/JSON 값, 중복·누락, 가격-수급 거래량/거래대금 일관성 등을 검사한다. 미수집을 0으로 채우지 않는다. 기관 금액 단위가 미확정인 원래 필드는 unit_unverified로 유지하고 값을 만들어내지 않는다.

## 아티팩트·소비자·게시 상태

- generation: `kis-regular-YYYYMMDD-2030`, artifact: `<generation>-run-<Run ID>`.
- 설정 보존기간 **90일**. 실제 만료일은 저장 API의 `expires_at`로 기록한다. 영구 보존/외부 백업이 아니며 만료되면 원문 복구가 불가능할 수 있다. 새 버킷·외부 자격을 만들지 않는다.
- 내부 PASS만으로 완료 포인터를 갱신하지 않는다. secret scan/manifest → artifact upload → API에서 artifact Run/name/digest/만료 확인 → 원문/DB 재검증까지 통과해야 원장과 연도별 latest_complete가 함께 COMPLETE_PROVISIONAL로 바뀐다.
- 실패 artifact는 진단 상태로 남으며 마지막 완료 포인터를 바꾸지 않는다. 업로드 실패/원문 무결성 실패는 COMPLETE가 아니다. runner 강제 종료나 secret scan 실패에서는 원문 업로드를 보장할 수 없으며 미확인 진행 원장을 리셋하지 않는다.
- `python -m collector_v1.regular.handoff --asof YYYY-MM-DD`는 정확한 날짜의 COMPLETE_PROVISIONAL만 읽는다. 없으면 NOT_READY_FOR_REQUESTED_DATE로 반환한다. 전일 자료를 당일 자료로 표시하지 않는다. 만료/만료시각 미확인 artifact는 READY로 반환하지 않는다. READY는 검증된 수신 영수증 metadata이며, 소비자는 다운로드 bytes/ZIP digest/manifest를 별도로 검증해야 한다. 응답은 generation/asof/검증 상태/Run/코드 SHA/artifact ID·digest·위치를 제공한다.
- **수집 완료 / 내부 검증 / 아티팩트 저장 / 대시보드 게시 / 앱 반영은 별도**다. 여기서 dashboard_publication=NOT_PUBLISHED, application_reflection=NOT_UPDATED다. 기존 운영 snapshot/DB/JSON/대시보드에 새 데이터를 설치하지 않는다.

기존 21:00 Word 브리핑 예약은 변경하지 않는다. 이 저장소의 현재 main에서 그 Word 예약 정의 자체는 확인되지 않았으며 외부 예약을 수정하는 도구도 사용하지 않았다. 10/28 실측상 20:30 전체 수집이 22:13까지 걸렸으므로 21시에 당일 완료 세대가 있다고 보장하지 않는다. 후속 브리핑은 요청 거래일의 완료 handoff가 없으면 보류/기준일 미준비를 명시해야 한다. 기존 승인된 게시 파이프라인을 새 잠정 artifact와 조용히 연결하지 않는다.

## 검증 및 배포 범위

오프라인 가상 응답으로 정상/휴장/달력 불가·충돌, 모든 pending 상태, 원자 실행권 경쟁·완료/실패 중복, 자정·장시간 지연·만료, 모집단 변화·부분 실패·예산 초과, 공유 KIS 안전성 차단·자격 바인딩·원문/manifest/DB 불일치, 아티팩트 후 완료와 정확한 날짜 소비자를 검사한다. 기존 main의 5개 테스트는 이미 제거된 레거시 cron을 요구해 실패했으므로, 수집 브랜치에 있던 비활성 유지 검사를 선별 반영한다. 운영 workflow는 복원하지 않는다.

기본 브랜치 반영 후 **등록 완료와 첫 정규 수집 성공은 따로** 보고한다. API를 부르는 별도 실수집은 이번 검증에서 하지 않는다. 추가 승인/증거가 없으면 등록된 schedule도 매일 사전 안전성 차단을 기록하고 인증을 시도하지 않는다. 결과와 실제 배포 버전은 `reports/regular-2030/` 및 상태 브랜치 `deployments/<Run ID>.json`에 보존한다.

공식 근거: [workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax), [schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). 이 환경은 api.github.com을 허용하지 않는 현재 정책이 enforced로 관측되므로 cloud CLI의 Actions 읽기는 전송 단계에서 차단될 수 있다. GitHub 내부 실행의 공식 API 확인과 공개 공식 Run/기본 브랜치 파일을 통해 등록을 교차 확인하며, 설정 초안만으로 적용됐다고 주장하지 않는다.

수신자 보호 검사: 토큰 메모리가 사라지는 다음 단계에서도 credential scan 실패 표식이 남으면 원문 업로드와 완료 승인을 계속 거부한다. 에러가 발생한 경우 Job Summary에는 우선 안전한 실패 코드만 표시한다.

오프라인 검증 기록: 전체 회귀 411통과/0실패(289.65초), 최종 관련 회귀 30통과/0실패(35.67초). 마지막 실행자 바인딩 시험은 전체 회귀 이후 추가되어 중복 제외 412개를 검증했다. 기존 보호 파일 881개는 동일하다. 신규 수집 실호출은 0회다.
