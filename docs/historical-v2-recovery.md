# 과거 원본 복구와 독립 V2 재분석

실행 결과와 실제 수치는 [A–H 결과 보고서](../reports/historical-v2-recovery/RESULT.md), 원본 목록은 [데이터 자산 목록](../reports/historical-v2-recovery/data-assets.json)과 [정규화 메타데이터](../reports/historical-v2-recovery/catalog/manifest.json)에 보존한다. 운영 데이터 또는 과거 판단을 수정하는 경로가 아니다.

## 경계와 입력 계약

- 운영 main의 snapshot/DB/JSON, collector_v1, kis_shared, 운영 설정과 정기 workflow는 수정하지 않는다. 후처리 출력은 저장소 및 입력 경로와 겹치면 거절한다.
- `historical-collector`: ZIP 외부 digest, Run/Artifact, 원본 manifest, report와 universe 경로를 지정한다. 실제 공식 master 원문을 다시 파싱하고 적격 목록과 정확히 비교한다. 실험 원본이 다른 master 아티팩트를 참조하면 그 master ZIP의 digest와 원문 해시도 요구한다.
- `historical-checkpoint`: 보존된 Git blob과 bundle SHA256, 거래일, 수집 완료 상태와 모집단을 검증한다. 과거 형식에 없는 HTTP 메타데이터를 생성하지 않는다.
- `historical-publication`: 고정 commit의 manifest와 파일 해시를 검증한다. 일별 적격 master를 증명할 수 없으면 `UNKNOWN_HISTORICAL`이다. 이는 연구용 관측 자료이며 전체시장 완료 세대가 아니고 후보 선정에도 사용하지 않는다.
- 가격 pool과 거래일 자료는 각각 외부 manifest/hash로 고정한다. 입력 뒤에 추가된 다른 세대가 과거 분석을 조용히 변경하지 않도록 분석 요청 ID에 입력 proof/컷오프/코드 해시를 포함한다.
- 현재 collector 입력 검사는 원본 PASS/INTERNAL_PASS, 종료된 HTTP 원장, 물리적 호출 예산, DB/JSON, 누락·중복 및 지수 2개를 확인한다. 실패 원본은 모든 행이 존재하더라도 완료로 승격하지 않는다.
- 원본의 내부 검증, KRX 6필드 검증, 공급자 최종 확정은 서로 다르다. 외국인·기관 수급의 공급자 최종성은 가격 KRX 통과로 승인되지 않는다.

## 가격 및 시점

`price_pool.py`는 KIS 원주가와 KRX의 OHLC·거래량·거래대금이 모두 정확히 같은 whole bar만 저장한다. 허용오차 확대, 일부분만 교체, 임의 조정계수 적용을 하지 않는다. 조정 관련 표지가 있는 시계열은 연속성 장벽으로 관리한다. 장기 이력은 원주가·수정주가 원문 쌍과 충분한 동일 whole-bar 중첩이 있는 경우에만 연결한다.

KRX 두 시장에서 실제 행이 관측된 날짜 집합을 별도 보존 KIS 지수 날짜 집합과 대조한다. 이것은 **공식 일별 응답에서 확인한 거래일**이며 공식 휴장일 공고 자체를 수집한 것은 아니다. 이 증거가 없는 초기 날짜는 주중 달력으로 만들어 채우지 않는다.

후대에 보존된 가격 빈티지로 과거를 재계산한 결과는 전부 `RETROSPECTIVE_REANALYSIS`, `historical_backtest_eligible=false`다. 공급자 관측 시각·당시 공개 가능 시각·수집 시각·이번 분석 시각을 혼동하지 않는다. 기업/뉴스/매크로 자료의 당시 공개 증거가 부족하면 해당 입력을 보류한다. 현재 생성한 후보는 당시 실제 판단이나 사용자 거래가 아니다.

## 재처리와 복원

`scripts/reprocess_historical_v2.py JOBS.json --workers 2`는 독립 출력 공간에 evidence와 investment 세대를 생성한다. 같은 요청의 성공 결과는 다시 검증하여 재사용한다. 입력 또는 코드가 달라지거나 과거 요청이 실패했다면 새로운 출력 세대를 요구한다. 이는 원천 HTTP 재사용을 신규 관측으로 표시하는 기능이 아니다.

고정된 143개 결과를 생성한 실제 실행:

