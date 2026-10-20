# 연구 코드 자동 검증

- 분석·수집·검증 코드 커밋: `b42dc9f817098378cc0106eb291846c8080a0f1d`
- GitHub Actions: [39467436494](https://github.com/0baselabkim-debug/qweasd/actions/runs/39467436494)
- 실행 방식: `workflow_dispatch` (예약 실행 아님)
- 결과: **success**
- 전체 pytest 단계 및 Streamlit HTTP 상태 점검: 통과
- 로컬 최종 전체 회귀: **308 passed in 256.70s**. [원문](tests.txt)
- 서로 다른 Python 해시 시드의 2회 전체 오프라인 재현: 주요 결과 7파일 동일. [해시](reproducibility.json)

이후 결과물 커밋은 보고서 생성 문구·보고서·CSV·연구 DB·ZIP 및 검증 기록을 정리한다.
분석 계산 코드의 CI 커밋과 결과물 게시 커밋을 구분한다. 운영 앱을 이 연구 코드로 배포하지 않았다.

최초 연구 CI 39467099716은 결과 파일 날짜 순서의 재현성 수정 후 대체 실행을 위해
대기 중 취소했다. 운영 수집 실행을 취소한 것이 아니다.
