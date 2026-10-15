# 개인 매매일지 연결 (무료 Supabase)

개인 기록은 공개 GitHub·DuckDB·Actions 산출물에 저장하지 않습니다. 로그인한 사용자 JWT와 Supabase PostgreSQL RLS로 격리합니다. Streamlit 서버 로컬 파일과 브라우저 임시 저장소는 영구 저장소로 사용하지 않습니다.

## 사용자 최소 설정

1. https://supabase.com/dashboard → **New project** → 무료 조직 → 프로젝트 이름과 DB 비밀번호 입력 → 가까운 리전 선택 → **Create new project**. 유료 플랜을 선택하지 않습니다.
2. **SQL Editor → New query** → 저장소의 [private_journal.sql](sql/private_journal.sql) 전체를 붙여넣고 **Run**. 새 프로젝트에 한 번만 실행합니다. 코드를 수정할 필요는 없습니다.
3. **Authentication → Users → Add user → Create new user** → 본인 이메일·비밀번호 입력 → 이메일 확인 옵션 활성화 → 생성. 여러 사용자는 각자 별도 계정을 생성합니다. **Authentication → Sign In / Providers → Allow new users to sign up**은 끕니다(메뉴 이름은 Supabase 버전에 따라 조금 다를 수 있음).
4. **Project Settings → API / API Keys**에서 Project URL 및 `anon`(또는 publishable) 키를 확인합니다. 공개 앱에는 **service_role / secret 키를 넣지 않습니다.**
5. 기존 Streamlit 앱 → 오른쪽 아래 **Manage app → Settings → Secrets**에 기존 항목은 유지하고 아래 두 항목을 추가 → **Save**. 키 값은 채팅이나 GitHub 파일로 보내지 않습니다.
   ```toml
   SUPABASE_URL = "https://프로젝트ID.supabase.co"
   SUPABASE_ANON_KEY = "anon 또는 publishable 키"
   ```
6. 장마감 비공개 시세 갱신: GitHub 저장소 → **Settings → Secrets and variables → Actions → New repository secret**에 `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` 등록. 두 번째 값은 Supabase의 서버 전용 `service_role` 키이며 Streamlit에는 넣지 않습니다. 기존 KIS 키는 유지합니다.
7. 웹앱 → **매매일지 → 이메일·비밀번호 → 로그인 → + 추가**. 저장 후 새로고침·다시 로그인해 기록이 유지되는지 확인합니다. 브라우저 새로고침 시 인증 세션이 끝날 수 있지만 DB 기록은 남습니다.
8. **Actions → Separate technical OHLCV cache → Run workflow → private**로 첫 수집을 실행할 수 있습니다. 이후 기존 일일 수집 성공 후와 평일 23:45 KST에 미완료 대상만 시도합니다. GitHub 예약 지연은 있을 수 있습니다.

설정 완료 상태만 알려주시면 실제 저장·로그아웃·재로그인·재배포 유지와 사용자 분리 검증을 이어갑니다. 사용자 비밀번호나 키를 채팅에 보내지 마세요. 자동 검증용 계정이 필요하면 개인 기록이 없는 두 테스트 계정을 별도로 만들고 보안 설정으로 공급해야 합니다.

## 검증 전 상태

현재 프로젝트·인증 키가 없으면 로그인 및 저장을 비활성화합니다. DB가 실제 연결되기 전에는 영구 저장 검증 완료로 보고하지 않습니다. 무료 요금제는 제공자의 용량·비활성 프로젝트 일시중지 정책이 적용됩니다. 일시중지되면 오류를 표시하고 로컬/공개 저장으로 대체하지 않습니다.

## 보안·운영

- 사용자 ID는 입력값이 아닌 Auth `/user` 검증 결과를 사용합니다. 실제 권한은 DB RLS가 강제합니다.
- 사용자 JWT는 해당 Streamlit 세션 메모리에만 보관하며 전역 캐시·URL·로그에 기록하지 않습니다. 인증 실패 시 세션을 제거합니다.
- 사용자끼리 매수 기록·변경 이력·추적 손절 상태를 읽거나 수정할 수 없습니다. 기록 변경은 revision을 비교하여 동시 수정 충돌을 알립니다.
- Actions 작업은 서버 전용 키로 **종목 코드만** 받아 시세를 갱신합니다. 매수일·수량·가격·사용자 ID를 로그·산출물·공개 Git에 남기지 않습니다. 개인 수집 결과는 Supabase에만 저장합니다.
- `technical_cache`의 불량 응답은 이전 정상 데이터를 덮어쓰지 않고 오류 상태를 남깁니다. 최종 확정 기준이 미확인인 자료는 매매 신호에 사용하지 않습니다.
- 추적 손절 값은 DB 함수에서 `greatest`로만 증가합니다. 마지막 매수 기록을 삭제하면 해당 종목 추적 상태도 제거합니다.
- 공개 저장소의 `technical/pilot.json.gz`는 고정된 대표 3종목 시장 데이터뿐이며 개인 매매 기록과 연결되지 않습니다.
