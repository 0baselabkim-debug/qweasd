# 아카이브 안정화 — 외부 보존, 원문 복구, 후속 전달 준비

기준: `feat/investment-asset-archive-v1` / `6ddaaadb`. 기존 수집기·Actions·일정·운영 DB·JSON은 수정하지 않는다. 새 코드는 `investment_assets.stabilization`의 **수동 오프라인 도구**이며 업로더·네트워크 클라이언트·자동 실행기가 아니다. 실제 측정과 상태는 [stabilization.json](../reports/investment-assets/stabilization.json)에 기록한다.

## 확인한 보존 상태

- 원본 `/workspace/investment-assets-v1`, 이전 복원본 `/workspace/investment-assets-restored-v1` 모두 무결성 PASS: 11세대, 고유 원본 817개, 정규화 관측 2,057,272건. 날짜·가격 기준·세대가 중복되므로 고유 종목 수가 아니다.
- 백업 `/workspace/investment-assets-backups/stage1-20261027.tar.gz`: 342,815,089 bytes, SHA256 `4f21e3e727e02354e36b612727746c92ae8323f384dca3ab54c3c2f148ca94be`.
- 새 경로 `/workspace/investment-assets-stabilization-restore`에 실제 백업을 복원하고 전체 원본·manifest·Parquet·커밋 영수증을 검사했다. 10월 14일 과거 게시본 조회가 원래 아카이브와 동일했다. 외부 저장소에서 내려받은 복원 시험은 아니다.
- 세 경로 모두 동일 환경에 있다. 환경 소실에 견디는 외부 백업은 아직 없다. 기존 소스 보호 대상 801개 해시도 동일하다.
- 운영 게시본·분석 sidecar·10월 26일 시험 영수증의 채널/브랜치/세대/모집단을 유지한다. 10월 26일은 **원문 0종목, 영수증만 보존** 상태다. 과거 관측 시각 UNKNOWN, `historical_backtest_eligible=false`, 가격·수급 UNVERIFIED를 승격하지 않는다.

## 외부 저장소 후보와 추천

| 후보 | 기존 인증 재사용 가능성 | 비용·보존 | 판단 |
|---|---|---|---|
| 비공개 Cloudflare R2 Standard | 현재 버킷·계정·유효 자격 바인딩 확인 불가. 새 설정 필요 | 공식 단가 아래 참조. 직접 인터넷 전송료 무료. 장기 보존·잠금 정책은 실제 계정에서 확인 필요 | 현재 확인된 구성에서는 우선 추천. 계정·비공개 버킷·권한·수신자 키를 정한 후 별도 업로드 |
| 비공개 AWS S3 | AWS 관련 환경변수와 설정 파일은 있으나 프로필 섹션·자격 내용이 비어 있어 사용 가능하다고 볼 수 없음 | 리전·스토리지 등급·요청·복원/전송료별 과금. 현재 계정/리전/단가는 확인 불가 | 이미 계약된 계정/IAM이 확인되면 우선 재검토. Object Lock/버전 관리 등 보호 설정의 비용·권한도 확인 |
| GitHub Release 첨부 파일 | GitHub Git 접근은 가능. Release API 쓰기 권한은 확인되지 않음; Actions metadata API 읽기는 확인됨 | Git 객체 이력에 대용량을 넣지 않음. 공식 문서는 전체 release 용량/대역폭 제한 없음으로 설명하나 파일·첨부 개수 제한 및 서비스 정책 적용 | 기존 서비스 재사용 후보. 공개 저장소의 첨부 파일은 공개될 수 있어 평문 업로드 금지. 주 백업보다 암호화된 보조 사본에 적합 |
| Backblaze B2 / GCS 등 비공개 객체 저장소 | 기존 대상/인증 확인 불가. GCP 자격 파일은 빈 JSON 객체 | 현재 공식 요금·리전·계약은 확인 불가 | 가격을 임의 기재하지 않음. 기존 계약 확인 시 비교 가능 |
| GitHub Actions 아티팩트 | 이미 사용 중이나 원문 다운로드 호스트 접근 제한 | 수집 워크플로는 원문을 14일, 운영 일별 원문은 7일 보존하도록 설정 | 수집→아카이브 전달용. 장기 백업 대체 불가 |

