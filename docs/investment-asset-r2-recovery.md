# 10월 26일 원문 재복구 및 R2 실사용 준비 — 2026-10-28 KST

기준 브랜치 `feat/investment-asset-archive-v1`, 커밋 `1e976cbe`. 이번 작업은 보존된 데이터만 읽는다. 시장 API, 운영 배포/main 병합, 기존 일정·수집기·운영 DB·JSON 변경은 하지 않는다. 계정 생성, 버킷 생성, 토큰 발급, 실데이터 암호화, 외부 업로드, 자동 백업 등록도 실행하지 않았다.

## 원문 접근 재확인 결과

Run `40326538555`, artifact `12658373693` / `kis-manual-state-20261026`, 기대 원본 ZIP SHA256:
`eef7625731b266aca24e39865a976925901bf52be746d0961c3a4f00e574c4e2`.

환경 설정 초안 revision 4에는 `api.github.com`, `productionresultssa1.blob.core.windows.net` 등 이전 허용 도메인이 남아 있다. 초안 조회는 실제 실행망 적용/Publish 확인이 아니다. 현재 공식 `gh api` Run/metadata/ZIP 요청은 전송 단계에서 Forbidden으로 종료했다. 일반 공개 API 루트 요청도 **`Tunnel connection failed: 403 Forbidden`**이었다. GitHub 웹·공식 문서의 공개 raw 소스는 접근된다.

| 구분 | 이번에 확인한 사실 |
|---|---|
| 네트워크 | 프록시 CONNECT 단계 403. 실제 GitHub HTTP 응답·request ID가 없음. 현재 API 도메인 접근이 정상이라고 확인할 수 없음 |
| 인증/권한 | GitHub 401/403 응답을 받은 것이 아님. 현재 인증 유효성/Actions 권한은 이번 실행에서 재검증 불가. 이 오류만으로 토큰 교체를 요구하지 않음 |
| 아티팩트 만료 | 현재 metadata를 받지 못함. 이전 10월 27일 조회는 expired=false, 만료 예정 11월 9일이었으나 이번 실시간 검증 성공으로 표시하지 않음 |
| 서명 URL 만료 | 오래된 서명 URL을 재사용하지 않았음. 공식 안정 API 경로에서 새 다운로드를 요청했지만 URL 발급/리디렉션 이전에 차단됨. 서명 URL 만료 여부는 확인 불가 |
| 원문 저장 호스트 | 이번 다운로드는 해당 호스트에 도달하지 못함. 별도의 호스트 루트 HEAD 연결 검사도 CONNECT 403으로, Azure HTTP 응답을 받지 못함. 루트 검사는 원문 인증/다운로드 검증이 아님 |
| 내려받은 파일 | 0 bytes, 기대 SHA256 불일치로 기존 어댑터가 거부. 원본으로 승인하거나 적재하지 않음 |

**원문 복구는 BLOCKED**, 원문 2,349종목의 실제 복원·완전성 확인은 NOT_RUN이다. 새 세대·원본 객체·원문 종목 관측 추가는 모두 0이다. 기존 receipt-only 세대는 그대로 남는다. 원문 재생성·재수집·인증/네트워크 우회 경로는 사용하지 않는다.

필요 조치: 환경 설정의 허용 목록을 검토·저장/Publish하고 실제 실행 환경에 반영되었는지 확인한다. 이미 적용했다면 환경/프록시 관리자가 두 도메인의 CONNECT 403을 확인해야 한다. 적용 후 공식 CLI로 metadata와 새 ZIP을 재확인한다. 현재 문제를 토큰 부족이나 만료로 단정하지 않는다. 원문 저장 호스트가 새 metadata의 리디렉션에서 달라지면 새로 관측한 공식 호스트만 검토한다. 서명 URL은 로그/Git에 남기지 않는다.

