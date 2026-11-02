# KIS 중앙 잠금 통합 V2 — 개발 검증 기록

범위: 개발 브랜치의 공통 잠금/전송 안전성. 운영 main 병합·권한 설정·실계정 연동은 이 기록으로 승인되지 않는다.

## 변경과 호출 경로

`call-paths.json`: 실행 진입점 39개 묶음 중 PROTECTED 28, DISABLED 11, BYPASS 0, UNKNOWN 0. 이는 조사한 개발 소스의 실행 경로 분류다. 아직 수정되지 않은 운영 main과 과거 체크아웃을 안전하다고 판정한 수치가 아니다.

* 세 저장소가 같은 lease.py/runtime.py/git_backend.py/boundary.py/worker.py를 사용한다. 중앙 대상은 qweasd의 `kis-shared-account-state:lease.json`이며 저장소별 concurrency 이름을 공유 잠금으로 간주하지 않는다.
* 실제 Git expected-parent, 일반 push의 non-fast-forward 거절, 원격 readback을 사용한다. force push, 자동 초기화, TTL 만료만으로 소유권 이전은 없다.
* OAuth와 시장 요청 모두 요청 직전 소유자·실행 ID·커밋·만료·정책을 다시 검사한다. 실패한 조회나 알 수 없는 응답도 인증 예산과 불명 상태에 반영한다. 알 수 없는 결과/소유권 변경 이후 요청을 재시도하지 않는다.
* urllib 감사 훅으로 사용자 지정 opener와 보조 helper에도 보호를 적용한다. 승인된 urllib dispatch가 없는 저수준 HTTP 전송, 주문/POST 시장 조회, 리다이렉트에 의한 자격 전송을 차단한다. 메모리만 사용하는 테스트 응답은 네트워크 성공의 증거로 사용하지 않는다.
* 구형 KIS 클라이언트는 Session과 요청 예산을 생성 시 고정한다. 작업 스레드에 두 ContextVar를 전달하고 전체 요청을 RLock으로 직렬화한다. 수집 모집단·원천 계산·검증 기준은 바꾸지 않는다.
* 정상 보조 workflow의 KIS 실행만 명시적 `kis_shared.legacy` 래퍼로 연결했다. 캐시/공식 자료 읽기 모드는 잠금 없이 유지한다. 별도 계정이나 앱키는 필요하지 않다.
* `.github/scripts`의 구형 HTTP helper 4개와 inline 진단 workflow 5개는 퇴역 경로로 차단했다. 5개 구형 inline job의 개발 코드에는 if:false와 즉시 중단을 함께 두었다. 기존 20:30 수집 및 장중 workflow의 예약·활성 설정은 건드리지 않았다.

## 제3 참여자와 실조회 경계

신규 분석 프로세스의 deny-all 감사 보호와 기존 LiveKIS 비활성화는 유지한다. 원본 읽기/분석에는 중앙 잠금이나 KIS 자격이 필요하지 않다.

별도 `python -I kis_shared/worker.py` 프로세스만 명시적 실조회 경로다. 기본 비활성화이며 활성화에는 schema2, 사용자 승인 상태, 세 참여자 배포/권한/정책 기록, 소스와 실제 proof SHA256 일치, 실행 주체, 고정 요청 ID, 15분 이하 시간창, 000720 한 종목 및 HTTP 최대 2회를 모두 요구한다. 원자적 request scope가 중앙 원장에 남아 새 출력 폴더나 프로세스로 예산을 초기화하지 못한다. OAuth 응답은 파일로 저장하지 않으며 토큰은 메모리에서만 사용한다. 주문, 자동 재시도, 운영 DB 갱신은 없다.

`kis-limited-validation.yml`은 신규 개발 저장소에서만 준비한 수동 workflow다. enable 기본 false이고, enable=true라도 제안 정책이 disabled이므로 현재 API 호출은 차단된다. 이 workflow는 현재 실행하지 않았으며 운영 기본 브랜치에 등록됐다고 보고하지 않는다.

기존 schema1 정책은 제3 저장소를 거부한다. 새 schema2 제안 파일의 enabled/승인/배포/권한 플래그는 false다. 단순 verified=true로 차단을 없애지 않는다. 기존 활성 정책과 Secrets 값은 수정하지 않았다.

## Native proof와 회귀

`native-proof.json`: 33 PASS, 0 FAIL. 세 실제 체크아웃의 별도 OS 프로세스, 실제 GitBackend/Session, 장후 Providers, 장중 OAuth Broker/HttpAdapter, 구형 KIS 클라이언트와 새 CLI를 실행했다. 최종 HTTPS 전송만 합성 응답으로 대체하고 Python의 실제 socket/DNS 연결은 거절했다. 프로세스별 로컬 Git 이력과 응답/결과를 CI 아티팩트로 보존한다.

단독 3종, 양방향 점유, 3개 프로세스 CAS 경합, 만료, 소유권 상실, 원장 장애/지연, 미확인 HTTP, OAuth 오류, 강제 종료, 이전 잠금, 외부 소유자 해제 거절, 수동/예약 경합, 비활성 CLI, Secrets 없는 실행, 주문, 직접 opener/저수준 HTTP, redirect, 429, 재시작/예산 초기화 및 구형 작업 스레드 경로를 확인했다. TTL 이후에도 터미널 소유자 증거와 정확한 원장 버전·격리 시간이 있어야 명시적 복구가 가능함을 별도로 검사했다.

