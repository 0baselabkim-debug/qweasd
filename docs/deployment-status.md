# 기존 Streamlit 앱 운영 검증

실제 접속 주소: https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/

2026-10-14 기준 실데이터를 유지한 채 기존 저장소 `main`과 기존 앱을 업데이트했습니다. 새로운 웹앱이나 유료 서비스를 만들지 않았습니다. 화면은 저장된 DuckDB 스냅샷을 읽으며 API 키가 필요하지 않습니다.

[외부 브라우저 검증](https://github.com/0baselabkim-debug/qweasd/actions/runs/38603069672)에서 HTTP 200, 보완된 재무 표시, 원전 테마 선택과 두산에너빌리티 상세 조회, 공식 업종 선택, 종목명 정렬, NAVER 상세 정보, CSV 다운로드를 확인했습니다.

첫 배포에서 실행 중인 구버전 계산 모듈/캐시 때문에 `flow_observations`를 찾지 못하는 오류를 발견했습니다. 소스·데이터 버전에 따라 모듈과 캐시를 갱신하도록 수정하고 실제 공개 앱에서 재검증했습니다. 이후 배포에서도 구버전 모듈을 강제로 남겨 재현하는 자동 테스트를 수행합니다.

최신 확보 수·시계열 범위·검증 결과는 [섹터·테마·자동 게시 보고서](../reports/sector/REPORT.md), [배포 데이터 해시](../snapshot/manifest.json), [자동 테스트](https://github.com/0baselabkim-debug/qweasd/actions/workflows/dashboard-check.yml)에서 확인할 수 있습니다.

화면의 분석일은 2026-10-14입니다. 일일 수집 결과를 검증 후 웹 스냅샷으로 자동 게시하도록 연결했습니다. 실제 보존 자료의 재처리·게시로 경로를 확인했으며, 새 거래일 전체 수집과 3거래일 연속 운영은 미검증입니다. 자료를 현재 날짜로 가장하지 않습니다.

최종 배포에서는 재무 2,415종목, 20거래일 수급 2,427종목, 종합 평가 1,161종목을 확인했습니다. [업종 화면](dashboard-preview.png) · [테마 화면](dashboard-theme.png) · [종목 상세 화면](dashboard-detail.png).

최신 [실제 게시 실행](https://github.com/0baselabkim-debug/qweasd/actions/runs/38650326964)과 [외부 브라우저 검증](https://github.com/0baselabkim-debug/qweasd/actions/runs/38650545213)이 성공했습니다. HTTP 200, 재무 2,415개·20일 수급 2,427개·종목 종합 평가 1,161개, 자동 게시 상태·39개 테마 기업과 선택/정렬/상세/다운로드를 확인했습니다.

5·20거래일 수급 추이를 기존 앱에 반영했습니다. 최신 [검증 보고서](../reports/flow-trends/REPORT.md), [실제 브라우저 실행](https://github.com/0baselabkim-debug/qweasd/actions/runs/38655148553), [배포 코드 검사](https://github.com/0baselabkim-debug/qweasd/actions/runs/38655147831)를 확인할 수 있습니다. DB와 기존 종목 점수는 유지했습니다.
