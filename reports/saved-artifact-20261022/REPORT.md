# 2026-10-22 저장 artifact 복구 — 검증 완료, 운영 게시 보류

2026-10-22 21:27 KST 확인. 운영 main은 `34cccb65c36f09643a2d3799e5404cf4ef28abf5`.
AGENTS.md는 작업 디렉터리·상위 경로 및 최신 main 트리에서 발견되지 않았다.
파일 크기 수정은 재구현하지 않았으며 기존 `checkpoint_storage.pack/unpack/restore`,
`persist_daily_checkpoint`와 기존 검증·게시 함수를 재사용했다.
해당 코드의 [CI 39775584266](https://github.com/0baselabkim-debug/qweasd/actions/runs/39775584266)은 성공했다.

## 실제 확보·검증

로컬 `gh api`의 기존 인증으로 artifact 메타데이터 조회는 성공했으나 ZIP 다운로드의
리다이렉트 대상 `productionresultssa4.blob.core.windows.net`에서 403이 발생했다.
권한 확대나 프록시 변경 없이, 이미 사용 중인 GitHub Actions 실행 환경과
기존 정규 워크플로와 동일한 `contents: write`, `actions: read` 권한으로 읽었다.
정규 수집 job은 건너뛰었고 시장 인증정보도 복구 job에 전달하지 않았다.

[격리 실행 39777358552](https://github.com/0baselabkim-debug/qweasd/actions/runs/39777358552)은
artifact 검증·증거 보존·prepare_daily_publish·전체 회귀 테스트 모두 성공했다.
당시 코드 `f30f433d9cecabe5d39a78ce6c8521fe9368f650`, 기존 테스트 모집 329개.
로컬 압축·복원/실행 보호 관련 39개도 통과했다.

| 항목 | 결과 |
|---|---|
| 원본 실행 / artifact | 39758472001 / 12422660017 |
| ZIP 크기 | 57,118,617 bytes |
| ZIP SHA-256 | `5bb6e061082877f0713b36a3c1af02f57c109c16991bec0f7cba937728696ef7` |
| bundle 크기 / SHA-256 | 36,146,603 / `7663f159e67b0c40eeb1278e2d7ef656036fd5b5af2bff4ab99b04c704fb47ac` |
| 거래일 / 상태 | 20261022 / complete |
| 성공 / 제외 / 실패 | 2,432 / 108 / 0 |
| 원본 JSONL 크기 | 210,306,708 bytes |
| gzip 크기 | 18,814,941 bytes, 90 MiB 미만이므로 1개 파일 |
| 원본·복원 JSONL SHA-256 | `db8d56a8dec27b71850c82add306020490325de1f3babff824a2802ca42eba10` |
| 원본 바이트 일치 / 잘린 마지막 행 | 일치 / 0 bytes |
| 원본 당일 수급 → 후보 DB 독립 대조 | 2,432건 모두 일치 |
| 추가 시장 API 호출 | **0회** (기존 운영 수집은 별도 실행) |

원본 ZIP과 파일은
[증거 브랜치 recovery/artifact-20261022](https://github.com/0baselabkim-debug/qweasd/tree/recovery/artifact-20261022)에 보존했다.
증거 커밋은 `c51fabd469e5267a4f560e70bfe0f7dba8d6867e`이다.
진단 JSON을 포함한 모든 원본 ZIP 내용은 삭제하거나 수정하지 않았다.
안전한 경로·중복·파일 타입·압축 해제 크기 검사 후 추출했고, 로컬에 가져온 ZIP도
크기·해시를 다시 검증했다. JSON 파싱·재직렬화로 JSONL을 압축하지 않았다.

최초 실행은 수집 단계 성공 후 체크포인트 보존 단계에서 실패했다.
이번에 확보한 plaintext JSONL이 GitHub 단일 파일 제한보다 큰 사실을 확인했다.
수정된 무손실 gzip을 사용한 **격리 증거 브랜치 push는 성공**했다.

## 게시 예행검증 — 공개 게시 아님

기존 `prepare_daily_publish`로 원본 main DB를 변경하지 않고 후보를 생성했다.
회귀 성공 후 `stage_daily_publication.stage`도 별도의 로컬 검증 폴더에만 적용했다.
실제 main 게시원장에는 기록하지 않았다.

- 후보 DB 수급 총 63,147행, 기존 보존 검사 통과.
- 후보 manifest / daily-validation / ledger / v3 index의 기준일은 모두 2026-10-22.
- 공통 원천 DB 해시:
  `26019a11fbac24d38e830fae3a3a3350e0388da0e763c3131d7d0d43836b1e06`.
- 후보 v3 세대:
  `flow-v3-2026-10-22-26019a11fbac24d38e830fae3a3a3350e0388da0e763c3131d7d0d43836b1e06`.
- 50개 업종 / 2,432종목 / 51개 JSON 파일 생성 및 기존 독립 검사 통과.
- TOP3 브리핑 원본 대조 54건, 추가 값 대조 33건, 업종 3개·종목 15개 순위 검사 통과.
- 10/22 보조 decision-support 자료는 생성·게시하지 않았다.
- 후보의 게시 시각·ready 상태는 기존 staging 함수가 만든 준비 메타데이터이며,
  실제 공개 완료의 증거로 사용하지 않는다.

## 실제 게시 차단

repository-level `/actions/runs`를 requested/queued/waiting/pending/in_progress로 각각
조회하고 정확한 `.github/workflows/daily-dashboard.yml` 경로를 필터링했다.
21:27 확인 당시 다음 작업들이 남아 있었다.

- 39760291133: 수정 전 SHA 45498d61의 main 정규 수집, in_progress.
- 39772674704: 유동성 복구, pending.
- 39770486247, 39778225150: 보조 가격 증거, pending.

격리 검증 실행은 처음에 기존 워크플로 이름을 사용해 후속 이벤트 1건이 만들어졌다.
그 후속 가격 workflow는 `workflow_run.head_branch == main` 조건 때문에 이 복구
브랜치를 실제 수집 대상으로 허용하지 않는다. 향후 반복 시 이벤트 자체도 만들지
않도록 **복구 브랜치에서만** 워크플로 이름을 별도로 바꿨다. 기존 작업은 취소하지 않았다.

사용자의 활성 작업 중 게시 금지 조건에 따라 **운영 main에 복구 게시하지 않았다**.
운영 DB·JSON·일정·정규 수집 코드 및 data/daily-20261022 체크포인트 브랜치를 변경하지 않았다.
현재 manifest / 검증 / 원장 / v3 / decision-support는 모두 10/21이며 원천 해시
`57ee8f4ea5bbeef7afd949b7b772745516b1a747e866c6aa6eecaf76d3a8b378`로 일치한다.
실제 게시 커밋은 없으며 라이브 앱 반영도 검증하지 않았다.

## 남은 실행 절차

활성 수집·게시 및 대기 작업이 끝나면 repository-level 상태와 최신 main 원장을 다시
확인한다. 10/22가 이미 게시됐으면 복구하지 않는다. 미게시라면 **최신 main의 별도
checkout**에서 보존된 bundle로 prepare → 회귀 → stage를 다시 수행한다.
이번 후보는 준비 당시 main 기준이므로 다른 정상 데이터 갱신 뒤 그대로 밀어 넣지 않는다.
최종 상태 재확인 후 스냅샷 파일만 단일 커밋·일반 push하고 실제 공개 기준일·해시·세대를
검증한다. 보조 자료는 별도 생성 상태를 확인한다.

**이 복구 실행 브랜치를 main에 병합하지 않는다.** 브랜치의 수동 진입점은 운영
워크플로를 대체하기 위한 것이 아니다. 신규 collect/verify_saved/시장 API 호출,
작업 취소, 강제 push, 자동 지연 실행은 사용하지 않았다.
