# 중앙 잠금 V3 운영 전환 사전 검증 — 안전 중단

## 판정

BLOCKED. 사용자 운영 반영 승인은 확보했다. 추가 병합 승인을 기다리는 상태가 아니라 실제 실행 자격과 구버전 실행 종료 증거가 부족하여 병합하지 않았다. 세 PR은 Draft/미병합이며 KRX PR #3도 변경하지 않았다. 현재 main과 기존 활성 정책/20:30 예약을 유지한다.

## 실제 확인과 차단 원인

* qweasd Actions Run41545127108: 실제 github.token으로 중앙 qweasd의 운영 원장/Actions 읽기, 별도 `kis-shared-probe-v3-20261102-operational` 브랜치에서 실제 GitBackend/Lease 취득·소유권 확인·반납 2전이 성공. KIS 자격은 값 대신 서버가 평가한 존재 여부 boolean만 사용했다.
* monitor Actions Run41545335889: 실제 기존 KIS_COORDINATOR_TOKEN으로 같은 시험 원장 취득·확인·반납 2전이 성공. 시험 최종 owner=null, sequence=16. 운영 원장은 sequence=12 및 기존 revision을 그대로 유지했다. 두 자격의 중앙 Contents 쓰기 및 Actions 읽기를 확인했다. 시험 브랜치를 운영 원장으로 대체하지 않았다.
* investment-integrated-dev Run41544297780/41545135359: KIS_APP_KEY, KIS_APP_SECRET, KIS_COORDINATOR_TOKEN을 Actions에서 이용할 수 없음. 해당 job 환경에서는 세 항목 모두 false다. 저장소 environments 조회도 0건이어서 선택 누락된 기존 environment로 해결할 근거가 없다. 연결 앱의 대상 저장소 Secrets 공개키 조회도 403(Resource not accessible by integration)이다. 원문은 읽거나 출력하지 않았고 새 자격을 만들거나 복사하지 않았다.
* monitor Run40811582221은 2026-10-29 생성된 ec4aa5c 구버전 workflow_dispatch가 queued/job0으로 남았다. 그 소스에는 중앙 잠금 없는 bootstrap 모드가 존재한다. 실제 실행 중인 job을 중단하지 않았다. job0/queued를 다시 확인한 후 일반 cancel과 공식 force-cancel을 각1회만 요청했지만 둘 다 HTTP409 'Cannot cancel a workflow run that has not been queued yet.'였다. GET 상태와 취소 API가 불일치하여 terminal 상태를 입증할 수 없다. 실행 기록 삭제, 잠금 강제 해제, 무한 재시도는 하지 않았다.

신규 저장소 자격을 이용 가능하게 하는 계정 소유자의 설정/권한 조치와 GitHub의 구버전 대기 실행 상태 해소가 필요하다. 현재 연결은 대상 Secrets 등록에 필요한 공개키 접근부터 거절되어 자체 권한 확대로 해결할 수 없다. 새 KIS 계정/앱키는 필요하지 않다.

## 코드와 승인 정책 준비

GitHub-only 무호출 Actions/script/17개 기본 검사와 제한 응답 검사8개를 세 기능 브랜치에 추가했다. Python socket/DNS/http.client 연결은 차단하고 gh/Git의 목적지는 중앙 저장소와 격리된 시험 브랜치로 제한한다. 실제 KIS 값은 runner에 주입하지 않으며 GitHub 자격은 기존 gh credential 경로로만 사용한다.

신규 개발 저장소의 기존 Secret 비참조 테스트는 이 승인된 단일 GitHub-only workflow에만 정확한 구조/명령/boolean 자격/읽기 권한 예외를 허용하도록 보완했다. 일반 분석/CI workflow와 분석 프로세스의 네트워크 거절은 그대로다. 이 예외로 KIS 호출이나 운영 DB 쓰기를 허용하지 않는다.

계정 조정 승인과 신규 실조회 승인을 분리했다. schema2의 operator_account_coordination_approved는 원장 참여를, operator_live_validation_approved는 신규 참여자의 OAuth/조회 허용을 별도로 통제한다. 새 참여자는 계정 등록만으로 before_http/실제 전송을 통과하지 못한다. 기존 장후·장중의 승인된 운영 경로는 신규 제한 실조회 승인과 분리한다. 새3개 모의 회귀로 등록/취소된 live 승인/기존 수집 호환성을 검증했다. 모든 제안 활성 플래그는 여전히 false다.

`native-premerge.json`은 세 실제 소스 트리/별도 프로세스/실제 Git CAS/Session/클라이언트의 Native33 PASS다. 최종 HTTPS 전송만 합성했다. 새 소스 해시와 proof SHA를 제안 정책에 바인딩했고 `hash-transition-plan.json`에 실제 main과 소스별 SHA를 기록했다. qweasd/monitor의 기존 schema1을 보존한 source rebind 후보는 wrapper의 not_applied=true 아래 격리했다. 실제 review_local_evidence/Session.review를 합성 credential marker로 호출하여 정적 승인 계약을 통과했다. Lease 취득과 OAuth는 이 정적 검사에서 호출하지 않았다.

