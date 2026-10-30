# 2026-10-30 KIS 공유 계정 정기 운영 반영 결과

판정: **READY — 예약·공유 계정 조정·배포 검증 완료, 첫 정규 실수집 미검증**.

| 항목 | 확인 결과 |
| --- | --- |
| qweasd 실제 main | f36ab970428564e7569ae37d61f2cfa3f644e119 |
| kis-intraday-monitor 실제 main | 8fe527d87271d28431f14b36a233596ab3b73b18 |
| 실제 등록 workflow | 400006658, active, 기본 브랜치 main |
| 기존 cron | 30 11 * * 1-5, 변경 없음 |
| 다음 설정 슬롯 | 2026-10-30 20:30 KST; GitHub 실행 지연·누락 가능 |
| 중앙 운영 잠금 | 최종 확인 시 소유자 없음 |
| 오늘 슬롯 claim | 아직 없음; 배포 검증이 슬롯을 소비하지 않음 |
| 이번 설정 과정의 새 OAuth / KIS 시장 HTTP | 0 / 0 |
| 공급자 최종 확정 / KRX 대조 | UNVERIFIED / PENDING_KRX 유지 |

## 실제 실행 증거

- [새 조정자 자격 검사 41021025811](https://github.com/0baselabkim-debug/kis-intraday-monitor/actions/runs/41021025811): KIS_COORDINATOR_TOKEN으로 중앙 쓰기·잠금 획득·해제 PASS.
- [전체시장 측 상호 배제 검사 41025361589](https://github.com/0baselabkim-debug/qweasd/actions/runs/41025361589) 및 [장중 측 검사 41025365240](https://github.com/0baselabkim-debug/kis-intraday-monitor/actions/runs/41025365240): 두 실제 Actions 실행 모두 completed/success. M 소유 중 Q 거절, Q 소유 중 M 거절, 인계와 최종 소유자 없음 확인. 실제 인증·시장 호출 없음.
- [장중 main 활성화 검사 41026281746](https://github.com/0baselabkim-debug/kis-intraday-monitor/actions/runs/41026281746): 실제 main 설정·원문 증거 해시·검토된 코드·기존 KIS Secret 등록 여부 PASS. 키 값 출력 및 실제 KIS 인증 없음.
- [정기 경로 배포 검사 41027170596](https://github.com/0baselabkim-debug/qweasd/actions/runs/41027170596): SUCCESS. 공식 GitHub API가 main의 활성 cron과 READY_SHARED_COORDINATION_FIRST_REGULAR_RUN_NOT_VERIFIED를 확인함. 시장 HTTP 0, 슬롯 claim false.

처음 빨간 X가 표시된 41020204832는 실제 중앙 잠금 시험 후 검사 결과 발행 시 Git 부모 객체가 없어 실패했다. 발행 전 기존 이력을 fetch하도록 수정했고, 새 자격의 실패로 오인하지 않았다.

## 적용한 보호 장치

모든 승인된 장중 bootstrap/수급/남은 요청 경로가 기존 데이터 요청 상한을 유지하면서 중앙 계정 잠금에 참여한다. 자기 저장소에는 기존 GITHUB_TOKEN을 사용하고, 중앙 원장에는 새 조정자 자격을 단계 내 GH_TOKEN으로 전달한다. 별도 운영 조회 자격이 없을 때 동일 조정자 자격으로 qweasd 상태를 읽는다. 기존 Secret 값은 수정하지 않았다.

운영 프로필은 실제 양방향 잠금 시험과 배포된 코드 해시에 연결했다. 단순 verified=true로 동작하지 않는다. 응답이 닫힌 뒤 발생한 장중 데이터 검증 실패는 잠금을 해제하며, 미확인 통신·강제 종료·만료된 미정리 소유자는 자동 해제하지 않는다. 기존 실행 취소와 강제 잠금 초기화는 수행하지 않았다.

정기 수집은 기존 호출 간격·타임아웃·복구 상한을 유지하고 준비·인증·실패 요청까지 합산해 5,600회를 넘지 못한다. 토큰은 메모리에서만 재사용하고, 미확인 공급자 만료 시간대는 보수적으로 계산한다. 기준일·170분 실행 창·중복 원장·동적 적격 모집단·원문 및 DB/JSON 검증은 유지한다.

정기 경로의 비공개 장중 저장소 보조 조회는 여전히 자체 토큰으로 읽기 불가다. 이를 조용한 상태로 간주하지 않으며, 계정 상호 배제는 두 저장소가 실제 검증한 공통 원장과 코드 해시로 판단한다. 불명확한 소유자 복구는 계속 별도 확인이 필요하다.

## 테스트 및 불변 확인

- 전체시장 저장소 전체 회귀: 458 통과. 이후 활성화 상태 관련 회귀: 77 통과, 실패 0. 두 그룹은 중복되며 단순 합산하지 않는다.
- 장중 저장소 활성화 상태 전체 검사: 531 통과, 실패·오류·실제 네트워크 이벤트 0.
- 두 저장소 실제 GitHub 잠금·인계 및 main 활성화 검증: PASS.
- 기존 수집 대상 필터·가격/수급 파서·원본 데이터·운영 snapshot·DB/JSON·대시보드 변경 없음.
- 장중 기존 트리거와 수동 실행 조건 변경 없음. 20:30 및 Word 브리핑 시간 변경 없음. 과거 중단된 workflow 재활성화와 새 반복 예약 없음.

실제 첫 정규 시장 수집의 성공은 아직 확인하지 않았다. 다음 예정 슬롯의 거래일 확인과 실제 API·원문·아티팩트 검증을 통과해야 COMPLETE_PROVISIONAL로 기록된다. 현재 데이터를 새로 수집했다고 표시하지 않았다. 아티팩트 보존 90일은 외부 영구 백업과 다르다.

사용자가 신고한 GitHub 토큰 만료일은 2027-01-28이며, 접근 검토는 그보다 이른 2027-01-27T00:00:00Z에 만료된다. 현재 추가 사용자 설정은 필요하지 않다. 상세 증거와 미확인 공급자 정책 범위는 [결과 JSON](operating-result-20261030.json), [운영 문서](../../docs/shared-account-operating-20261030.md), [소스 결합 증거](rollout-proof-20261030.json)에 보존했다.