```bash
# 정상 GitHub 응답인지 확인한 뒤 id/run/expired/digest를 고정 영수증과 대조
# 토큰/Authorization 헤더를 출력하는 디버그 옵션은 사용하지 않음
gh api repos/0baselabkim-debug/qweasd/actions/artifacts/12658373693

# 기존 파일을 덮어쓰지 않는 새 경로로 다운로드, 성공 종료 및 SHA 검사 필수
# 실패한 0-byte/부분 파일은 원본으로 승인하지 않음
gh api repos/0baselabkim-debug/qweasd/actions/artifacts/12658373693/zip > /new/path/original.zip

.venv/bin/python -m investment_assets --root /workspace/investment-assets-v1 \
  ingest-kis-artifact --repo /workspace/qweasd --commit 3d838753 \
  --branch feat/market-data-collector-v1 --zip /new/path/original.zip
```

적재 어댑터는 ZIP digest→전체 manifest/원천 해시→고정 영수증과 full 보고서→3,950행 마스터 및 2,349 적격 종목의 코드/시장/날짜/행 수/중복/누락→물리 HTTP 4,701건의 시작/종료 시퀀스·200 응답→원문과 DB/JSON 및 두 지수 일치를 확인한다. PASS 라벨만으로 부분 자료를 승인하지 않도록 완전성 검사를 보강했다. 검증은 오프라인이며 수집 실행기는 호출하지 않는다. 성공해야만 신규 `kis_full_trial` 세대를 적재한다. 가격은 PENDING_KRX, 공급자 최종 상태는 UNVERIFIED를 유지하며 운영 세대와 합치지 않는다.

## 확정한 현재 백업 대상

| 항목 | 실제 값 |
|---|---|
| 소스 아카이브 | `/workspace/investment-assets-v1` |
| 부트스트랩 전체 백업 | `/workspace/investment-assets-backups/stage1-20261027.tar.gz` |
| 백업 크기 | **342,815,089 bytes** (약 342.82MB), 암호화 전 압축 파일 |
| 백업 SHA256 | `4f21e3e727e02354e36b612727746c92ae8323f384dca3ab54c3c2f148ca94be` |
| 원본/세대 파일 목록 | `/workspace/investment-assets-backups/20261028-full-inventory.json` |
| 파일 목록 크기 / SHA256 | **168,036 bytes** / `5e4bff8611e882f439010a038c6b44e325aae52afe255eae0b3af93e54319b28` |
| inventory ID | `4844b721b6f0a826b1c754addc723c84dea54eadfea139b2c212120674b57ad8` |
| 전체 목록 범위 | **895개 파일**, 평문 파일 합계 **601,183,926 bytes**, 고유 콘텐츠 해시 895개 |
| 데이터 범위 | **11세대 / 원본 객체 817개 / 정규화 관측 2,057,272건**. 10/26은 영수증만 포함 |

895개는 원본 객체 817개에 세대 Parquet/manifest/summary/커밋 영수증/FORMAT을 더한 수다. 고유 종목 수가 아니다. 목록에는 `FORMAT.json`, `objects/sha256/`, `generations/`, `commits/`만 포함한다. 파생 `catalog.duckdb`와 lock은 제외하며 복원 후 catalog를 재생성한다.

처음 R2에 보존할 대상은 **암호화된 기존 전체 백업 1개와 암호화된 inventory/복구 체크포인트**다. 암호문은 실제 공개키가 없어 아직 만들지 않았으므로 정확한 업로드 bytes는 확정 불가다. 601.18MB는 파일별 초기 증분 전송을 선택할 경우의 평문 총량으로, 압축 백업 342.82MB에 추가로 모두 전송해야 한다는 뜻이 아니다. 0-byte 실패 ZIP은 백업 원문 목록에 포함하지 않는다.

