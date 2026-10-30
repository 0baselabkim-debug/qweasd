# 2026-10-30 20:30 정기 수집 사전 점검

상태: **예약 등록 PASS / 실제 자동 수집 준비 BLOCKED**. 현 접근 정책을 유지하면 오늘 정기 실행도 KIS 인증 전에 `shared_rollout_not_approved`로 차단된다. 어제의 승인된 수동 복구 성공은 정기 경로의 차단 해제를 뜻하지 않는다.

GitHub 내부 공식 API를 사용한 [검사 Run 41017834421](https://github.com/0baselabkim-debug/qweasd/actions/runs/41017834421)은 검사 작업으로서 SUCCESS다. 수집 성공을 뜻하지 않는다. 검사 시각은 2026-10-30 16:04:55 KST다.

| 검사 | 결과 |
| --- | --- |
| 실제 기본 브랜치 | main, b1ae57918a16f4857af865e66f4f4158294735dd |
| 정기 workflow | 400006658, active |
| cron | `30 11 * * 1-5`, 평일 20:30 KST |
| 다음 설정 슬롯 | 2026-10-30 20:30 KST; 실제 거래일은 기존 KIS 응답 검증 필요 |
| 기존 KIS Secrets | APP_KEY / APP_SECRET 등록 여부 모두 true; 값은 읽거나 출력하지 않음 |
| 중앙 공유 잠금 | 소유자 없음 |
| 오늘 정규 슬롯 | 아직 claim 없음 |
| 정기 접근 정책 | verified=false, shared.enabled=false |
| 정기 작업 자격의 장중 저장소 읽기 | GitHub API 404, UNREADABLE |
| 이번 검사 OAuth / 시장 HTTP / 슬롯 claim | 0 / 0 / 0 |

## 수정한 실제 오류

현재 main의 정기 수집 전 테스트가 실제 날짜에 의존해 **26 통과, 2 실패, 2 오류**로 재현됐다. 가상 시계를 쓰는 테스트에서 물리 호출 예산의 남은 시간 계산만 실제 시계를 사용해 `manual_insufficient_time_for_population` 경로로 실패했다.

`tests/test_regular_collector.py` 테스트 fixture 두 줄만 수정했다. 운영 시간 제한이나 시장 데이터 검증 규칙은 변경하지 않았다. 관련 **30개 테스트 모두 통과**했고, 독립 브랜치 `fix/regular-clock-tests-20261030`의 검증된 변경만 main `b1ae5791`에 반영했다. 운영 cron, 수집 코드, 원문·DB·JSON·대시보드와 Word 브리핑은 변경하지 않았다.

## 남아 있는 차단

두 저장소의 정식 공유 정책은 비활성 상태다. 장중 저장소에는 중앙 잠금 없이 실행하는 감독된 bootstrap 경로도 있으므로, 한쪽 verified 플래그만 true로 바꾸는 방식은 안전하지 않다. 저장소 간 GitHub 권한 연동, 모든 실제 인증·요청 경로의 중앙 잠금 참여, 토큰 만료·호출 정책의 배포 버전별 증거가 필요하다.

장중 저장소에도 새 무시장 API 권한 검사 브랜치를 push했다. 이 보고서 작성 시 해당 요청에 대응하는 새 결과 영수증은 아직 관측되지 않았다. 최근 보존된 10/29 결과에서는 `KIS_COORDINATOR_TOKEN`과 `OPERATIONS_READ_TOKEN`이 없고, 자체 GITHUB_TOKEN의 중앙 저장소 쓰기가 거부됐다. 이 과거 결과를 오늘 새로 검증한 사실로 표시하지 않는다. 현재 새 검사 결과와 과거 영수증은 결과 JSON에서 구분한다.

현재 클라우드의 허용 목록에는 api.github.com이 없고 GitHub Secret 관리 도구가 제공되지 않는다. 사용자 토큰을 새로 만들거나 Secret에 복사하지 않았다. 등록·권한 변경을 완료했다고 표시하지 않는다.

## 운영 판정

오늘 20:30 슬롯은 등록돼 있으나 **현재 상태로 정상 자동 수집을 보장할 수 없으며, 공유 정책에서 차단될 예정이다**. GitHub 예약은 정각 실행·누락 방지를 보장하지 않는다. 기존 170분 창, 거래일 검증, 중복 원장과 실패 보존 정책은 그대로 유지한다. KIS 공식 확정 및 KRX 대조 상태도 완화하지 않는다.

상세 증거: [결과 JSON](preflight-20261030.json).
