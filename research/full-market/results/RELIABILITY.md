# 데이터 신뢰성 보고서

고유 상태 집계와 중복 가능한 사유를 구분한다.

| 고유 데이터 상태 | 종목 수 |
| --- | --- |
| 가격·수급 모두 정상 | 2310 |
| 수정주가 검증 보류 | 53 |
| 신규상장·거래정지 등 구조적 예외 | 41 |
| 가격 관측값 충돌 | 25 |

| 상세 사유(중복 가능) | 종목 수 |
| --- | --- |
| zero_volume_or_turnover_40 | 99 |
| corporate_action_adjustment_unverified | 55 |
| official_reference_conflict | 24 |
| unresolved_observation_conflict | 24 |
| history_short_40 | 8 |
| listing_short_40 | 8 |
| flow_missing_required_10 | 4 |
| adjustment_precision_rule_unverified | 1 |

## 관측 충돌

6필드 대조 관측 차이 행 2247개, 종목 2209개.
서로 다른 관측 시각·요청 옵션·해시 파일을 그대로 보존한다.
분석창 안의 차이가 남으면 최신 값으로 덮어쓰거나 정상으로 바꾸지 않는다.
종가 변경 여부와 고가·저가·거래량·거래대금 변경 여부는 `fields`에 분리되어 있다.
종가가 같아도 OHLCV 전체 정합성 미해결이므로 보수적으로 잠정 집단에 둔다.
단일 필드 차이를 보고 NXT·기업행사·가격 정정 중 하나로 원인을 확정하지 않는다.

## 기존 8종목

| symbol | name | trade_date | old_observed_at | new_observed_at | request_market | different_fields | difference_type | new_adjusted_equals_raw | krx_same_date_status | root_cause | krx_close | winner | old_open | new_adjusted_open | new_raw_open | old_high | new_adjusted_high | new_raw_high | old_low | new_adjusted_low | new_raw_low | old_close | new_adjusted_close | new_raw_close | old_volume | new_adjusted_volume | new_raw_volume | old_turnover | new_adjusted_turnover | new_raw_turnover |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 000640 | 동아쏘시오홀딩스 | 2026-10-19 | 2026-10-19T09:56:50.528307+00:00 | 2026-10-20T06:33:10.548868+00:00 | J/KRX | high,close,volume,turnover | PRICE_AND_ACTIVITY | True | MISSING_SAVED_REFERENCE | UNRESOLVED | — | — | 93,900 | 93,900 | 93,900 | 99,100 | 93,900 | 93,900 | 91,800 | 91,800 | 91,800 | 93,600 | 93,900 | 93,900 | 12,414 | 12,424 | 12,424 | 1,151,721,100 | 1,152,657,500 | 1,152,657,500 |
| 139480 | 이마트 | 2026-10-19 | 2026-10-19T10:18:44.243919+00:00 | 2026-10-20T06:33:12.513358+00:00 | J/KRX | close,volume,turnover | PRICE_AND_ACTIVITY | True | MISSING_SAVED_REFERENCE | UNRESOLVED | — | — | 73,100 | 73,100 | 73,100 | 76,000 | 76,000 | 76,000 | 72,400 | 72,400 | 72,400 | 75,000 | 75,800 | 75,800 | 142,521 | 142,713 | 142,713 | 10,633,561,700 | 10,647,984,800 | 10,647,984,800 |
| 083420 | 그린케미칼 | 2026-10-19 | 2026-10-19T10:15:53.054336+00:00 | 2026-10-20T06:33:14.607700+00:00 | J/KRX | close,volume,turnover | PRICE_AND_ACTIVITY | True | MISSING_SAVED_REFERENCE | UNRESOLVED | — | — | 4,525 | 4,525 | 4,525 | 4,810 | 4,810 | 4,810 | 4,525 | 4,525 | 4,525 | 4,750 | 4,785 | 4,785 | 33,505 | 33,603 | 33,603 | 157,311,992 | 157,777,252 | 157,777,252 |
| 368770 | 파이버프로 | 2026-10-19 | 2026-10-19T11:14:52.001996+00:00 | 2026-10-20T06:33:21.093390+00:00 | J/KRX | high,close | PRICE_ONLY | True | MISSING_SAVED_REFERENCE | UNRESOLVED | — | — | 10,570 | 10,570 | 10,570 | 11,470 | 11,400 | 11,400 | 10,530 | 10,530 | 10,530 | 11,410 | 11,330 | 11,330 | 110,453 | 110,453 | 110,453 | 1,223,251,270 | 1,223,251,270 | 1,223,251,270 |
| 059120 | 아진엑스텍 | 2026-10-19 | 2026-10-19T10:39:06.582176+00:00 | 2026-10-20T06:33:23.246330+00:00 | J/KRX | high,close,volume,turnover | PRICE_AND_ACTIVITY | True | MISSING_SAVED_REFERENCE | UNRESOLVED | — | — | 6,050 | 6,050 | 6,050 | 6,560 | 6,510 | 6,510 | 5,960 | 5,960 | 5,960 | 6,520 | 6,490 | 6,490 | 165,600 | 167,191 | 167,191 | 1,050,902,985 | 1,061,295,735 | 1,061,295,735 |
| 042940 | 상지건설 | 2026-10-19 | 2026-10-19T10:33:45.245017+00:00 | 2026-10-20T06:33:25.105796+00:00 | J/KRX | high,close,volume,turnover | PRICE_AND_ACTIVITY | True | MISSING_SAVED_REFERENCE | UNRESOLVED | — | — | 4,955 | 4,955 | 4,955 | 5,160 | 5,090 | 5,090 | 4,905 | 4,905 | 4,905 | 5,160 | 5,090 | 5,090 | 13,746 | 14,917 | 14,917 | 69,142,910 | 75,239,850 | 75,239,850 |
| 064520 | 테크엘 | 2026-10-19 | 2026-10-19T10:40:19.060797+00:00 | 2026-10-20T06:33:27.258162+00:00 | J/KRX | high,close,volume,turnover | PRICE_AND_ACTIVITY | True | MISSING_SAVED_REFERENCE | UNRESOLVED | — | — | 1,449 | 1,449 | 1,449 | 1,754 | 1,449 | 1,449 | 1,387 | 1,387 | 1,387 | 1,405 | 1,406 | 1,406 | 10,871 | 11,021 | 11,021 | 16,466,234 | 16,676,966 | 16,676,966 |
| 225570 | 넥슨게임즈 | 2026-10-19 | 2026-10-19T11:02:39.034374+00:00 | 2026-10-20T06:33:29.436663+00:00 | J/KRX | high,close | PRICE_ONLY | True | MISSING_SAVED_REFERENCE | UNRESOLVED | — | — | 9,550 | 9,550 | 9,550 | 9,570 | 9,550 | 9,550 | 9,320 | 9,320 | 9,320 | 9,510 | 9,490 | 9,490 | 124,330 | 124,330 | 124,330 | 1,171,911,830 | 1,171,911,830 | 1,171,911,830 |