추천은 **조건부**다. 확인되지 않은 서비스 가입·버킷 생성·결제·권한 변경·업로드는 하지 않았다. 저장소 분리 없이 코드 저장소를 재사용하되 실제 데이터는 비공개 객체 저장소에 둔다.

### 공식 가격 근거와 용량 시나리오

공식 Cloudflare 문서 원본을 고정 커밋으로 읽고 해시를 보존했다. [가격 및 문서 근거](../reports/investment-assets/stabilization-pricing.json). 표준 저장 `$0.015/GB-month`, Class A `$4.50/백만 회`, Class B `$0.36/백만 회`, 직접 다운로드 전송료 무료. 무료 제공량은 계정의 월 10GB-month·A 100만·B 1,000만 회이며 **현재 계정에서 남아 있는지는 확인 불가**다. 과금 단위 올림과 월간 일별 최대 저장량 평균 방식이 적용된다. 세금·환율·별도 서비스 사용료는 포함하지 않는다.

현재 아카이브 601,720,502 bytes. 최신 운영 게시 세대의 원본 30,219,685 bytes와 Parquet 11,872,781 bytes를 합친 **42,092,466 bytes/거래일**을 고정한 250거래일 시나리오:

| 항목 | 산정 |
|---|---|
| 연간 신규 원본+Parquet | 10,523,116,500 bytes ≈ 10.52GB |
| 현재 보존량 포함 연말 아카이브 | 11,124,837,002 bytes ≈ 11.12GB |
| 위 연말 용량을 한 달 내내 저장 | 무료 잔여량 미적용 `$0.18/월`; 무료 10GB-month가 전부 남았다면 `$0.03/월` |
| 같은 연말 용량을 12개월 보유하는 저장료 | 각각 `$2.16` / `$0.36`. 실제 첫해 청구액 예측이 아님 |
| 매일 늘어난 전체 아카이브를 250개 모두 별도 보관 | 누적 약 1.47TB **압축 전**. 위 11.12GB 시나리오와 다른 저장 전략 |

실제 누적 DB 성장률, 수정주가 재게시, 분석 데이터, 여러 회차, 메타데이터, 암호화, 복제본은 실측 성장률이 없다. 원본 중복 제거는 증가량을 줄일 수 있고 누적 DB·추가 세대는 늘릴 수 있다. 현재 백업 압축률을 미래 전체 데이터의 확정 비율로 적용하지 않는다. 위 값은 최소 필요량/확정 예산이 아니다.

현재 백업 명령은 전체 묶음이다. 장기 자동 누적 시 content-addressed 원본 객체+세대 단위 증분 전달과 정기 복원용 체크포인트를 구현하고, 기준 세대와 의존 객체 목록을 고정하는 방식을 권장한다. **증분 외부 업로더는 이번에 구현하지 않았다.** 기존 데이터 자동 삭제·수명주기 삭제 정책도 등록하지 않는다.

### 암호화·권한·복구

1. 사전 설정: 실제 비공개 대상, 리전/계정/결제, 라이선스상 외부 보관 가능 여부, 전체 공개키 지문을 독립 경로로 확인한다. 공개키만 작업 환경에 전달하고 개인 복구키는 다른 안전한 위치에 둔다. 공급자/KIS 원문 이용·재배포 조건은 현재 확인 불가이므로 외부 공개 금지다.
2. 저장소: public ACL/공개 URL 차단, TLS 사용, 서버측 암호화 설정 확인. 업로드 전용 자격은 전용 버킷/접두사에 최소 권한으로 한정하고 삭제·수명주기·관리 권한과 분리한다. 해당 서비스의 실제 최소권한 범위는 계정에서 확인해야 한다. SHA 기반 이름만으로 덮어쓰기 방지가 보장되지는 않으므로 조건부 생성/보존 잠금을 검증한다.
3. 로컬 암호화: 수동 `encrypt`는 별도 임시 GnuPG keyring에 **공개키만** 가져와 고정 지문 수신자에게 AES256/MDC OpenPGP로 암호화한다. 키 서버 조회를 끄고 키 유효성 실패 시 중단한다. 원본 백업 SHA와 암호문 SHA·키 파일 SHA를 별도 영수증에 기록한다. 기존 암호문/영수증 충돌 시 교체하지 않는다.
4. 업로드 승인은 별도다. 이번 도구에는 업로드 기능이 없다. 실제 복구키가 설정되지 않았으므로 실데이터 암호화·외부 업로드·원격 복원은 모두 NOT_RUN. 테스트 전용 키와 명시적 가상 데이터의 암호화→복호화→아카이브 복원만 통과했다.
5. 이후 승인된 업로드 후에는 독립 환경에서 암호문 다운로드 SHA 검사→복구키로 복호화→평문 백업 SHA 검사→빈 경로에 `restore-backup`→전체 manifest/원본/Parquet 검사→과거 조회 비교를 실시한다. 다운로드에 성공한 것만으로 복구 성공으로 판정하지 않는다. 복구 지문/기대 SHA는 저장소와 별도 안전한 경로에도 보관한다.

