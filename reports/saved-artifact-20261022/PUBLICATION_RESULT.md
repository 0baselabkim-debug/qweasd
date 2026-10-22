# 2026-10-22 보존본 복구 게시 결과

## 실제 실행

- 취소 순서: 대기 39778580042 → 수집 중 39760291133. 각각 21:44:38, 21:46:26 KST에 `completed/cancelled` 확인. 수집 중 실행의 게시 단계는 skipped.
- 다른 실행은 취소하지 않았다. 기존 유동성 복구 39772674704와 후속 보조 게시가 종료될 때까지 기다렸다.
- 최신 main `62eaa366a56b80d2c2047f3092c8069b22d258f3`의 새 작업 디렉터리를 사용했다. 저장 형식 수정 `34cccb65` 및 이후 정상 유동성·보조 데이터 변경을 보존했다.
- 운영 코드·워크플로·예약 변경 없음. 추가 시장 API·재수집 **0회**. 이번 복구와 별개로 이미 대기 중이던 정규 유동성 복구의 자체 호출은 이 수치에 포함하지 않는다.

## 불변 원본 및 체크포인트

- 증거 원본: `c51fabd469e5267a4f560e70bfe0f7dba8d6867e` (`recovery/artifact-20261022`), 변경 없음.
- ZIP: 57,118,617 bytes, SHA-256 `5bb6e061082877f0713b36a3c1af02f57c109c16991bec0f7cba937728696ef7`.
- bundle SHA-256 `7663f159e67b0c40eeb1278e2d7ef656036fd5b5af2bff4ab99b04c704fb47ac`.
- JSONL: 210,306,708 bytes, SHA-256 `db8d56a8dec27b71850c82add306020490325de1f3babff824a2802ca42eba10`.
- gzip: 18,814,941 bytes, SHA-256 `743096ce28fcaf2ae373ad710b7bb931d508414bc15cb344cff555bfeea701a9`.
- Journal 전행 해시·거래일·bundle 내용 일치. complete / 성공 2,432 / 제외 108 / 실패 0.
- 기존 저장 함수를 이용한 정상 체크포인트 브랜치 `data/daily-20261022` 커밋 `ab5529757a68cc3ebe7a2ae014f2db8776197362`. bundle·gzip·manifest는 증거 원본과 바이트 단위 일치.

## 준비·검증·게시

`prepare_daily_publish` → 전체 회귀 → `stage_daily_publication` → 저장 데이터만으로 `build_decision_support --fetch-checkpoints --install` → 독립 대조 → 일반 fast-forward push.

최신 main 갱신 후 이전 후보 폴더를 재사용한 1회 시도는 이전 manifest와 생성 중 DB의 세대 불일치로 안전하게 차단됐다. 검증 조건을 변경하지 않았고 새 빈 후보 경로에서 성공했다. 실패 후보는 게시하지 않았다.

- 회귀 329개 통과(최초 328.08초, 최신 main 기준 재검증 317.37초).
- 수급 원문 대조 2,432건 일치.
- TOP3 브리핑: 54개 수급 합계 + 33개 추가 값 + 업종 3개/종목 15개 순위 대조 통과.
- 전체 시장 v3: 50업종·2,432종목·20,256개 지표 대조 통과.
- Decision Support: 2,432종목·14,592개 수급값 대조 통과.
- 게시 직전 requested/queued/waiting/pending/in_progress 전체 조회에서 활성 작업 0건; main 변경 없음, 당일 미게시 확인.
- 게시 커밋: [01254eca3061e033aa69e94882e003bd1c2c96ba](https://github.com/0baselabkim-debug/qweasd/commit/01254eca3061e033aa69e94882e003bd1c2c96ba). snapshot 허용 산출물만 138개 변경. 강제 push 없음.
- 복구는 로컬 보존본으로 수행했으므로 원장의 run_id는 null, mode는 saved_artifact_recovery. 원본 collection source_run 39758472001은 보존했다. 기존 격리 검증 Actions 39777358552를 이번 실제 게시 실행으로 오인하지 않는다.

## 실제 공개 확인

2026-10-22 22:01 KST 공개 raw URL에서 manifest / daily-validation / publication-ledger / daily_briefing / briefing index / decision-support index 모두 HTTP 200 및 기준일 2026-10-22 확인.

공개 DB 27,275,264 bytes를 직접 수신해 SHA-256 `33ac8b315fe75a42d31c5c53715e8564852ef1b13b5e2d70f1eb74d10b1f4c40` 확인. 모든 JSON의 원천 해시와 일치.

- v3 세대: `flow-v3-2026-10-22-33ac8b315fe75a42d31c5c53715e8564852ef1b13b5e2d70f1eb74d10b1f4c40`
- 보조 세대: `decision-v1-2026-10-22-701cce68a4c31986cfa8176fe7415ac5a3f591bae449695412367e0c1b07ff08`
- 보조 source_dataset_id는 위 v3 세대와 일치.

## 보류 유지

Decision Support는 passed_with_metric_holds. 당일 원시 OHLCV 2,432건은 있으나 공식 검증을 통과한 당일 OHLCV는 0건이므로 SMA/ATR/일목 등은 보류했다. 외국인 20일 수급 2,419종목, 재무 2,409종목. 공급자 최종 확정은 UNVERIFIED를 유지했다.

수집·저장 검증 통과를 공급자 최종 확정으로 바꾸지 않았다. 사용자용 일일 브리핑 본문·Word 파일을 생성하거나 발송하지 않았다.

## CI·라이브 앱

- 게시 커밋 CI: https://github.com/0baselabkim-debug/qweasd/actions/runs/39783137250
- 공개 앱 브라우저 검사: https://github.com/0baselabkim-debug/qweasd/actions/runs/39783232360
- 공개 앱 검사 **실패**: HTTP 200이지만 실제 revision `5545fce140411702b77ad19419b1bba03fc25a62`, 실제 data SHA `c88b68bbeab6512caca5c9f0ea01fea0535b216e6a0bf4055741b9899ae96948`로 10/21 이전 게시 세대를 계속 제공했다. 기대 revision은 `01254eca...`, DB SHA는 `33ac8b...`이다.
- 앱 검사 결과: [result.json](https://github.com/0baselabkim-debug/qweasd/blob/evidence/dashboard-39783232360/reports/live-dashboard/result.json). 브라우저는 데이터 세대 대조에서 중단했으므로 이후 조작·CSV 검증 성공을 주장하지 않는다.
- Streamlit 관리 권한이 없는 공개 검사로 확인할 수 있는 사실은 구버전 제공까지다. 배포 지연·연결 브랜치·빌드 오류 중 근본 원인은 관리 로그 없이 확정하지 않는다. 기존 계정으로 My apps → 해당 앱 ⋮ → Settings에서 main/app.py 연결·로그 확인 후 필요하면 Reboot app, 같은 데이터 해시 재확인이 남았다. 자격 증명·권한 확대는 요청하거나 적용하지 않았다.
- 게시 커밋 CI 최종 상태는 실행 종료 후 아래에 기록한다.

게시 커밋 CI **success** 확인. 전체 pytest 단계와 Streamlit HTTP health 단계 모두 success. 원본 evidence 브랜치 c51fabd...와 운영 main 01254eca...는 최종 재조회에서도 동일했다.
