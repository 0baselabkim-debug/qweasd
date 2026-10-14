# 한국 주식 · 자금 흐름 스크리너

2026-10-14에 수집한 실제 KRX 기준 외국인 수급 2,430종목과 공시일을 확인한 OpenDART 연간 재무 2,388종목을 조회하는 Streamlit 대시보드입니다. 투자 후보 탐색용이며 매수 추천·주문 기능은 없습니다.

## 웹 배포: 코드 수정 없이

1. [Streamlit Community Cloud](https://share.streamlit.io)에 로그인합니다.
2. GitHub 연결이 없다면 **Workspaces → Connect GitHub account → Authorize streamlit**을 선택합니다. GitHub 계정은 `0baselabkim-debug`입니다.
3. **Create app → Yup, I have an app → Paste GitHub URL**을 선택하고 아래 주소를 붙여 넣습니다.

   `https://github.com/0baselabkim-debug/qweasd/blob/main/app.py`

   개별 항목으로 입력하는 화면이면 Repository `0baselabkim-debug/qweasd`, Branch `main`, Main file path `app.py`입니다.
4. **Advanced settings**에서 Python **3.12**를 선택합니다. **Secrets는 비워 두고 Save**를 누릅니다. 이 화면은 저장된 자료만 조회하므로 API 키가 필요하지 않습니다.
5. **Deploy**를 클릭합니다. 완료 후 브라우저에 표시되는 `https://…streamlit.app` 주소가 실제 대시보드 URL입니다.

App URL 항목을 비워 두면 서비스가 사용 가능한 주소를 자동으로 정합니다. 이 저장소는 공개 저장소이며, 기본 배포 화면도 공개입니다. Community Cloud 무료 호스팅을 사용하며 유료 리소스는 만들지 않습니다.

## 화면에서 확인할 내용

- 기준일 `2026-10-14`, 실제 수급 `2,430종목`, 공시일 확인 재무 `2,388종목`
- 시장 선택, 업종 선택, 종목 검색·정렬, 종목 상세 재무 및 항목별 점수, CSV 다운로드
- `KOSPI · 전기·전자 → 삼성전자`: 당일 외국인 순매수 **-1,900.64억원**
- `KOSPI · IT 서비스 → NAVER`: 당일 외국인 순매수 **47.95억원**

5·20일 평가에 필요한 이력이 부족하므로 종합 점수·순위는 **데이터 축적 중**입니다. 빈 점수의 가중치는 다른 항목으로 재배분하지 않습니다. 원천 최종 확정 상태는 **미검증**으로 표시합니다.

## 보존과 데이터 범위

- `snapshot/dashboard.duckdb`는 기존 로컬 대시보드 DB를 변경 없이 복사한 공개 조회용 자료입니다. 해시는 `snapshot/manifest.json`에 기록했습니다.
- `snapshot/verification.json`에는 기존 독립 검증의 3종목·2업종 비교 결과가 있습니다.
- KRX 공식 당일 시세 API가 빈 응답이어서 표시 종가는 기존 KIS 수급 응답의 **KRX 종가**입니다. 시가총액·PER·PBR은 확보 전까지 평가 보류입니다. KRX/NXT 통합 자료로 표시하지 않습니다.
- 재무는 **2025년 연간 및 2024년 비교**, 실제 공시일 기준입니다. 최신 분기 자료와 구분합니다.
- 이번 배포는 **2026-10-14 저장 자료**를 제공합니다. GitHub Actions의 추가 수집 결과가 웹 화면으로 자동 동기화되는 기능은 아직 연결하지 않았습니다. 날짜를 새로 꾸미거나 외부 API를 다시 호출하지 않습니다.
- 기존 KIS 수집 워크플로와 KRX·OpenDART 연동 코드는 변경하지 않았습니다. `.env`, 토큰, 계좌 정보는 배포 자료에 포함하지 않습니다. GitHub Secrets를 Streamlit에 복사할 필요가 없습니다.

## 개발자 검증

Python 3.12에서 `requirements.txt`를 설치하고 `streamlit run app.py`로 실행합니다. 로컬 `data/dashboard.duckdb`가 있으면 사용하고, 없으면 GitHub에 포함된 스냅샷을 읽습니다. 읽기 전용 연결이며 DB를 웹 화면에서 수정하지 않습니다.

[Dashboard deployment check](https://github.com/0baselabkim-debug/qweasd/actions/workflows/dashboard-check.yml)는 인증정보 없이 의존성 설치, 실제 자료 무결성, 점수 경계 사례, 화면 조작, HTTP 상태를 검사합니다.

공식 배포 안내: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