원문을 나중에 복구하면 새 세대와 새 이름의 백업/목록을 생성한다. 기존 백업을 덮어쓰거나 현재 백업에 10/26 원문이 있다고 표시하지 않는다. 이전 저장료 시나리오(고정 크기 250거래일 약 10.52GB 추가)는 누적 DB 성장·수정본·다회 관측·복제본을 포함한 실측 연간 예측이 아니다.

## 사용자가 준비할 R2 설정

[비밀값 없는 설정 양식](r2-backup-settings.example.json)은 안내용이며 업로더가 아니다. 현재 account ID·실제 버킷·사용 가능한 S3 자격·복구용 공개키/지문은 연결되어 있지 않다. 사용자의 Cloudflare 계정 자체가 없다고 단정하는 것은 아니다.

1. **계정/결제:** 사용자가 Cloudflare 계정에서 R2 이용·결제 설정을 확인한다. 계정 2단계 인증을 설정하고 관리자와 일상 백업 실행자의 역할을 분리한다. 계정 생성·구매·결제 동의를 에이전트가 대신 수행하지 않는다.
2. **비공개 Standard 버킷:** 별도 백업 버킷 이름, account ID, 관할을 정한다. 스토리지는 Standard, public r2.dev URL/공개 custom domain은 사용하지 않는다. 공개 접근 차단을 실제 설정에서 확인한다. 기본 endpoint는 `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`, S3 region은 `auto`다. EU/US/FedRAMP 관할을 선택하면 공식 관할별 endpoint를 사용해야 한다. 실제 계정 endpoint만 환경 네트워크에 허용한다.
3. **자격:** R2 → Manage API Tokens에서 가능한 경우 계정 소유 토큰을 선택하고 **Object Read & Write를 해당 버킷으로만 제한**한다. S3용 Access Key ID/Secret Access Key를 보안 저장소에 넣는다. 일반 Cloudflare Bearer 토큰을 S3 Secret Access Key로 임의 대체하지 않는다. 관리자 토큰은 백업 프로세스에 주지 않는다. 복구용 별도 Object Read only 자격도 해당 버킷에 한정한다.
4. **토큰의 한계:** Object Read & Write를 '삭제 불가 전용 권한'이라고 가정하지 않는다. 요청 코드에서 DELETE를 안 쓰는 것만으로 탈취된 자격의 삭제 위험이 없어지지 않는다. 별도 관리자가 네이티브 버킷 잠금을 설정하고 실제 차단을 검사해야 한다. 세밀한 권한 구성이 실제 계정에서 다르면 그 범위에 맞춰 재검증한다.
5. **암호화 키:** 소유자의 독립 복구 기계에서 `gpg --full-generate-key`로 암호화 가능한 수신자 키와 강한 개인키 암호문을 관리한다. 전체 지문을 별도 경로로 확인하고 공개키만 작업 환경의 Git 외부에 전달한다. 예: `gpg --fingerprint RECIPIENT` / `gpg --armor --export FULL_FINGERPRINT > recovery-public.asc`. 개인키와 암호구문은 이 저장소·채팅·로그·일상 백업 환경에 제공하지 않는다. 테스트 전용 키를 실데이터 수신자로 사용하지 않는다.
6. **보존 잠금:** R2 버킷 Settings → Bucket lock rules에서 실제 데이터 접두사 `investment-assets/v1/`의 보존 기간/무기한 여부를 소유자가 정한다. 관리 권한과 일상 Object 토큰을 분리한다. 이 단계에서 잠금 규칙을 설정하거나 삭제하지 않았다. 잠금 관리자는 규칙을 제거할 수 있으므로 법적/하드웨어 WORM 보장으로 표현하지 않는다. 복구키/기대 해시와 가능하면 추가 사본을 다른 실패 영역에 보관한다.

자격은 비밀 관리자에 `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` 등의 이름으로 연결하고 값은 코드/Git/로그에 남기지 않는다. account ID·버킷·endpoint·전체 공개키 지문은 비밀값과 구분해 기록하되 필요 이상의 공개를 피한다. CLI를 사용할 때는 Git 밖의 안전한 credential profile(`r2-archive`) 등을 사용한다. 디버그/셸 tracing은 끄고 서명 URL도 출력하지 않는다. 현재 자격이나 키를 임의 생성하지 않았다.

