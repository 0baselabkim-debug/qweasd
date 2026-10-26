# Actions 1차 공식 마스터 시험 결과 — 2026-10-26

## 실행 증거와 판정

[GitHub Actions 실행 40289601356](https://github.com/0baselabkim-debug/qweasd/actions/runs/40289601356)의 공개 실행 요약에서 **Success**, 전체 1분 10초, master job 1분 7초, 아티팩트 2개를 확인했다. 시작 표시 시각은 2026-10-26 08:02 UTC / **17:02 KST**다. 수집 시간만의 상세 수치는 이 환경에서 receipt를 다운로드하지 못했으므로 별도로 단정하지 않는다.

실행 브랜치는 `feat/market-data-collector-v1`, 실행 코드 준비 커밋은 `59369894`, 제어 파일만 변경한 실제 실행 커밋은 `744d26e4e6249b727ddbf95b3e2ee4b6272e8245`다. 기준일은 2026-10-26, stage는 master다. 이후 보고서/추가 가드 push는 제어 파일을 건드리지 않아 재수집을 시작하지 않는다.

| 확인 항목 | 상태 | 근거 및 범위 |
|---|---|---|
| Workflow 등록·실행 | PASS | 지정 브랜치/제어 파일 push로 실제 run 생성 및 Success 확인 |
| Repository Secrets 6개 주입 | PASS | 한 개라도 비어 있으면 수집 단계가 실패하도록 강제된 가드를 통과. 값은 출력하지 않음 |
| KIS 인증·공식 휴장일 확인 | PASS | 마스터 이전 필수 단계 통과. 당일 opnd_yn=Y만 허용 |
| 두 시장 공식 마스터 수집 | PASS | 마스터 단계 및 전체 job 성공. 가격/수급 엔드포인트는 허용하지 않음 |
| 종목 코드/시장/이름 및 중복/인원 가드 | PASS (Actions 내부) | 각 시장 최소 행 수, 전체 2000 이상, 중복 및 지정 3종목 검사 필수 |
| 원문 SHA256·원문/관측 메타데이터 연결 | PASS (Actions 내부) | 원문 gzip 보존, 원문 및 메타데이터 해시 검사, 결과 봉인 단계 통과 |
| 실제 제공처 HTTP 예산 | PASS | 첫 실행 정상 경로는 인증·휴장일·KOSPI master·KOSDAQ master **4회**. 자동 재시도 없음. 누적 20회 상한 |
| 아티팩트 생성·공개 메타데이터 확인 | PASS | 아래 이름·크기·SHA256을 실행 화면에서 직접 확인 |
| Codex에서 아티팩트 다운로드·독립 재해시 | BLOCKED | GitHub API Forbidden, 공식 웹 아티팩트 다운로드 URL 404. 인증 우회하지 않음 |
| 오프라인 회귀 테스트 | PASS | 실행 준비 시 관련 116개 통과. 최종 경계 가드 추가 후 **117개 / 2.42초** 통과. 신규 12개 포함 |
| 2차 종목별 가격·수급 수집 | NOT_RUN | 코드/제어 파일은 master 단계만 허용. 별도 승인/실행 필요 |

원문 ZIP 자체의 SHA256 값, 정확한 전체 종목 수, 개별 Secret 유효성을 원격 receipt를 열어 직접 재확인했다고 주장하지 않는다. 위 내부 PASS는 성공한 워크플로의 필수 검사에 근거한다. 정확한 종목 수와 원문 SHA256은 `receipt.json`/`observations/`에 기록되어 있다. 전체 아티팩트 ZIP의 digest와 원문 ZIP의 해시는 서로 다른 값이다.

## 제공처별 연결

| 제공처/경로 | 상태 | 검증 범위 |
|---|---|---|
| KIS openapi.koreainvestment.com | PASS | 인증·휴장일 확인만. 종목별 API 접근 성공으로 확대 해석하지 않음 |
| KIS 공식 다운로드 new.real.download.dws.co.kr | PASS | KOSPI·KOSDAQ ZIP 수집 및 내부 검증 |
| KRX | NOT_RUN | Secret 존재 여부만 검사; 호출하지 않음 |
| OpenDART | NOT_RUN | Secret 존재 여부만 검사; 호출하지 않음 |
| ECOS | NOT_RUN | Secret 존재 여부만 검사; 호출하지 않음 |
| FRED | NOT_RUN | Secret 존재 여부만 검사; 호출하지 않음 |

6개 Secret 중 KIS 인증에 사용된 두 값 이외의 유효성·API 권한은 이번 시험으로 입증되지 않았다. FRED의 과거 Codex 프록시 차단을 Actions 연결 실패로 간주하지 않는다.

## 지정 종목과 다음 단계

| 종목 | 공식 마스터 포함·시장·이름 | 가격·수급·KRX·DB/JSON 대조 |
|---|---|---|
| 현대건설 000720 / KOSPI | PASS (Actions 필수 검사) | NOT_RUN |
| LG씨엔에스 064400 / KOSPI | PASS (Actions 필수 검사) | NOT_RUN |
| 플리토 300080 / KOSDAQ | PASS (Actions 필수 검사) | NOT_RUN |

정상 경로 4회는 첫 실행에 이전 데이터가 없고 각 요청을 딱 한 번 호출하는 코드와 run 성공에 근거한다. 원장 원문은 아티팩트에 있으며 Codex에서 내려받아 개별 행을 읽은 것은 아니다. GitHub 제어/패키지 설치 통신은 제공처 호출 원장과 구분한다.

**1차 성공을 근거로 2차 시험 경로를 준비할 수 있다. 실제 2차 실행은 별도 사용자 승인 후에만 가능하다.** 1차의 정확한 아티팩트·원장·세대/날짜/해시를 복원해야 하며 4회를 포함한 누적 20회를 적용해야 한다. 잘못된/누락된 아티팩트, 이전 시험 실패, 날짜 불일치는 차단해야 한다. 현재 Codex의 다운로드 접근 제한을 해결하거나, 정상 접근 가능한 Actions에서 승인된 복원 절차를 수행해야 한다. 현 master-only 워크플로는 2차를 자동 실행하거나 예약하지 않는다.

## 보존 아티팩트

동일한 [실행 요약](https://github.com/0baselabkim-debug/qweasd/actions/runs/40289601356)의 Artifacts 영역에 있다. GitHub 로그인 및 적절한 읽기 권한이 필요할 수 있다.

| 이름 | 공개 표시 크기 | artifact ID |
|---|---:|---|
| collector-v1-trial-state | 547 KB | 12636170227 |
| collector-v1-trial-preflight-40289601356 | 32.4 KB | 12635234947 |

- state artifact SHA256: `5cbc6ef7db90e4a1f1aaaf95b5e8d78a90bec648a201a488a68e119b42b22a1a`
- preflight artifact SHA256: `f25189399b53a2ff6d2f77562361429076b4115c71ad58cd87e9838b62e0cf0a`

state 안에는 raw/observations, 호출 원장, 수집 상태, 검증된 master JSON, receipt 및 파일별 해시 manifest가 보존된다. preflight 안에는 제어 파일/코드/이전 run 바인딩과 snapshot 비교 기준이 보존된다. 아티팩트 보존 기간은 30일이다. 수동 삭제/만료로 원장을 복원하지 못하면 다음 시험을 진행하면 안 된다.

로컬 공개 화면 관측 결과는 `data/collector-v1-trial-observer/observed-result.json`에 저장했다. 이 파일은 실제 원천 아티팩트의 대체물이 아니다.

## 운영 영향과 후속 수정

운영 snapshot 743개 파일은 로컬 전후 SHA256과 목록이 동일하며, Actions도 동일 검사를 필수로 수행했다. main 병합, 원격 main push, 전체시장 종목별 수집, 기존 예약 복구, 신규 예약 생성은 수행하지 않았다.

성공 실행 후 날짜 경계 가드를 추가 보강했다. 1.05초 대기 중 자정을 넘는 경우에도 HTTP 전에 날짜를 다시 검사하도록 했으며 새 오프라인 테스트로 검증했다. 실제 실행은 KST 17시대여서 이 경계에 해당하지 않았다. 해당 후속 변경은 제어 파일을 변경하지 않아 추가 실수집을 실행하지 않는다.

Actions 화면에는 기존 v4 액션의 Node 20 폐기 및 ubuntu-latest 이행 안내가 표시됐지만 실패는 아니었다. 성공한 시험을 반복하거나 새로운 API를 호출해 이를 해소하지 않았다.
