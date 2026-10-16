# 공개 데이터 복구 운영 기준

## 가격 충돌 재관측

수집 완료(`completed_asof`)는 가격 검증 완료를 의미하지 않는다. `collect_technical.py --mode recover --limit 3 --budget 240`은 기존 일별 검증과 분리된 공개 데이터 전용 경로다. private worker나 Supabase를 호출하지 않는다.

- 기존 KIS 1.05초 호출 간격과 `kis-validation-auth` Actions concurrency를 유지한다.
- 첫 검증은 최대 3종목. 실제 2종목 복구 확인 후 기존 예약 검증 뒤에 회당 최대 2종목의 자동 재확인을 연결했고, 실제 3→5→8건 시험(최대 49 HTTP 시도, 재시도 0건)을 거쳐 회당 4건으로 조정했다. CLI 상한은 10종목, 인증·재시도를 포함한 실제 KIS HTTP 전송 상한은 60회다. 실제 전송 직전에 한 슬롯씩 차감하며 미사용 재시도를 미리 차감하지 않는다.
- 240초 이후 새 요청을 시작하지 않는다. 이미 시작된 요청의 타임아웃/백오프는 끝날 수 있으므로 복구 실행 자체는 Actions `timeout 600`으로 제한한다. 예약 검증(최대 1500초)과 복구를 순차 실행하고 증거를 게시할 여유를 포함해 전체 job 상한은 40분이다. KRX 기준 자료 확인은 최대 2개 날짜 × 2개 시장이며, 충돌 복구에서는 해당 날짜의 공식 응답도 새로 관측하고 기존 유효한 기준 캐시는 덮어쓰지 않는다. 일반 검증에서는 같은 날짜 캐시를 재사용한다. 기존 공식 명세는 보존하며 복구 때 반복 조회하지 않는다.
- 불일치·공급자 오류가 남는 날짜·종목은 6시간, 12시간, 최대 24시간 후 다시 시도한다. 요청 예산 소진은 공급자 오류와 구분해 15분 후부터 다음 실행에서 이어받는다. 날짜를 순환하고 예산 중단 재개 작업 및 오래된 시도를 우선 처리한다.
- 정확한 날짜의 KIS J 수정주가(0)와 원주가(1)를 새로 요청한다. 최근 겹치는 구간 또는 수정주가 관측이 달라졌으면 KIS J의 동일 기준 전체 가격 이력을 다시 구성한다. KRX 종가를 KIS OHLCV 안에 끼워 넣지 않는다.
- `technical/observations/<날짜>/<종목>/<내용SHA256>.json.gz`는 기존 및 신규 번들 증거를 보존한다. `provider-observations`에는 이번에 받은 원시 응답 필드를 저장한다. 실패 후보는 `candidates`, 재시도 상태는 `recovery-state.json`, 실행 결과는 `recovery-report.json`에 저장한다.
- 후보는 날짜·종목·OHLCV 6필드·원주가·거래일·20일 실제 수급·해시 검증을 모두 통과해야 기존 날짜 포인터에 반영한다. 불일치와 오류가 계속되면 원본 포인터는 유지된다. 기존 acceptance와 충돌 이력은 별도로 보존한다.
- 활성 가격 충돌은 현재 증거로 재계산하되, 다른 종류의 보류 사유를 지우지 않는다. 공급자 `finality=UNVERIFIED`는 유지한다. 일별 운용 검증 통과는 공급자 최종 확정이나 매수 조건 충족을 뜻하지 않는다.

## 기업 공시 공정 재시도

공시 목록 탐색에는 호출/시간 예산의 최대 1/3만 배정하고 나머지를 저장된 대기 공시 처리에 남긴다. 완전히 읽은 시장·페이지 구간만 탐색 완료일을 전진시킨다. 목록 오류는 대기 공시 처리를 중단시키지 않는다.

최신 연간/중간 공시 선택 원칙은 유지한다. 미시도 공시를 먼저, 다음으로 가장 오래 시도한 공시를 처리한다. 사업 문서와 재무 응답은 각각 시도 시각·횟수·오류 분류·다음 재시각을 기록한다. 성공한 구성요소는 다시 호출하지 않는다.

- 전송 오류, BadZipFile, 일시 미제공: 1시간부터 지수 백오프.
- 접수번호/기간 불일치, 본문 유일성 실패, XML 미확보: 근거 검토 필요. 24시간부터 백오프하며, 재관측해도 검증을 완화하지 않는다.
- 최대 간격 168시간. 새 공시 접수번호는 별도 작업이므로 이전 실패가 새 공시를 막지 않는다.
- 기존 재무 버전·검증된 테마 이력은 삭제하지 않는다. 후보/미확보/근거 불일치를 성공으로 바꾸지 않는다.

## 시장 전체 투자자 수급의 남은 근거

FHPTJ04040000 공식 예제는 `frgn_ntby_tr_pbmn`, `prsn_ntby_tr_pbmn`, `orgn_ntby_tr_pbmn`의 정체를 설명하지만, 그 예제만으로 금액 배수와 KRX/NXT 범위는 확정할 수 없다. 종목별 FHPTJ04160001의 백만원 단위를 전용하지 않는다. `inspect_investor_spec.py`로 해당 API의 공식 개요 및 필드 명세를 별도로 보존한다. 명시적 단위·범위 근거가 없으면 `unit_scope_pending`을 유지한다.

