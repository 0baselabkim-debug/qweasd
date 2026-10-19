# 일일 수급 브리핑 JSON

공개 주소: https://raw.githubusercontent.com/0baselabkim-debug/qweasd/main/snapshot/daily_briefing.json

평일 18:30 수집 / 21:00 재시도 일정은 유지한다. 정상 후보 스냅샷을 만든 뒤 `scripts.stage_daily_publication`이 브리핑을 생성·독립 검증한다. DB·manifest·validation·ledger·JSON은 한 Git 커밋으로 게시한다. 생성 실패 또는 push 충돌 시 기존 공개 게시본을 유지한다. 동일 날짜가 게시된 경우 정규 수집은 기존 ledger에 따라 건너뛴다. 가격 복구로 같은 날짜 DB 해시가 바뀌는 경우에도 `publish_liquidity.py`가 JSON을 함께 검증·갱신한다.

`daily-briefing.yml`은 기존 검증 스냅샷만으로 초기 게시/복구하는 수동 워크플로다. 시장 API를 호출하지 않는다. 동일한 입력과 결과면 기존 파일의 생성 시각과 바이트를 유지한다. 이 워크플로의 수동 성공은 향후 schedule 성공을 뜻하지 않는다.

## 계산 및 검증

- 공식 업종 중 5거래일 순매수가 양수이고 완전한 섹터의 상위 3개. 동률은 시장·업종명 순서. 종목은 5일 순매수 내림차순, 동률 종목코드 순서.
- 현재 기준일 업종 구성을 과거 구간에 적용한다. 과거 실제 구성이나 종합 강세 점수가 아니다.
- 대시보드 `universe`, `stock_activity`, `sector_indicators`를 재사용한다.
- 별도 SQL 및 원본 관측 합산으로 전체 후보 순위, 1/5/20일 금액, 겹치지 않는 직전 5일 대비 차이, 확산율·시총 대비 강도를 대조한다.
- 누락은 null이며 실제 0원과 구분한다. TOP 3 미달이나 날짜·해시·값 불일치 시 실패한다.
- 금액 단위 KRW, 확산율 %, intensity는 비율(백분율 표시 시 ×100).
- 운영 검증 passed는 공급자의 최종 확정 보증이 아니다. `provider_finality`는 기존 UNVERIFIED를 유지한다.

## 21:30 소비자 필수 확인

1. `asof`가 브리핑 대상 한국 거래일과 일치해야 한다. 당일 요청이면 Asia/Seoul 오늘과 비교한다.
2. `publication_status == "ready_for_briefing"`, `validation_status == "passed"`, `raw_flow_reconciliation.status == "passed"`를 모두 확인한다.
3. 이전 날짜이면 당일 자료로 해석하지 않고 “당일 검증 자료 미게시 / 지연 또는 휴장 확인 필요”로 표시한다. 휴장 여부를 날짜만으로 추측하지 않는다.
4. 더욱 엄격한 소비자는 최신 manifest의 asof 및 sha256과 `source_snapshot_sha256`까지 비교한다. CDN에서 세대가 다르게 보이면 재시도하고 불일치 자료를 사용하지 않는다.

`collection_started_at`/`collection_completed_at`은 원본 검증 기록, `snapshot_published_at`은 원본 게시 ledger, `generated_at`/`published_at`은 JSON 생성·커밋 준비 UTC 시각이다. 실제 게시 여부는 main 공개 파일 접근으로 확인한다. 생성 시각만으로 당일 수급 여부를 판단하지 않는다.

ChatGPT 예약 작업 자체는 이 저장소에서 변경하거나 실행하지 않는다. 예약 작업은 위 조건으로 JSON을 읽어야 한다. 21:30을 넘긴 수집에 대해 검증을 생략하지 않는다.

## 수동 검증 명령

```bash
python -m pytest -q tests/test_daily_briefing.py
python scripts/export_daily_briefing.py --asof 2026-10-16 --output /tmp/briefing.json
```

고정 날짜 회귀는 시험 브랜치의 실제 JSON과 대조한다. 공개 스냅샷에 테스트 자료를 섞지 않는다. 실패 사유는 Actions 실패 로그와 `briefing-failure.json` artifact에서 확인한다.