기존 운영 main의 승인 정책(schema1)은 현재 원본 소스/영수증 해시와 모두 일치한다. 각 PR은 main의 후손이고 실제 merge-tree도 충돌 없이 PR 트리와 같았다. 병합 후 SHA는 아직 존재하지 않으며 임의 생성하지 않았다. 실제 병합 후 main 소스 해시와 run GITHUB_SHA를 다시 대조해야 한다. 기존 원장 schema1을 초기화하거나 schema2 원장으로 바꾸지 않는다. schema2는 참여자 승인 정책 버전이며, lease JSON schema1과 구분한다.

## 제한적 실조회 계획(이번에는 실행하지 않음)

종목000720, 요청시장 J. `/oauth2/tokenP` POST1회와 `/uapi/domestic-stock/v1/quotations/inquire-price` GET1회(FHKST01010100)로 최대2회다. 인증 실패도 물리 요청에 포함한다. 외부 캐시 토큰을 자동 신뢰·재사용하지 않고 동일 실행 내 검증된 메모리 토큰만 사용한다. OAuth 응답이 제공처 캐시 토큰일 수도 있어 실제 발급 횟수를 추정하지 않는다.

별도 승인 후에만 ENABLE_LIVE_KIS_VALIDATION, operator_live_validation_approved, actual_market_calls_authorized와 소스/자격/시간창/고유 request_id/000720/2회 상한을 모두 통과해야 한다. 원장 acquire/각 요청 직전 owner 검증이 필수이며 정규 수집이 점유 중이면 진행하지 않는다. 기본값은 비활성이다.

응답은 HTTP 상태와 rt_cd, output 객체, 존재하는 종목코드, 필수 가격/거래량/거래대금 정수 문자열을 검사한다. 공급자가 종목코드나 거래일을 반환하지 않으면 REQUEST_BOUND_ONLY/UNAVAILABLE로 남기며 추정하지 않는다. 제공처 오류/손상 응답을 PASS로 승인하지 않는다. 숫자를 임의 보정하거나 반올림하지 않는다.

시장 응답은 정상·실패 모두 자격 echo 검사를 통과한 경우에만 별도 원문·SHA256·응답 상태·관측 시각으로 보존한다. OAuth 원문/토큰은 저장하지 않는다. 네트워크 불명·인증 오류·429·redirect·스키마 오류 시 후속 요청/자동 재시도를 중단한다. 정상 종료는 토큰 폐기/response closed 이후 lease를 반납하고, 불명 결과/비정상 종료는 소유권을 보존하여 명시적 terminal-owner 검토를 요구한다. 기존 방어/만료 탈취 금지는 유지한다.

운영 데이터/DB/JSON/대시보드에 연결하지 않으며 모든 결과는 검증용 출력에 격리한다. 현재 미등록 실조회 템플릿은 이 작업에서 등록/실행하지 않았다.

## 테스트와 운영 영향

새 코드의 최종 전체 테스트 개수와 실행 코드 버전은 PR CI/JUnit 및 최종 PR 설명의 V3 결과를 우선한다. Native33과 표적52개는 로컬 PASS다. 실제 main 병합이 없어 병합 후 회귀는 NOT_RUN이며 이를 개발 브랜치 CI 성공과 혼동하지 않는다. 신규 저장소 운영 readiness의 BLOCKED 실패는 그대로 유지한다.

main3개, 기존 20:30 cron, Secrets, 운영 lease/정규 ledger, 원본/DB/JSON, 가격 승인, 대시보드 변경0. 실제 KIS/OAuth/KRX/OpenAI/Telegram0. 운영 잠금 강제 해제0. 격리된 실제 GitHub 시험 ref 생성1회와 lease 취득/반납 총4전이만 수행했다. probe 자동 허용은 다시 false로 되돌렸다.

공식 가격 승인0/PENDING_KRX2348/실전 후보0을 유지한다. 기존695건 유형 식별694/근거 부족1은 수정하지 않았다. 이 가격 차단은 중앙 잠금 병합과 독립적이다.

## 재개 조건과 안전 순서

1. 새 저장소 Actions에 기존 세 자격을 안전하게 이용 가능하게 하고 무호출 read/probe를 통과시킨다. 필요한 Secrets 설정 권한을 현재 연결에 허용하거나 계정 소유자가 기존 값을 등록해야 한다. 채팅으로 값을 전달하지 않는다.
2. Run40811582221의 terminal 상태와 구버전 worker 부재를 확인한다. 현재 API 불일치를 단순 busy 없음으로 무시하지 않는다. 삭제나 강제 잠금 해제로 대체하지 않는다.
3. 실제 소스/세 실행 자격/시험 owner 반환/후속 CI를 재확인하고, 기존 두 참여자 소스 승인 기록과 함께 PR#4·#23을 조정 배포한다. PR#1의 기본 비활성 코드를 반영하고 실제 main별 해시를 검증한다. schema2 계정 정책은 세 배포·권한·배제 증거가 있을 때만 활성화한다. PR#3은 필요하지 않다.
4. main 무호출 검증과 동일 범위의 전체 회귀/아티팩트 검증 후에만 OPERATIONAL_LOCK_VERIFIED 및 READY_FOR_LIMITED_LIVE_TEST를 판정한다. 실제 KIS2회 실조회는 별도 승인 이후다.

추가 병합 승인을 다시 요구하는 것이 아니라 이미 받은 승인 범위 안에서 사실 확인 조건이 충족될 때 재개하는 절차다. 이번에는 MERGE_COMPLETE=false, OPERATIONAL_LOCK_VERIFIED=false, READY_FOR_LIMITED_LIVE_TEST=false, BLOCKED_BY_OFFICIAL_DATA=true다.
