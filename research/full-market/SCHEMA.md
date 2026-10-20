# 연구 결과 필드 및 해석

- `asof`: 고정 평가 거래일 2026-10-19. `observed_at`: 실제 자료 수신 시각(UTC); 서로 다른 의미다.
- `symbol`: 6자리 문자열. CSV를 읽을 때 문자열로 지정해 선행 0을 보존한다.
- `market`, `sector`: 동결 DB의 기준일에 유효한 공식 업종 구성. 과거 전체 기간의 업종 구성은 아니다.
- `price_rows`, `continuous_sessions`: 저장 행 수와 기준일에 끝나는 유효 OHLCV 연속 거래일 수.
  실제 무거래 행은 연속 행에 포함될 수 있지만 40일 창에 무거래가 있으면 구조 평가를 보류한다.
- `price_data_status`: 중복 없는 대표 상태. 복수 문제는 `reasons`에 모두 남는다.
  충돌과 짧은 이력이 함께 있으면 대표 상태는 충돌이며 상세 열에 이력 부족도 남는다.
- `trusted_candidate_data`: 확보한 원본·요청 범위·연속성·가격 대조·필수 수급 검사를 통과했는지.
  거래소의 최종 확정 인증이나 기업행사 원장의 완전성을 보장하지 않는다.
- `provider_finality`: 모두 UNVERIFIED. 내부 정합성 통과로 이를 VERIFIED로 바꾸지 않는다.
- `net_1d_krw`, `net_5d_krw`, `previous_5d_krw`, `net_20d_krw`: 원화 순매수 금액.
  실제 0원은 0, 필수 날짜 결측은 빈칸이다. 수량이나 총매수액이 아니다.
- `change_5d_krw`: 최근5일 − 직전의 겹치지 않는5일. 전일 대비가 아니다.
- `intensity_5d_pct`: 기준일 검증 시가총액 대비 최근5일 순매수 ×100. 퍼센트 단위다.
- `H1/L1/H2/L2/H3/L3_*`: 종가 스윙의 가격·발생일·2거래일 뒤 확인일.
- `early/middle/confirmed_confirmation_date`: 현재 유효한 L2 구조와 연결된 원래 단계 사건 확인일.
- `current_structure_only`: 현재 구조는 관측되지만 과거 사건일에 필요한 40일 가격이 없는 경우.
  사건을 새로 만들지 않고 확인일을 비운다. 극값 자체의 확인일은 별도 보존한다.
- `new_events`: 기준일에 새로 발생한 사건. `price_structure`: 기준일 현재 유지 상태.
- `rise_pct`, `within10/15/20`: 모두 같은 L2에 대한 기준일 종가의 상승률과 누적 상한.
  events 파일의 같은 필드는 해당 사건 확인일 종가 기준이다. 이 둘을 혼동하지 않는다.
- `h3_above_l2_10`: H3가 L2보다 10% 초과 높은지.
- `breakout_within10_impossible`: H3가 L2의 110% 이상이라 그 고정 H3를 엄격히 초과하는
  돌파를 10% 이내에서 만들 수 없는지. 새로운 L2를 선택해 조건을 맞추지 않는다.
- `final_group`: 고유 최종 분류. `잠정`은 가격 검증이 끝나지 않은 조건 충족 관측이며,
  `검증 보류`/`판정 불가`를 조건 미충족과 같은 의미로 해석하지 않는다.
- `source_identifiers`: 별도 보존 원본의 경로·내용 해시. 새 값으로 기존 원본을 덮어쓰지 않는다.

`stage-events.csv`의 `flow_improving_asof`와 `asof_data_trusted`는 10/19 기준이다.
과거 사건일에 알려진 수급이나 당시 자료 품질로 읽으면 안 된다.
`point_in_time_observation_available=false`는 사후 수신 가격으로 재구성한 사건이다.
`event_date_quality_verified`는 별도 검증 범위를 표시하며, 이번 연구는 과거 성과를 계산하지 않는다.

연구용 DuckDB의 `prices`는 선택한 관측 버전이며, 경쟁 관측은 입력·collection 파일과
`observation-conflicts.csv`에 별도로 보존된다. 운영 DB의 테이블을 수정하지 않는다.
