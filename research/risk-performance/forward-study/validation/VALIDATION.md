# 최종 검증 기록

검증한 코드 커밋: `a7f1a59bb5b077c14b4a71d010e9163712355e22`.

- 로컬 전체 회귀: 352 passed / 270.42초 (`pytest-final.log`).
- 연구 테스트: 44 passed / 2.54초. 전체 회귀에 포함된 부분집합이다.
- [정확한 코드 커밋 CI](https://github.com/0baselabkim-debug/qweasd/actions/runs/39520064709): success. 테스트와 HTTP 서버 검사 단계 모두 성공. 기존 test-only 워크플로를 수동 실행했으며 수집/예약 성공을 의미하지 않는다.
- 실제 게시본: 고유 2,429종목 전부 검증 보류, 새 신호 0건. 원시 관측 74개 보존. 37종목 공식 KRX 대조 자료 미확보.
- 실제 시스템 시각으로 지난 신호의 등록을 시도하여 `prospective_freeze_outside_available_window` 거절과 파일 미생성을 확인했다 (`expired-signal-test.json`).
- 테스트 임시 자료에서만 개장 전 정상 등록 경로를 검증했다. 실제 신규 신호 성공이나 실제 수익률 검증을 주장하지 않는다.
- 공개 연구 파일 5개 HTTP 200 및 SHA 일치 (`public-check.json`, 해당 코드 커밋 기준).
- 운영 파일/기존 연구 원본 변경 없음, 추가 시장 API 호출 및 신규 예약 없음 (`protection.json`, `actual-results-check.json`).

이 검증 기록을 추가하는 후속 커밋은 보고서·검증 증거만 변경한다. 실행 코드의 검증 대상은 위 커밋이다.
