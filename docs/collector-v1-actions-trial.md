# GitHub Actions 일회성 공식 마스터 시험

현재 실제 제어 파일은 **master 단계**다. 별도 승인 후 사용할 market 단계 코드는 [2차 준비 결과](collector-v1-market-ready.md)에 설명한다. 이번 준비 push는 제어 파일을 변경하지 않는다. 1차 master 실행에서 종목별 수급/가격/KRX/기업/매크로 호출, 정기 예약, main 변경은 실행하지 않는다. 사용자 확인에 따라 Repository Secrets 6개의 등록 사실은 알고 있지만 주입 및 유효성은 실제 Actions에서만 확인한다.

## 실행 경로와 안전장치

- `.github/workflows/collector-v1-trial.yml`: `push`, 정확한 기능 브랜치 `feat/market-data-collector-v1`, 정확한 제어 파일 `.github/trials/collector-v1.json` 변경만 반응한다. workflow_dispatch 기본 브랜치 등록을 요구하지 않는다.
- 작업 코드/워크플로 push와 시험 제어 push를 분리한다. 실행 직전 Git 이벤트의 before/after와 실제 부모/HEAD를 대조하며, **단 하나의 제어 파일만 바뀐 단일 커밋**을 요구한다. 다른 파일 변경, 강제 push, 브랜치 생성 이벤트 및 Actions Re-run은 차단한다.
- repository/ref/event를 코드와 job 양쪽에서 검사한다. `contents: read`, `actions: read`만 사용하고 checkout 인증을 남기지 않는다. 공통 `kis-validation-auth` 동시 실행 그룹을 사용한다.
- 제어 파일의 schema/stage/asof/max_total_http/요청 ID/이전 run ID를 검사한다. stage=master, 누적 20, KST 관측 날짜 일치만 허용한다. 날짜가 바뀌면 HTTP 직전 다시 차단한다. 다른 날짜로 자동 대체하지 않는다.
- GitHub Actions 자체의 GITHUB_TOKEN으로 같은 워크플로의 최신 이전 실행을 확인한다. 첫 실행 외에는 최신 실행의 아티팩트를 정확히 복원해야 한다. 이전 run 미완료, artifact 누락/만료, 원장/파일 SHA256/날짜/trial ID 불일치, 실패한 이전 시험은 차단한다. 이전 기록을 무시하고 예산을 0으로 초기화할 수 없다.
- 원장 기록 후 HTTP, 순차 요청 1.05초 간격, 자동 재시도 없음, 첫 오류 시 중단, 복원된 원장 포함 최대 20회다. 1차는 KIS 인증 1 + 공식 휴장일 확인 1 + 공식 KOSPI/KOSDAQ 마스터 각 1로 **정상 경로 4회**다. 휴장일/인증/네트워크 실패 후 마스터 호출을 계속하지 않는다.
- KIS calendar는 실제 거래일 여부만 확인한다. 종목별 가격·수급은 예산 계층에서도 허용하지 않는다. KRX/DART/ECOS/FRED 네트워크 연결이나 키 유효성을 이 단계에서 시험하지 않는다. 패키지 설치 및 GitHub 제어/아티팩트용 통신은 제공처 HTTP 원장과 별개다.

GitHub API 접근이 Actions에서도 차단되면 사전 점검에서 BLOCKED이며 제공처 요청을 시작하지 않는다. main에 워크플로를 추가하거나 비공식 경로로 우회하지 않는다.

## 검증 및 아티팩트

`collector_v1/trial.py`는 기존 Providers/Store/Budget/master 파서를 사용한다. 원 ZIP bytes를 gzip+SHA256으로 보존하고 전 시장 목록을 저장한다. 시장별 최소 행 수(KOSPI 500 / KOSDAQ 1000), 전체 2000 이상, 시장 내/시장 간 종목코드 중복, 지정 세 종목의 코드·시장·공식 이름을 검사한다. 거래정지 등 분석 제외 속성도 보존하며 포함 확인을 분석 적격성으로 바꾸지 않는다. 최소 행 수 검사는 실제 모집단의 완전성을 수학적으로 보증하지 않으며, 대상 누락·레이아웃 변경·중복 검출을 위한 보수적인 가드다.

- `collector-v1-trial-state`: raw/, observations/, requests.json, state.json, errors.json, last-run.json, masters.json, receipt.json, state-manifest.json. 실패 시에도 원문/원장을 보존하고, 수집 중단 시 별도 finalizer가 남은 원장을 봉인한다. 업로드 실패/강제 runner 종료 시 다음 실행은 이전 아티팩트 복원이 불가능하므로 차단한다.
- `collector-v1-trial-preflight-<run_id>`: 비밀값 없는 사전 점검 또는 차단 원인.
- 실제 Secret 문자열과 임시 토큰이 출력 파일에 들어갔는지 검사한다. 노출이 의심되면 state 아티팩트를 업로드하지 않고 BLOCKED로 남긴다. 토큰·인증 응답·헤더는 저장하지 않는다.
- receipt는 secret **존재 여부**만 기록한다. 실제 KIS 인증 성공과 나머지 키의 유효성을 구분한다. master 공급자 최종성은 UNVERIFIED다.
- snapshot 파일 목록과 SHA256을 전후 대조한다. 운영 DB·snapshot·Git refs를 수정하는 명령은 없다.

아티팩트는 30일 보존한다. 보존 기간이 끝나거나 실패 원장을 잃으면 새 시험을 진행하지 말고 별도 검토한다. 정상 원문은 같은 날짜에 재사용하며, HTTP 20회는 재실행까지 누적한다. 실패한 시험을 재실행으로 덮어쓰지 않는다.

## 제어 파일 예시 (설명용)

```
{"schema":1,"trial_id":"master-20261026","request_id":"master-20261026-01","stage":"master","asof":"2026-10-26","previous_run":null,"max_total_http":20}
```

초기 제어 파일은 코드 검증/push 후 별도 커밋으로만 추가한다. 그 커밋의 push가 사용자 승인된 1차 시험이다. KST 날짜가 지났다면 이 파일로 수집하지 않는다. 다음 실제 거래일과 새 제어 파일은 별도 검토하며 예약을 만들지 않는다.

2차 종목별 실연결 코드는 별도 단계로 준비되었으며, 현재 제어 파일로는 실행되지 않는다. 1차 성공 및 원문/원장 아티팩트 확인 후 사용자 별도 승인과 제어 파일 변경이 있어야 한다. 정확한 아티팩트 핀·원장 복원·날짜 가드는 [2차 준비 결과](collector-v1-market-ready.md)를 따른다.
