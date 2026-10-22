# KRX 40거래일 연구 — 수동 실행

운영 main에 병합하지 않는다. 기존 수동 워크플로 파일 `kis-history-probe.yml`의 **연구 브랜치 정의만** KRX40 경로로 교체했다. KIS 재조회·신호 등록·cron·야간 지연 실행은 없다.

## GitHub 화면

1. 저장소 **Actions → 기존 KIS history probe 워크플로 → Run workflow**.
2. **Use workflow from**에서 반드시 `research/full-market-transition-20261019` 선택.
3. `mode=prepare`(기본값): 시장 API 없이 최신 정상 게시본·공식 달력·해당 일자 원본 체크포인트를 재검증한다.
4. 야간 수집은 **23:00 이상~다음 날 09:00 미만 KST**에, 운영 실행/대기가 없을 때 새 수동 실행에서 `mode=collect-batch` 선택. 낮에 시작한 실행의 야간 재개/재실행은 차단한다.
5. 한 번에 최대 20회 후 종료한다. 다음 배치는 결과 확인 후 다시 수동 실행한다. 성공한 시장·날짜는 재호출하지 않는다. 기본 70개 누락은 최대 20+20+20+10의 네 배치가 필요하나 게시본 변경·오류·예산 상태에 따라 달라진다.
6. `retry_failed`는 기본 false. 원인을 확인한 재시도에만 명시적으로 true. 401/403/429 및 요청 여부 불명확한 중단은 이를 선택해도 자동 재시도하지 않는다.

main 정의를 유지하므로 Actions 메뉴 이름은 기존 KIS 이름으로 보일 수 있다. **main을 선택하면 기존 KIS 작업이므로 실행하지 않는다.** UI 입력이 브랜치 선택 후 갱신되지 않으면 아래 연구 브랜치 지정 CLI를 사용한다(Codex가 수행 가능).

```
gh workflow run kis-history-probe.yml --repo 0baselabkim-debug/qweasd --ref research/full-market-transition-20261019 -f mode=prepare
# 승인 야간에만:
gh workflow run kis-history-probe.yml --repo 0baselabkim-debug/qweasd --ref research/full-market-transition-20261019 -f mode=collect-batch
```

## 실행 및 보존

- `krx40_manual.py` → `krx40_study.prepare_latest()` → 정상 게시본·공식 달력·누락 목록 재계산.
- 최신 게시본과 같은 거래일의 `data/daily-YYYYMMDD:daily-results/bundle.json.gz`를 Git 읽기 전용으로 확보. 게시 run ID·전체 종목·날짜·시장·수정주가 옵션까지 검증. Git 읽기는 시장 API 요청이 아니다.
- `collect-batch` → `krx40_study.execute_batch()` → 동일 plan 재계산 및 현재 main/연구 HEAD/공유 락/운영 대기 확인 → `collect_batch()`.
- 매 물리 호출 직전 원장 예약을 연구 브랜치에 먼저 저장. 응답·해시를 받은 후 즉시 저장. 총 80회 원장은 실행·날짜·배치가 바뀌어도 초기화하지 않는다. 1.05초 간격 유지. 키는 기존 KRX secret만 읽는다.
- 성공/응답 거절 배치 뒤 `prepare`로 공식 40일 이력·가격/기업행사·원본 수급·A/B/C/D를 다시 검증한다. 일부 배치 성공을 전수 완료로 표시하지 않는다.
- 원본과 결과는 `collection/`, `daily-checkpoints/`, `inputs/`, `runs/`에 보존. `manual-executions/<run>-<attempt>/START.json`, `RESULT.json`에 실행별 상태와 실제 누적 요청 수를 기록한다. `LATEST_MANUAL_RESULT.json`과 `CURRENT_RUN.json`은 파생 포인터이며, 이전 불변 결과는 남는다.
- 검증 실패 시 이전 CURRENT_RUN 유지. 오류 결과와 원장은 always 단계로 게시하고 artifact도 남긴다. 원격 게시 실패/강제 종료 시 추가 요청을 하지 않으며 미결 예약은 수동 증거 확인 전 보수적으로 차단한다.
- `registration_ready`와 실제 등록은 별개. 이번 진입점은 신호 저장기를 호출하지 않으며 과거 신호를 소급 등록하지 않는다.

## 테스트

```
PYTHONPATH=.:research/risk-performance python -m pytest -q
```

시간·API 응답을 주입하는 테스트는 임시 디렉터리 안에서만 실행한다. 실제 시장 API 호출은 없다. 테스트 성공은 야간 공급자 접근 성공을 뜻하지 않는다.
