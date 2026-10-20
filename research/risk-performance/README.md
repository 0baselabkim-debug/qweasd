# 위험 대비 성과 연구 — 운영과 분리

**판정: 데이터 부족으로 검증 불가.** 실제 분석은 `BACKTEST_FEASIBILITY.md`, `BACKTEST_REPORT.md`를 참조한다. 빈 성과 값은 0% 수익률이 아니다.

이 디렉터리만 쓰며 운영 DB·JSON·수집·화면·예약을 변경하지 않는다. 연구 기준 커밋과 파일 SHA256은 `inputs/source.json`, 보존 원본 출처는 `inputs/preserved-checkpoints.json`, 아카이브 해시는 `archive-hashes.json`에 있다. 이전 전수조사 코드는 그대로 가져와 실행한다.

## 재현

저장소 루트, Python 3.12 / requirements.txt와 pytest 설치 환경:

```sh
python research/risk-performance/run.py
python research/risk-performance/verify.py
python research/risk-performance/write_reports.py
python -m pytest -q
```

`run.py`는 네트워크 접속을 코드에서 차단한다. 필요한 입력은 이 브랜치에 보존된 파일뿐이다. 실제 호출 수는 0이다. 새로운 성과 기간이 충분해지면 현재 감사 스크립트는 중단한다. 검증되지 않은 후속 가격을 자동 체결자료로 승격시키지 않도록 한 경계다. `engine.py`는 검증된 후속 자료를 입력받는 계산 모듈이고, 현재 원본 충돌을 해소하는 자동 어댑터가 구현됐다는 뜻이 아니다.

## 수동 전향 관찰 (새 예약 없음)

```sh
python research/risk-performance/forward.py freeze /path/to/validated-bundle.json /path/to/calendar.json
python research/risk-performance/forward.py observe /path/to/new-observation.json
```

첫 명령은 실제 실행 시각이 증거 확보 이후이면서 다음 공식 거래일 09:00 KST 전인지 검사한다. 이미 지나간 신호를 뒤늦게 고정하지 않는다. 둘째 명령은 관측 원문만 불변 저장하며 검증 완료로 취급하지 않는다. 실제 전향 신호를 이번 작업에서 등록한 것은 아니다.

`signals.csv`의 `record_kind`를 반드시 구분한다. `retrospective_reference`는 과거 매매 입력으로 사용할 수 없다. `historical_partial_universe`도 전체 시장 또는 실시간 기록 신호가 아니라 당시 보존 자료를 지금 재현한 부분 표본이다. `trades.csv`의 `entry_open_candidate`는 미검증 관측값이며 체결값이 아니다.