### 확인한 R2 지원 범위

공식 문서의 고정 커밋/해시는 [r2-document-evidence.json](../reports/investment-assets/r2-document-evidence.json)에 보존한다.

- **S3 `GetBucketVersioning`/`PutBucketVersioning`는 미지원**이다. AWS 명령으로 버전 관리를 켰다고 가정하지 않는다.
- R2 네이티브 **Bucket locks**는 지정 기간/날짜/무기한의 삭제·덮어쓰기를 막고 기존/새 객체에 적용된다. S3 `PutObjectLockConfiguration`/Object Lock 헤더와는 별개다.
- `PutObject`의 `If-None-Match: *` 조건부 생성이 지원된다. 기존 객체가 있으면 재업로드로 교체하지 않고 기대 암호문 해시/크기와 대조한다.
- R2는 자동 AES-256 저장 암호화/TLS를 제공하지만 별도 수신자 공개키 암호화를 대체하지 않는다. S3의 `--server-side-encryption aws:kms`를 지원한다고 가정하지 않는다.
- Standard 공식 저장료는 `$0.015/GB-month`, A `$4.50/백만 요청`, B `$0.36/백만 요청`, 직접 전송료 무료다. 무료 계정 잔여량·과금 올림·세금·추가 서비스 비용은 별도다. 현재 약 0.343GB 부트스트랩의 비용과 미래 여러 세대/복제본 비용을 혼동하지 않는다.

## 증분 및 중복 방지 설계

새 수동 도구 `python -m investment_assets.r2_plan`은 **로컬 목록과 차이만** 만든다. 네트워크/자격/업로드 기능이 없고 `upload_authorized=false`다. 현재 아카이브를 먼저 검증하고 writer lock 아래 전체 원본/세대 파일의 경로·SHA256·크기를 고정한다. 이전 목록을 사용할 때는 알려진 inventory ID를 함께 요구하고 해시·경로·종속 세대·상태를 검증한다. 과거 파일/세대가 사라지거나 동일 경로의 내용이 바뀌면 삭제/교체 계획으로 바꾸지 않고 중단한다.

```bash
.venv/bin/python -m investment_assets.r2_plan --root /workspace/investment-assets-v1 \
  --destination /outside/git/current-inventory.json

.venv/bin/python -m investment_assets.r2_plan --root /workspace/investment-assets-v1 \
  --destination /outside/git/next-inventory.json \
  --previous-inventory /outside/git/current-inventory.json --previous-id TRUSTED_INVENTORY_ID
```

같은 현재 상태의 재실행은 목록 bytes/ID가 같고 추가 전송 계획은 0개다. 동일 원본 bytes는 세대가 달라도 하나의 평문 SHA로 참조한다. 이것은 **로컬 차이 계산**이며 이전 목록이 있다는 사실만으로 원격에 파일이 있다고 판단하지 않는다.

권장 원격 구조(아직 만들지 않음):

```text
investment-assets/v1/
  bootstrap/<recipient-key-version>/<ciphertext-sha256>.tar.gz.gpg
  payloads/<recipient-key-version>/<plaintext-sha256>/<ciphertext-sha256>.gpg
  checkpoints/<inventory-id>/<ciphertext-sha256>.json.gpg
```