```bash
# 검사만 수행. 외부 구성 미설정이면 BLOCKED_EXTERNAL_SETUP과 비정상 종료 코드.
.venv/bin/python -m investment_assets.stabilization preflight \
  --root /workspace/investment-assets-v1 \
  --backup /workspace/investment-assets-backups/stage1-20261027.tar.gz \
  --sha256 4f21e3e727e02354e36b612727746c92ae8323f384dca3ab54c3c2f148ca94be

# 실제 수신자 공개키/전체 지문 확인 후 수동 수행. 이 명령은 이번에 실데이터로 실행하지 않음.
.venv/bin/python -m investment_assets.stabilization encrypt \
  --root /workspace/investment-assets-v1 --backup /path/to/verified-backup.tar.gz \
  --sha256 EXPECTED_SHA256 --public-key /path/to/recovery-public.asc \
  --fingerprint FULL_CONFIRMED_RECIPIENT_FINGERPRINT \
  --destination /outside/git/archive-backup.gpg
```

공개키 암호화는 키 분실·잘못된 수신자·관리자 삭제·유출을 모두 해결하지 않는다. 평문 원본/백업은 여전히 로컬에 있고, 암호문 영수증의 크기·지문 등 메타데이터도 공개하지 않는 것을 권장한다. 현재 파일 권한 보호는 하드웨어 WORM이 아니다.

## 10월 26일 원문 접근 조사

대상: Run `40326538555`, 아티팩트 `12658373693` / `kis-manual-state-20261026`, ZIP SHA256 `eef7625731b266aca24e39865a976925901bf52be746d0961c3a4f00e574c4e2`.

- 공식 공개 Run 페이지는 HTTP 200으로 열리며 해당 artifact ID와 영수증과 동일한 digest가 표시된다. 이것은 **목록 존재 확인**이며 원문 다운로드/만료 여부의 검증이 아니다.
- 초기 공식 `gh api` 요청은 HTTP 헤더/상태 없이 `api.github.com` 전송 단계의 Forbidden으로 실패했다. 당시 제한 설정 기본 목록에 API 호스트가 없었다. 이는 GitHub 인증 거절 응답이 아니었다.
- 이후 공식 CLI로 다시 확인한 결과 **저장소 및 artifact metadata 모두 HTTP 200과 GitHub request ID**를 반환했다. API 접근이 가능해진 정확한 경위는 확인 불가이며, 초안 저장이 실제 환경을 적용했다고 단정하지 않는다.
- metadata는 `expired=false`, 만료 예정 `2026-11-09T14:33:17Z`, 크기 27,223,433 bytes, 정확한 Run/branch/code SHA/digest를 반환했다. 메타데이터 읽기는 가능하고 아티팩트는 현재 만료되지 않았다.
- 그러나 공식 `/zip` 다운로드는 제공자가 안내한 **`productionresultssa1.blob.core.windows.net`**로 이동한 뒤 HTTP 상태/제공자 응답 헤더 없이 전송 단계 Forbidden으로 종료했다. 실제 차단 위치는 원문 저장 호스트의 egress다. 실패 파일은 0 bytes이며 원본 ZIP으로 승인하지 않았다. 서명 URL/쿼리/인증값은 보고서에 저장하지 않는다.
- 따라서 원문 손상·인증 scope 부족·아티팩트 만료로 분류하지 않는다. 다운로드 데이터·manifest·원장 검증은 아직 NOT_RUN이며 **BLOCKED**를 유지한다. 우회 프록시·새 인증 경로·재수집·재생성은 사용하지 않았다.