기존 시범 원문은 재사용했으며 같은 10종목을 다시 조회하지 않았다.
이 8종목의 10/19 재조회 수정주가와 원주가는 서로 같았으며 이번 공식 KRX 종가와도 모두 같았다. 따라서 그날 기존 관측과의
차이를 수정 옵션 하나로 설명할 근거는 없다. [공식 대조 CSV](../evidence/previous-eight-official-comparison.csv)에서 6필드 전체를 확인할 수 있다. 상지건설의 과거 조정 차이는 별도 문제다.
공식 명세의 `J=KRX`, `0=수정주가`, `1=원주가`는 `docs/official_itemchart.py`에 보존했다.
보존 KRX 원시세는 10/14·10/15 대조에 활용했으나 10/19 대조를 대신하지 않는다.
이번 공식 대조 시도 기록: `[{"market": "KOSPI", "http": 200, "status": "received_unreconciled"}, {"market": "KOSDAQ", "http": 200, "status": "received_unreconciled"}]`.
접근이 거부되면 우회·연속 재시도하지 않았다. HTTP 거부의 발생 주체를 단정하지 않는다.

## 상지건설

상지건설의 6/1~9/21 수정·원주가 차이, 9/22 조정 표시는 보존했지만
이번에는 코스닥시장본부의 공식 권리락 원문에서 **유상증자, 실시일 2026-09-22, 기준가 5,680원**을 확인했다.
원래 미확인 기록은 `inputs/sangji-status.json`에 보존하고 새 확인 내용은 `evidence/sangji-review.json`에 분리했다.
KIS 원주가 전일 종가 5,900원 대비 계수 5,680/5,900으로 78일 × OHLC 4필드 **312개**가
정수 절사 모델과 모두 일치한다. 다만 이 절사 모델의 공식 공급자 명세와 전일 KRX 원시가는
별도로 확보하지 못했고, 거래량은 3일에 1주 차이가 남는다(모두 40일 분석창 이전).
기업행사 원인 자체는 확인됐지만 정밀도 검증을 생략해 신호 사용을 강제하지 않았다.
추가 종목도 조정 표시/수정·원가격 차이를 동일 원칙으로 분류했다.
추가 공식 공시 목록/원문 확인 상태: `received_for_offline_review`.
선택한 원문: `[{"corp_code": "00232007", "corp_name": "상지건설", "stock_code": "042940", "corp_cls": "K", "report_nm": "권리락              (유상증자)", "rcept_no": "20260921900481", "flr_nm": "코스닥시장본부", "rcept_dt": "20260921", "rm": "코"}, {"corp_code": "00232007", "corp_name": "상지건설", "stock_code": "042940", "corp_cls": "K", "report_nm": "[기재정정]주요사항보고서(유상증자결정)", "rcept_no": "20260921000184", "flr_nm": "상지건설", "rcept_dt": "20260921", "rm": ""}]`.
원본·식별자·요청 기간은 `collection/sangji-official-probe.json`에 보존한다.

## 거래일·결측·기업 상태

- 공식 KIS 지수 거래일 검증 상태: passed; 보존 연구 달력과 일치: True.
- 상장 전 행·신규상장 짧은 이력·실제 무거래·빈 날짜를 구분했다.
- 결측을 0으로 채우지 않고 각 창의 날짜가 모두 존재할 때만 합산·분류했다.
- 전체 이벤트의 가격 접두 구간만 사용했지만, 사후 수신 자료의 당시 관측 가능성은
  별개다. `point_in_time_observation_available=false`는 실시간 재현으로 사용할 수 없다.
- 기업행사 원장 완전성·공급자 최종 확정은 보장하지 않는다.

## 자료 위치

전체: `all-stocks.csv`; 사유: `coverage.csv`; 충돌: `observation-conflicts.csv`;
조정: `adjustment-differences.csv`; 잠정: `provisional-only.csv`.