## Physical budgets and resumable sessions (2026-10-16)

Production recovery uses a ContextVar guard at KIS's actual HTTP boundary. Authentication and every retry consume one of at most 60 slots; unused retry attempts consume none. The 1.05-second pacing, shared workflow concurrency and 240-second deadline for new requests remain unchanged. KRX reference requests remain separately recorded and limited to two dates / two markets (at most four requests) per run. A transport adapter without physical-boundary support retains conservative reservations and is explicitly labelled as such; it is not reported as measured physical traffic.

Public response bodies are content-addressed immediately after each successful response, and a session index survives interruption. Resuming still requests a fresh adjusted overlap and same-day raw anchor. Cached pages are reused only if both anchors match the interrupted vintage and the original bundle hash is unchanged. Full candidate validation, exact-date KRX comparison and 20-day flow checks remain mandatory. A changed anchor causes a new coherent history request.

The queue rotates dates and selects eligible old attempts within each date, with a separate priority for budget-paused work. Provider-error backoff still applies. Successful date/symbol observations are excluded even if old pending copies remain. A current accepted bundle can seed at most four older **unvalidated** date-specific tasks for masked liquidity dates. The seed strips current acceptance and computed values, trims future bars/calendar/events, and loads that date's exact 20 flow rows; it is never itself accepted.

## Connection to turnover metrics and publication

The original price rows and original conflict list are preserved. A separate `liquidity_price_evidence` record in DuckDB can release only the same date/symbol's identical original turnover and volume, after the entire price bundle passes existing OHLCV, raw price, calendar, 20-flow and hash gates. It does **not** correct the DB's old close, market cap or valuation, clear general price conflicts, or grant acceptance to other dates. Those remaining price differences stay separately auditable. All 25 observations are still required for activity; all sector constituents remain required.

Publication prepares a candidate DB plus matching manifest, validation and ledger. Old generation files are archived by DB hash. The public reader rejects mixed generations and selects a coherent archived generation until all new files agree. The hidden browser identity now includes both UI source and actual published DB hashes. No visible date/status header is added. Git publication commits the generation together; nonoverlapping concurrent changes are merged, while overlapping changes preserve the candidate on an evidence branch instead of forcing/rebasing a binary snapshot.

## DART attachment corrections

`[첨부정정]` is not assumed to contain another full business report. The exact original periodic receipt is linked only when the official disclosure listing and financial API agree on issuer, receipt, year, report code and period. Original and attachment records remain separate. Original financial records are reused unchanged; attachment audit content remains separately held for review. XML status 014 is classified as file unavailable, not a corrupt ZIP. A business section is selected only from a unique matching issuer/receipt/form document, not the largest/first ZIP entry.

## 공급자 장애와 실제 데이터 결과 (2026-10-16)

KRX 401/403은 `REFERENCE_AUTH_ERROR`로 기록하고 동일 실행의 나머지 시장·날짜 요청도 중지한다. 다음 요청은 1시간, 반복 시 2/4/8/16/24시간 뒤부터 허용한다. 전송 오류·408·429·5xx는 `REFERENCE_TEMPORARY_FAILURE`이며 15분부터 최대 24시간 백오프한다. 새 정상 응답이면 해제한다. 동일 날짜의 정상 캐시를 사용하는 일반 검증은 유지하지만, 충돌 복구의 `refresh=True`는 백오프/장애 중 캐시로 대신하지 않는다. 요청 시도 여부·HTTP·응답 해시·수신 시각·재시각을 기록하며 403의 정확한 원인은 상태 코드만으로 단정하지 않는다.

`recovery-report.json`은 검증 후보 결과이며 게시 완료를 뜻하지 않는다. 실제 Git 게시 성공 후 `evidence/recovery-outcome-<run_id>` 브랜치의 `reports/recovery-execution/outcome.json`과 Actions Summary에 `RECOVERED`, `NO_ELIGIBLE_WORK`, `PROVIDER_BLOCKED`, `BUDGET_PAUSED`, `VALIDATION_FAILED`, `PUBLICATION_FAILED`를 구분한다. 혼합 결과는 건수도 함께 표시한다. 공급자 상태 건수는 시장/날짜 수이며 종목 수와 다르다. 준비 실패 시 정상 main에 후보를 게시하지 않고 증거 브랜치에 보존한다.

`diagnose_activity.py`는 기존 25일 행렬의 마스킹을 그대로 적용해 업종별 구성 수·검증 수·차단 날짜·사유·최소 추가 날짜/종목 확인 수를 산출한다. 일부 종목 합계를 완전한 업종 수치로 만들지 않는다. `review_corrections.py`와 `publish_document_reviews.py`는 공식 정정·감사 원문 비교 근거만 추가하며 기존 재무 접수번호 검증과 실패/검토 상태를 변경하지 않는다.

실제 추가 2건/15회, 4건/29회 시험에서 재시도·호출 제한 오류가 없음을 확인한 뒤 예약 복구를 최대 6건으로 단계 확대했다. 기존 8건/49회 시험도 참고했다. KIS 60회·240초 상한은 유지한다. 과거 날짜 작업 생성은 회당 최대 4건(선택 한도가 더 작으면 그 이하)이며 검증 통과를 의미하지 않는다. 실제 6건 예약 성공과 장기 처리량은 이후 실제 schedule 기록으로 판단한다.