환경 온보딩 스킬로 기존 설정을 보존한 **추가 허용 도메인 초안**을 저장했다: `api.github.com`, `docs.github.com`, `developers.cloudflare.com`, `aws.amazon.com`, `www.backblaze.com`, 실제 다운로드 호스트 `productionresultssa1.blob.core.windows.net`. 도구는 저장 성공과 `requires_publish=true`를 반환했다. **초안 저장은 적용/게시가 아니다.** 환경 설정에서 변경 검토·저장 후 Publish가 필요하며 이 작업은 수행하지 않았다. 사용자 지시가 스킬의 일반 지침보다 우선하므로 아카이브 개발은 승인된 범위에서 계속했다.

접속 정책 적용 후 정상 경로로 확인할 순서:

```bash
# HTTP 제공자 응답과 Actions 읽기 권한, 만료, run/digest 확인
# 토큰·Authorization 헤더·환경변수는 출력하지 않음.
gh api repos/0baselabkim-debug/qweasd/actions/artifacts/12658373693

# metadata의 id/run/digest/expired=false가 영수증과 일치한 후,
# 새 경로로 원본 ZIP을 그대로 보존. 추출 후 재압축하면 기대 ZIP 해시가 아님.
gh api repos/0baselabkim-debug/qweasd/actions/artifacts/12658373693/zip > /new/path/original-12658373693.zip

# 정확한 SHA·manifest·원천·모집단·원장·DB/JSON 검증 후 독립 세대 적재
.venv/bin/python -m investment_assets --root /workspace/investment-assets-v1 \
  ingest-kis-artifact --repo /workspace/qweasd --commit 3d838753 \
  --branch feat/market-data-collector-v1 --zip /new/path/original-12658373693.zip
```

바이너리 명령은 부분 파일이 남을 수 있으므로 공식 CLI 성공 및 기대 SHA 검증 전에는 적재하지 않는다. 일반 `gh run download`는 보통 추출하므로 원본 ZIP digest 검증용으로 재압축해서는 안 된다. 이후 GitHub 응답이 401이면 인증 갱신, 403이면 제공자 request ID·권한/속도 제한 확인, 404이면 비공개 접근/대상 없음 구분, metadata `expired=true`이면 기존 별도 보존본 탐색이 필요하다. 410만으로 만료 원인을 확정하지 않는다. 이 경우에도 원문이 없으면 복구 성공으로 표시하지 않는다.

## 이후 자동 누적 전달 구조 — 설계 및 오프라인 계약만 준비

고정 시각보다 **실제 게시 완료와 검증된 원본 존재**를 기준으로 한다. 기존 워크플로에 후처리 step을 추가하지 않고, 별도 실행 주체가 읽기 전용 Git/Actions 접근으로 다음 계약을 확인하는 구조를 권장한다. 기존 인증정보를 시장 API용으로 재사용할 필요는 없다.

| 생산 경로 | 코드에서 확인한 순서 | 후처리가 기다릴 조건 |
|---|---|---|
| 운영 `daily-dashboard.yml` | 저장된 원문 seal/독립 보존 확인→검증·게시 트랜잭션→data commit main push 확인→후속 결과 보존 | 게시 트랜잭션의 실제 data commit·manifest/ledger/DB/JSON 일치와 생산 Run 완료 성공. 수집 시작 코드 SHA를 게시 데이터 SHA로 대신하지 않음 |
| 시험 `kis-manual-validation.yml` | 수집·검증→seal→artifact upload→Git 영수증 게시→Job Summary→job 종료 | Run 완료 성공 + artifact metadata/ZIP digest + 고정 영수증 commit. 영수증의 `source_commit`은 수집 코드 SHA이며 영수증 게시 SHA와 별도 |
| 시간대 실험 `market-20261027.yml` | 회차 수집·검증→회차 PASS JSON 게시→seal→artifact upload→해당 job 종료 | **해당 회차 job 완료 성공** + 고유 generation + artifact 확인. PASS JSON만으로 업로드 완료라 볼 수 없음. 다른 회차가 대기/실행 중인 동일 Run 전체 완료까지 기다릴 필요는 없음 |

