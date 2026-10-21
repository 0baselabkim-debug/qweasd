# 검증 기록

- 로컬 전체 회귀: `416 passed in 262.51s`.
- 연구 전용 회귀: `174 passed`.
- 실제 원본 대조: `independent-check.json` — 공식 10/16 2,159개, 10/20 2,429개, 원본 수급 금액/수량 각각 3,780행 통과.
- 이전 227개 중 살아남은 123개: 기존 수급값·연구 그룹 변동 0.
- 기존 파일 1,884개 해시 불변. 실제 등록 신호 0. 운영 main으로 병합하지 않음.
- GitHub 공식 조회 실행: https://github.com/0baselabkim-debug/qweasd/actions/runs/39550940669 — 성공. HTTP와 내용 검증은 보고서/별도 원본 검사로 판단.
- 코드 및 실제 데이터 CI: 최종 실행 메타데이터를 `github-ci.json`에 별도 기록한다. 로컬 시험을 CI 성공으로 대신하지 않는다.

테스트 데이터는 `tests/`의 가상 경계 사례에만 사용하며, `official-crosscheck/responses/`·CSV·연구 저장소에는 실제 보존 자료만 사용했다.