- 최초 체크포인트는 압축 부트스트랩의 암호문 키/해시/크기, 복호화 후 tar.gz의 기대 해시, 전체 inventory ID/파일 해시, 수신자 버전을 연결한다.
- 이후에는 새 고유 평문 SHA만 암호화한다. 암호화는 임의성이 있어 같은 원문을 다시 암호화하면 암호문 SHA가 달라질 수 있다. 검증된 `수신자 키 버전 + 평문 SHA → 기존 암호문/컨테이너` 매핑을 보존해 중복 전송을 막는다. 키 교체 시에는 명시적으로 새 키 버전을 쓰고 옛 사본을 덮어쓰지 않는다.
- 이전 원본이 최초 tar.gz에만 들어 있으면 체크포인트는 그 **컨테이너와 내부 경로**를 참조한다. SHA 키 객체가 별도로 있다고 가정하지 않는다. 모든 부모 체크포인트·부트스트랩·참조 payload 의존성이 필요하다.
- payload를 먼저 생성·다운로드 검증하고 모든 의존성이 확인된 뒤 암호화 체크포인트를 새 키로 게시한다. `latest`를 덮어써서 복구의 유일한 기준으로 삼지 않는다. 실패/중단된 전송은 완료 체크포인트가 아니며 정상 백업으로 승인하지 않는다.
- `sync --delete`, 자동 GC, 이전 세대 삭제는 금지한다. 이름이 해시라는 사실만으로 불변성이 보장되지는 않으므로 조건부 생성과 버킷 잠금을 함께 검사한다. 관리자에 의한 잠금 해제/계정 손실 위험은 별도로 남는다.

**증분 업로더/암호문 매핑/증분 적용 엔진은 설계만 준비했으며 구현·원격 검증하지 않았다.** 첫 실사용은 기존 전체 백업의 수동 roundtrip으로 시작한다. 평문 해시가 객체 이름에 나타나면 동등성 정보가 드러날 수 있으므로 버킷·목록·복구 영수증은 비공개로 유지한다. 기대 체크포인트/암호문 해시를 버킷 밖의 신뢰 경로에도 보관한다. 공개키 암호화의 MDC만으로 업로드 주체의 진위를 인증한다고 표현하지 않는다.

## 자동 실행 전에 수행할 수동 roundtrip

아래는 **준비된 절차이며 이번에 실행한 외부 작업이 아니다**. 실제 계정·대상·권한·암호화 구성 확인 전에는 어떤 객체도 업로드하지 않는다.

1. 실제 account/bucket/관할/endpoint/Standard/비공개 설정·토큰 버킷 범위·관리자 분리·공개키 전체 지문을 확인한다. 관리자의 잠금 설정 확인과 별도 복구 기계의 개인키 보유/복호화 가능성도 확인한다. 자격은 안전한 런타임 바인딩으로만 사용한다.
2. 실데이터가 아닌 소량 canary를 실제 수신자 공개키로 암호화하고 별도 복구 기계에서 복호화해 지문/해시를 검사한다. 인위적인 키를 실사용 검증으로 표시하지 않는다.
3. 계정 설정 확인 후 canary 전용 키를 조건부 생성(`If-None-Match: *`)한다. `HEAD`로 크기를 확인하고 **별도 GET**으로 내려받아 암호문 SHA를 비교한다. ETag나 사용자 metadata의 SHA 문자열만으로 성공 처리하지 않는다. 이미 있는 canary의 교체가 거부되는지와 bytes 불변도 확인한다.
4. 잠금/삭제 방어 검사는 **폐기 가능한 canary만** 사용한다. 정상 백업에 DELETE/overwrite 실험을 하지 않는다. 예상과 달리 교체/삭제가 가능하면 실데이터 전송을 중단하고 역할/잠금 설정을 수정한다. 실제 동작/응답은 기록하되 비밀/서명 URL은 기록하지 않는다.
5. 기존 helper로 검증된 전체 백업을 수신자 지문에 고정해 암호화한다. 실제 암호문 크기/SHA/수신자 버전/평문 SHA를 기록한다. 현재 helper는 개인키 입력을 거부하고 자동 키 서버 조회를 하지 않는다. 소유자가 가진 복구키 검증을 대신하지 않는다.
6. 암호문 전체 백업과 암호화 inventory/체크포인트를 새 이름으로 수동 전송한다. AWS CLI 예시 형태는 `aws --profile r2-archive --endpoint-url https://ACCOUNT_ID.r2.cloudflarestorage.com s3api put-object --bucket BUCKET --key NEW_IMMUTABLE_KEY --body ENCRYPTED_FILE --storage-class STANDARD --if-none-match '*'`다. 이는 placeholder이며 현재 환경에서 실행하지 않았다. 사용 CLI 버전의 조건부 인자 지원도 확인한다.
7. 독립 복구 환경에서 새로 다운로드한 bytes의 암호문 SHA 확인→복호화→평문 tar.gz SHA 확인→빈 경로에 아래 기존 명령으로 복원→전체 파일 inventory/manifest/원천/Parquet/커밋 영수증 및 과거 조회를 비교한다. 원격에 백업이 존재하는 것과 실제 복구 완료는 별개다.
8. 부트스트랩은 복원 경로에서 catalog를 재생성한다. 나중에 증분 적용을 구현하면 먼저 임시 복구 경로에 전체 의존성을 검증·적용하고 새 커밋 영수증을 마지막에 공개한 뒤 아카이브 검사를 수행한다. 기존 운영 경로에 적용하지 않는다. 현재 그 증분 적용 기능은 없다.
9. bucket/key·세대·실제 시각·비밀이 아닌 provider 요청 ID·해시·크기·검증/복원 결과만 원격 수신 영수증으로 보존한다. 성공 전 자동 백업을 등록하지 않는다. 이번 단계에서는 성공 후라도 정기 예약은 등록하지 않는다.