이 proof가 정규 수집 2,348종목 전체를 다시 실행했다는 의미는 아니다. 정규 runner 및 모니터 전체 실행 순서/검증은 기존 회귀 테스트로 확인하고, 실제 HTTP 경계는 같은 운영 클라이언트/Session에서 시험했다. 현재 GitHub 원장의 실제 자격으로 세 저장소가 쓰기 경쟁한 시험과 KIS 실계정 연동은 NOT_RUN이다.

CI의 `bundled_proof.py`는 권한 확대 없이 해시 고정된 실제 클라이언트 소스 스냅샷으로 동일 경합을 반복한다. 스냅샷 manifest와 출처 기준 커밋을 보존하며 코드 under test의 공통 core를 사용한다. bundled proof를 운영 활성화 증거로 사용할 수 없고, 활성화 검사는 `actual_source_trees` proof만 받는다.

로컬: 신규 통합 383 PASS, 장중 553 PASS 및 별도 219 subtest PASS. 장중 deny-network runner 531 PASS/네트워크 감사 이벤트 0. 장후 기존 전체 실행은 490 PASS(수집 시점의 테스트 집합), 이후 추가 구형 경로 검사 12 PASS. 최신 전체 테스트 수와 GitHub CI 상태는 `github-verification.json`의 실제 실행 결과를 우선한다. Native 33개는 pytest 합계에 중복 합산하지 않는다.

## PR 의존성과 운영 반영 순서

qweasd PR #4는 안전성 수정이며, qweasd PR #3의 KRX 자료/가격 승인 로직이나 분석 PR에 코드 의존하지 않는다. 신규 시스템 PR #1은 investment-integrated-dev 저장소의 PR이며 같은 번호의 다른 저장소 PR과 혼동하지 않는다. 장중 수정은 별도 PR이다. PR #3과 #4의 파일 교집합/merge-tree 결과는 `pr-dependencies.json`에 기록한다.

권장 순서: (1) 활성 실행 종료 및 구버전 runner 배제 확인, (2) PR #4와 장중 안전성 PR을 검토하고 같은 배포 창에서 소스 바인딩 영수증/기존 두 참여자 정책을 갱신, (3) 신규 시스템 PR #1 코드 배포, (4) 제3 참여자 중앙 저장소 권한의 무호출 확인, (5) 세 참여자 배포·실행 주체·소스 hash·신규 proof에 연결된 schema2 활성화 정책 승인, (6) 별도 승인 후 OAuth 포함 2회 제한적 실조회. PR #3은 잠금 수정과 별도로 검토 가능하다.

PR #4만 무작정 병합하면 기존 활성 정책의 source hash와 새 코드가 달라 정기 수집이 fail-closed로 차단될 수 있다. 이 작업에서는 정책을 임의 활성화하거나 main을 병합하지 않았다. 구형 실행 바이너리에는 새 감사 훅이 소급 적용되지 않는다. 모든 구버전 작업 종료를 확인하지 않고 공유 안전성을 주장하지 않는다.

## 남은 승인·설정과 한계

* 모든 PR main 병합 및 운영 정책/소스 영수증 반영은 별도 승인 필요. 기존 20:30 cron과 Word 브리핑 예약은 변경하지 않는다.
* 신규 저장소 실행 환경에서 기존 KIS_APP_KEY/KIS_APP_SECRET과 KIS_COORDINATOR_TOKEN이 사용 가능한지 실제 주입 검사는 미실행. Secret 값은 읽거나 변경하지 않았다. 필요한 중앙 권한은 qweasd Contents read/write와 소유 실행 복구 확인용 Actions read이며, 추가 확대/발급은 하지 않는다.
* GitHub를 통한 세 참여자 실제 자격 proof는 미완료. 현재 개발 Native proof만으로 이 권한을 입증하지 않는다. 중앙 원장 접근 실패는 OAuth 전 차단한다.
* KIS의 정확한 한도 적용 단위와 토큰 재발급 무효화 조건은 확정하지 않는다. 기존 독점 점유·요청 간격(최소 1.05초, 장중 기존 더 긴 간격 유지)·1회 OAuth·메모리 토큰·종료 후 60초 격리로 동시 사용을 금지한다. 인증과 조회를 분리해 동시 허용하지 않는다. Lease는 고정 TTL 내에서만 사용하고 자동 연장/만료 탈취를 하지 않는다.
* 개발 결과 LOCK_SAFETY_READY, 운영 반영 MERGE_REVIEW_REQUIRED. 실계정은 아직 READY_FOR_LIMITED_LIVE_TEST로 승인하지 않는다. 배포/자격 proof/활성화 정책/별도 실조회 승인이 남았다.

## 운영 및 가격 불변

실제 KIS/OAuth/KRX/OpenAI/Telegram 0회. 운영 main/Secrets/원장/원본/DB/JSON/20:30 예약/대시보드 변경 0건. 모든 관련 PR은 Draft/미병합 유지한다.

공식 가격 승인 0, PENDING_KRX 2,348, 기술지표 실전 승인 및 실전 매수 후보 승인 0 상태를 유지한다. 과거 695건의 694건 유형 식별/1건 근거 부족 기록을 수정하지 않는다. 이번 Native proof를 시장 데이터 공식 검증으로 사용하지 않는다.

검증용 GitHub 아티팩트는 14일 보존 후 만료 가능하다. 해시/요약/소스 manifest는 이 개발 브랜치에 저장하며 운영 데이터 세대를 덮어쓰지 않는다. 외부 저장소·새 자격·유료 인프라는 생성하지 않는다.