`handoff`는 전달 후보의 저장소/브랜치/워크플로·거래일·실행/작업 완료·artifact/run/digest·영수증 일치·모집단 해시·금지된 확정 상태를 검사한다. 전달 ID로 같은 후보를 식별한다. 입력 JSON 자체의 진위/커밋 ancestry는 인증된 원천 조회 단계에서 별도 확인해야 한다. 결과 `READY_FOR_OFFLINE_FETCH_AND_VERIFY`는 **적재 성공·원문 무결성·공식 확정 판정이 아니다**.

후속 절차: 인증된 metadata 읽기→원본 임시 다운로드→ZIP SHA→안전 추출→전체 manifest/원문/모집단/HTTP원장→DB/JSON 값 검증→정확한 소스 브랜치/커밋 확인→별도 아카이브 루트에 원자적 적재→수신 영수증 기록. 실패는 후처리 자체 오류 큐에 남기며 생산자에 재수집 요청·실패 전파·운영 파일 쓰기를 하지 않는다. 성공 원문 재다운로드는 필요 시 동일 artifact 재검증으로 제한하며 시장 API를 부르지 않는다.

시간대 요약에는 `db_json_match`가 생략된다. 이 경우 사전 계약은 원문 검증 후보로만 허용하고, 원문 수령 후 DB/JSON 검증을 필수로 요구한다. 명시적 DB/JSON 실패는 후보 단계부터 차단한다. 현재 adapters는 운영 게시본/분석 및 고정 10월 26일 ZIP이다. **새 시간대/일반 일별 원문 adapter·인증된 전달 worker·외부 증분 전송·오류 큐는 후속 구현 사항**이며 자동 적재가 완성됐다고 보고하지 않는다. 전달 후보 단계에서도 전체시장 모드와 목표/수집/검증 종목 수 일치를 요구해 표본·부분 수집을 차단한다. 공식 가격 확정 증거는 별도 계약으로만 취급한다.

```bash
.venv/bin/python -m investment_assets.stabilization handoff \
  --event /path/to/authenticated-producer-metadata.json \
  --receipt /path/to/pinned-published-receipt.json --kind manual_full
```

자동 실행·신규 Actions·예약·운영 배포는 등록하지 않았다. 이후에도 `operating_publication`, `operating_analysis`, `kis_full_trial`를 조용히 합치지 않는다. 공급자 최종 확정과 가격 공식 대조는 별도 증거이며 후처리 완료로 승격하지 않는다.

## 테스트와 다음 단계

신규 오프라인 검증: 네트워크 오류 분류, 백업 손상 거부, 비용 계산/과금 올림, 잘못된 키·개인키 거부, 암호화 파일 덮어쓰기 방지, 테스트 키 복호화/아카이브 복원, 생산자 부분 완료/잘못된 실행/만료/DB 실패 차단, 회차별 작업 완료 처리, 영수증만으로 성공 금지. 실제 로컬 백업 전체 복원과 과거 조회 비교도 수행했다. 테스트 키·가상 응답으로 통과한 경로를 실데이터 외부 복구로 표시하지 않는다.

기본 sandbox에서는 GnuPG agent의 로컬 Unix 소켓 생성이 차단되어 초기 신규 시험 25통과/3설정오류가 발생했다. 네트워크 없는 로컬 테스트에 소켓 사용이 가능한 실행 권한으로 다시 실행해 28개 모두 통과했다. 기능/검증 기준은 완화하지 않았다. 실제 수신자 키를 사용한 복호화 검증은 여전히 별도다.

2단계 신호 탐지의 **별도 오프라인 개발**은 보존된 데이터의 커버리지·시점·UNVERIFIED/백테스트 제한을 유지하는 조건으로 가능하다. 10월 26일 2,349종목에 근거한 신호 검증은 원문 복구 전 불가다. 장기 자동 운영 준비 완료 판정은 외부 보존/독립 복원, 정상 API 접근, 일반화된 원문 adapter/worker, 시점 및 가격/수급 정책 확인 이후에만 가능하다.

최종 검증: 전체 회귀 593통과/0실패(419.27초), 마지막 관련 회귀 63통과/0실패(25.17초). 마지막 관련 시험에는 전체 회귀 이후 보강한 계약 시험 6개가 포함된다. 실행 간 중복을 제외하면 기준 테스트 565개와 신규 안정화 시험 34개, 총 599개를 검증했다.
