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

## v3 전체 시장 데이터셋 (v2는 그대로 유지)

인덱스: https://raw.githubusercontent.com/0baselabkim-debug/qweasd/main/snapshot/briefing/index.json

- `daily_briefing.json`의 schema_version=2 및 상위 3업종·각 5종목 의미는 바꾸지 않는다.
- 새 `briefing/index.json` 및 `briefing/sectors/{sector_id}.json`은 schema_version=3이다. 지표별 수치를 제공하며 종합 점수나 투자 순위를 부여하지 않는다. 인덱스는 시장·업종명, 종목 파일은 종목코드 순이다.
- sector_id는 시장 소문자와 SHA256(시장 + NUL + 공식 업종명)의 앞 24자리 조합이다. ASCII 경로이며 시장이 다른 동명 업종도 별개다. 중복 ID/대상 범위 오류는 독립 검증에서 차단한다.
- 모든 대시보드 포함 종목을 담는다. 음수와 일부 기간 결측 종목을 제거하지 않는다. 미분류는 별도 그룹에 `official_sector=false`로 남기며 공식 업종 선택에서 제외한다.
- `members`는 대시보드에 포함된 업종 구성 종목 수, `registered_members`는 instruments에 등록되고 기준일 해당 업종으로 매핑된 수다. 전체 거래소 상장 종목 수라고 해석하지 않는다.
- `coverage.population_total`은 DB instruments와 당일 공식 exclusions의 종목 합집합이다. included/excluded/not_observed는 서로 배타적이며 합계가 population_total이다. `omitted`에 코드·상태·원본 사유를 보존한다. 제외 목록에만 있고 instruments에 없는 종목의 시장·업종은 추측하지 않고 null로 남긴다.
- 결측 종목은 included에 남고 각 `metric_validity` 및 `quality`에 표시된다. actual 0은 유효 수치다. 섹터 금액은 날짜별 모든 구성 종목의 관측이 있어야 합산하며 부분 합계를 완전한 합계로 표시하지 않는다.
- 20일 지표는 net_20d, positive_days_20d, observed_days_20d, 업종 evaluable_20d/positive_20d/breadth_20d를 제공한다. 원본 관측과 대조할 수 있는 기존 계산만 노출한다.

### GPT Dot 조회 절차

1. 인덱스를 GET한다. `schema_version=3`, `publication_status=ready_for_briefing`, `validation_status=passed`, `raw_flow_reconciliation.status=passed`를 확인한다. 한국 기준 브리핑 대상 거래일과 `asof`가 다르면 당일 자료로 사용하지 않는다.
2. 인덱스의 `source_snapshot_sha256`, `dataset_id`, 기준일을 고정한다. 가능하면 최신 `snapshot/manifest.json`의 asof/sha256과도 비교한다. 불일치하면 캐시 갱신 후 재조회하고 계속 다르면 브리핑을 보류한다.
3. 인덱스 `sectors`의 모든 공식 업종 지표를 살펴보고 분석 관점에 맞는 3개를 선택한다. 순매도 축소와 순매수 전환을 구분하고, 결측을 0이나 최하위로 단정하지 않는다. `official_sector=false` 그룹은 공식 업종 선정에서 제외한다.
4. 선택한 항목의 `path`를 인덱스 URL의 디렉터리 기준으로 연결한다. 예: `.../snapshot/briefing/` + `sectors/kospi-<hash>.json`.
5. 응답의 **원시 UTF-8 바이트** SHA256이 인덱스 항목의 sha256과 일치하고, byte 길이도 bytes와 같아야 한다. 파일의 dataset_id/asof/source_snapshot_sha256이 고정한 인덱스와 같아야 한다. stock_count, 배열 길이, 인덱스 members도 일치해야 한다.
6. 해당 파일의 전체 stocks를 보고 5개 후보를 선정한다. 미리 상위 5개로 잘린 파일이 아니다. net/change는 원(KRW), intensity는 ratio(백분율 표시는 ×100), breadth는 이미 %이다. 종목별 결측 표시와 20일 관측 일수를 함께 확인한다.
7. 도중에 파일이 없거나 해시·세대가 다르면 같은 인덱스에서 재시도한다. 새 인덱스로 넘어가려면 선택한 모든 파일을 다시 읽어야 한다. 서로 다른 세대 파일을 섞지 않는다. 계속 실패하면 미완료로 보고한다.

`index_content_sha256`은 자기 필드를 뺀 인덱스를 `json.dumps(..., ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'`로 직렬화한 UTF-8 바이트의 SHA256이다. 자기 참조를 피한 **내용 해시**이며 원시 index.json 파일 전체 해시와는 다르다. 하위 파일의 sha256은 파일 전체 바이트 해시다. 생성 코드의 `scripts/briefing_schema.py`가 이 계약을 정의한다.

같은 source SHA와 schema의 dataset_id는 동일하다. 같은 자료 재실행은 최초 generated_at/published_at과 파일 바이트를 보존한다. 시장 자료나 계산 계약이 바뀌면 새 스냅샷 또는 새 스키마로 게시해야 한다. 현재 업종 구성을 과거에 적용한 수급이라는 한계와 공급자 finality UNVERIFIED는 그대로다.

### 운영·복원

정규 후보 및 가격 복구 후보에서 기존 프레임을 한 번 계산해 v2/v3에 재사용한다. v3는 원본 SQL을 한 번 읽고 모든 종목·업종 지표를 독립 대조한다. 정상 후보만 한 Git 커밋으로 DB·manifest·검증·v2·v3를 게시한다. 공유 concurrency와 수집 API 호출/시간은 변경하지 않는다. 실패 사유는 Actions 로그와 briefing-failure.json을 확인한다. 수동 초기 게시 워크플로는 별도 수집 없이 두 JSON 체계를 검증하고 공개 파일을 확인한다.

원자적 Git 게시 이후에도 Raw CDN은 파일별 캐시가 달라질 수 있으므로 해시·세대 검사는 필수다. 이전 세대는 Git 이력에서 조회할 수 있다. 예전 시각으로 브리핑하려면 해당 커밋의 DB·manifest·v2·v3 전체를 함께 사용한다.

v3 소비를 중단하고 이전 방식으로 돌아갈 때는 GPT Dot의 읽기 주소를 기존 v2 주소로 돌리면 된다. v2와 UI는 계속 유지된다. 코드 차원의 되돌리기가 필요하면 v3 구현 커밋만 revert하고 이후 데이터/공시 커밋을 보존한다. DB를 오래된 파일로 덮어쓰지 않는다. 남은 v3 파일은 더 이상 갱신되지 않는다면 사용 중단해야 하며 asof/hash 검사로 이를 감지한다.