```bash
# R2에서 내려받아 실제 복구키로 복호화한 파일만 대상으로 수행
.venv/bin/python -m investment_assets --root /outside/git/new-restored-archive \
  restore-backup --archive /downloaded-and-decrypted/stage1.tar.gz \
  --sha256 4f21e3e727e02354e36b612727746c92ae8323f384dca3ab54c3c2f148ca94be
```

개인키 분실, 전체 관리자 자격 탈취, 설정 잘못된 보존 잠금, 월별 누적 DB 증가와 참조 의존성 손실은 별도 위험이다. 현재 같은 환경의 로컬 백업/복원본은 외부 재해 복구 사본이 아니다. 데이터 보관/재배포에 대한 공급자 이용 조건도 실제 서비스 적용 시 확인해야 하며 현재 공개 재배포 가능하다고 주장하지 않는다.

## 이번 검증의 실제 범위

실제 로컬 백업을 `/workspace/investment-assets-recovery-20261028-restored`에 복원했고, 전체 inventory 895개 파일의 SHA/크기·세대와 과거 조회가 원본 아카이브와 동일했다. 실제 영수증 재적재는 ALREADY_PRESENT, 실패 ZIP은 해시 검사에서 거부, 원래 아카이브 목록은 바뀌지 않았다. 원문/세대 무결성 PASS는 공급자 최종 확정이나 10/26 원문 복구 성공이 아니다.

신규 오프라인 테스트는 증분 계획 반복/원본 중복 재사용/과거 파일 손실·변경 거부/불완전 세대·손상 차단, 고정 2,349종목 보고서의 누락·중복·시장·날짜·확정 상태·영수증·DB 실패 차단을 검사한다. 실제 원문 ZIP 긍정 경로는 실행할 수 없었으며 가상 원문 긍정 경로와 구분한다. 전체 실행 결과는 [완료 보고서](../reports/investment-assets/recovery-r2-20261028.json)에 기록한다.

최종 테스트: **621통과 / 0실패**(411.07초), 신규 22개 포함. 관련 회귀 51통과/0실패(26.11초)는 전체와 중복된다. 실제 보호 파일 791개 해시 동일. 원문 ZIP 실제 긍정 경로·실수신자 암호화·외부 업로드/원격 복원은 미실행이다.
