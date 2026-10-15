# 매수·매도 가이드 / 개인 매매일지 구현 결과

배포 URL: https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/

**코드와 공개 화면은 배포됨. 실제 매매 신호와 개인 매매일지 실사용은 아래 데이터·인증 조건 때문에 아직 대기 상태다.**

## 구현

- 상세 화면 네 번째 KPI 바로 위에 골드 16px 매수 가이드. 매수(복수 유형) / 관망 / 판정 보류를 별도 규칙 엔진으로 처리.
- SMA5/20/60/120, 일목 9/26/52 및 현재·미래 구름대 구분, 후행스팬, Wilder ATR14, 확인된 피벗 지지·저항, 손익비. 계산 규칙은 [상세 기준](../../docs/trading-guidelines.md) 및 [설정](../../technical_rules.json).
- 매매일지 독립 메뉴, 로그인, + 추가 팝업, 종목 자동완성, 날짜·단가·수량, 취소/저장/실패 처리, 개별 수정·삭제 및 수량 가중평균 합산. 매도 가이드가 마지막 열.
- Supabase Auth + PostgreSQL RLS, 감사 이력, revision 동시 수정 검사, 사용자별 추적 손절 최고 기준. 공개 앱은 사용자 JWT만 사용하며 서버 전용 키는 Actions에만 등록.
- 별도 기술 데이터 캐시·일일 작업: 기존 수급 수집 성공 후와 평일23:45 KST, 대상 최대30종목, 완료 날짜 제외 및 실패 재시도. 개인별 결과도 비공개 DB에 저장. 열린 매매일지는 5분마다 재조회. **Supabase 미연결이므로 이 개인 자동화의 실제 운영 성공은 아직 검증하지 못함.**

## 실제 데이터

[GitHub Actions 실제 API 실행](https://github.com/0baselabkim-debug/qweasd/actions/runs/38709671079)

|종목|OHLCV|기간|구조 검증|실제 매매 판정|
|---|---:|---|---|---|
|삼성전자 005930|320일|2025-06-23~2026-10-14|통과|판정 보류|
|SK하이닉스 000660|320일|동일|통과|판정 보류|
|NAVER 035420|320일|동일|통과|판정 보류|

총960행, 호출23회, 약24.91초, 호출 제한 오류0건. 대표3종목 확보율100%, 기존2,430종목 대비 기술 자료 확보율0.1235%. 전체 종목 재수집은 하지 않음. 동일 날짜20일 수급은 기존 원본과 일치. 3종목 이동평균·일목·ATR 원본 독립 계산 일치.

**신호 생성 가능0종목.** 공급원 최종 확정 근거가 없고, 기존 보존 종가와 신규 KIS 관측 종가가 달라 원인을 임의로 해소 처리하지 않았다. 160일을 확보했다는 사실만으로 이 검증을 통과시키지 않았다. 기존 원본은 보존. 신규 자료에 기업행사 검증이 없으면 보유수량·평균단가 관련 매도 판단도 보류.

## 규칙 중 이번에 수치화한 개발 기본값

- 피벗 좌우2일 확인, 120일 탐색, 0.5ATR 군집, 최소5일 간격 반응2회, 구간폭±0.25ATR.
- 눌림: 전일 저가가 MA20/기준선±0.5ATR, 반등: 당일 종가>전일 고가, MA20 상승: 5일 전 대비.
- 돌파: 전일 확인 저항 상단+0.1ATR 초과, 당일 거래대금은 당일 제외20일 평균의1.5배 이상.
- 진입=종가, 손절=1차 지지 하단−0.5ATR, 목표=1차 저항 하단. 정상 가격 순서일 때만 손익비 계산, 최소2.
- 매도 우선순위: 핵심 지지 이탈→추세·수급 훼손→추적 손절→저항 상승 실패. 최초 위험의2배 유리한 가격 이후 추적 활성화, 최고 종가−3ATR, 저장 기준은 낮추지 않음.
- 실제 거래비용이 아직 설정되지 않음. 청산 조건이 있어도 손절/익절 순손익 분류는 보류. 순손익0도 보류. 사용자 승인값이나 수익 보장으로 표현하지 않음.

## 검증

- 기존110개 + 기술·매매일지24개 = **134개 자동 테스트**. 기존 데이터·수급·재무·테마 및 주요 수집 워크플로 해시 일치: [실제 검증 자료](actual-data.json).
- 실제 데이터 기반 로컬 브라우저1920/1440/768/390px: 매크로, 섹터·종목 더블클릭, 탭·정렬·CSV, 금색 가이드 위치, 미로그인 매매일지 저장 차단 확인.
- 별도 **가상 저장소** 브라우저: 추가·부분 검색·저장실패 후 팝업 유지·재시도·취소·수정·삭제·복수 매수·로그아웃·다른 테스트 사용자 표시 분리·모바일 팝업 확인. [테스트 기록](journal-synthetic-browser.json). 이는 실제 Supabase 인증/RLS/영구 저장 검증을 대신하지 않음.
- SQL43문장·PL/pgSQL8함수 파싱 통과. 실제 DB 실행·사용자 간 권한 침범 차단·새로고침 및 재배포 후 개인 기록 유지 검증은 **미완료**.
- 아직 지나지 않은 거래일의 자동 운영 성공을 주장하지 않음.

## 화면

- [실제 NAVER 상세·매수 가이드 보류](screenshots/actual-detail.png)
- [실제 모바일 상세](screenshots/actual-detail-mobile.png)
- [실제 매매일지 연결 대기](screenshots/journal-unconfigured.png)
- [가상 테스트 계정의 매매현황 UI](screenshots/holdings-synthetic.png)
- [가상 테스트 계정의 추가 팝업 UI](screenshots/popup-synthetic.png)
- [가상 테스트 모바일 팝업](screenshots/popup-mobile-synthetic.png)

## 사용자 설정 / 남은 작업

1. [무료 Supabase 설정 안내](../../docs/private-journal-setup.md)에 따라 새 프로젝트 생성, 준비한 SQL 실행, 본인 사용자 생성, Streamlit에 URL·anon키 등록, GitHub에 URL·service_role키 등록. 코드 수정 없이 복사·붙여넣기로 설정.
2. 키와 비밀번호를 채팅에 보내지 말고 설정 완료 여부만 전달. 실제 저장·로그인·재시작 유지·사용자 분리 검증을 이후 수행해야 함.
3. KIS 최종 확정 및 기존 종가 충돌 해소 근거가 추가되기 전까지 가이드는 보류. 거래비용도 실제 적용값 확인 후 설정해야 함.

## 주요 변경 파일

`app.py`, `screener/exploration_ui.py`, `screener/technical.py`, `technical_rules.json`, `screener/technical_cache.py`, `screener/journal_store.py`, `screener/journal_ui.py`, `scripts/collect_technical.py`, `.github/workflows/technical-refresh.yml`, `docs/sql/private_journal.sql`, `docs/private-journal-setup.md`, `docs/trading-guidelines.md`, `tests/test_technical.py`, `tests/test_journal.py`, 테스트 전용 브라우저 하네스와 검증 스크립트. 기존 수급/재무 DB와 기존 수집 워크플로는 변경하지 않음.