- Run `41215929380`, Artifact `13049456226`
- ZIP SHA256 `d3c082ddadcb0913e3698fcd64ee770865df8ed85f32d85f786325752a84c206`
- `results.json` SHA256 `902a2a4b05c3fd0ec1036ed77d4213ea65eeed95dc9ee72e17981a2904832c96`
- 각 job별 `.tar.gz`와 아카이브 hash, 두 분석 manifest hash가 results index에 들어 있다.

`restore_historical_v2.py`는 index 외부 SHA → job tar SHA → 두 manifest → 각 파일 → JSON/DuckDB/Parquet 전체 행·값을 검증한다. 기존 경로, Git 체크아웃 내부, traversal/symlink/중복 경로 및 비정상 확장을 거절한다. 실제로 143개를 모두 새 임시 디렉터리에 복원했고, Oct26 복원본을 별도로 보존했다.

예시 명령은 개발자 재현용이며 이번 작업에서 실제 복원 검증을 이미 수행했다.

```sh
python scripts/restore_historical_v2.py \
  --index /independent/results.json \
  --index-sha256 902a2a4b05c3fd0ec1036ed77d4213ea65eeed95dc9ee72e17981a2904832c96 \
  --job collector-12658373693 --archives /independent/native-bundle \
  --destination /independent/new-oct26-generation
```

`scripts/build_historical_outcomes.py`는 검증된 pool·달력 SHA를 입력받아 5/10/20거래일 **가격 경로**를 새 JSON/DuckDB/Parquet에 저장한다. 실제 매매, 전략 성과, 배당·세금·수수료, 장중 방어선 체결을 추정하지 않는다. API를 호출하지 않는다.

## 신규 일일 입력과 GPT

새 정규 수집의 완료 handoff에서 asof/generation_id/run_id/code_commit/Artifact ID·digest·내부 검증 상태를 전달하면 같은 collector proof 계약으로 적재할 수 있다. FAIL이나 ARTIFACT_UNCONFIRMED를 성공 handoff로 받아들이지 않는다. 원본 master/calendar/ledger를 다시 검증한다. KRX 미확보 시 허용된 수급 분석은 제한 정보를 표시하고, 가격 확정이 필요한 지표·가설은 보류한다. 이전일 데이터를 오늘로 바꾸지 않는다.

원천 검증 → 정량 evidence → 가설/전략/브리핑 → 판단 기록 인터페이스를 재사용한다. GPT는 별도의 명시적 실행과 비용 검토 대상으로 남겨둔다. 정량 재처리나 무호출 검증 workflow가 OpenAI를 자동 실행하지 않는다. 실제 사용자 매매 입력이 없으면 거래 기록을 생성하지 않는다.

이 신규 분석 경로는 **운영 자동 후처리로 배포하지 않았다**. 정기 수집·운영 브리핑·대시보드 연결은 별도 검토 후 적용해야 한다.

## 보존과 재실행 제한

원본 재보존 Run `41222293255`, Artifact `13050263098`은 원본 ZIP 274개를 content-addressed objects로 저장한다. 총 ZIP `95524a1db242e117db4e2950cd1bdf7a8a0ab200221774312f321dcda7956242`와 각 내부 ZIP 해시를 실제 다운로드 후 확인했다. `source-index.json`으로 이전 Run/Artifact/digest/만료일을 추적한다. 원본 Oct26 ZIP도 다시 추출하여 이전 영수증과 대조했다.

이 저장소는 현재 공개 상태다. 같은 기존 접근 범위로 보존했으며 비공개·클라이언트 암호화 외부 백업이 아니다. R2 계정/버킷/자격은 생성하지 않았다. 원본 재보존 만료일은 **2027-01-29T16:59:24Z**, 분석 아티팩트는 **2027-01-29T15:39:58Z**다. 영구 보존이 필요하면 만료 전에 별도 승인된 외부 저장소에 hash index와 함께 이전하고 실제 복원을 검증해야 한다. Git에는 약 8.5MB의 압축 메타데이터 목록과 보고서만 추가하고 원문 ZIP/시장 DB를 누적하지 않는다.

유한 복구 workflow는 기능 브랜치의 지정 제어 파일 push만 받으며 만료 시간, scope, 최초 attempt, 원장 예산을 확인한다. 정기 cron은 없다. KIS 이번 복구 허용분 3회는 소진됐고 wrapper가 HTTP 전에 거절한다. 같은 제어 파일 재무장, 다른 root나 재시도로 예산을 초기화하지 않는다. KRX 실패 시 첫 오류에서 중단했다. 과거 실패 자료와 호출 원장은 그대로 유지한다.
