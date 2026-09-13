# HUD 입력 일관성 진행 기록

목표: 외부 Neural Band 입력이 웹 HUD의 명시적 검토 흐름을 우회하지 않게 한다.
범위: 제스처 매핑, 명시적 후보 결정 검증, 기존 브리지 회귀 테스트와 문서.
소유자: 현재 Codex 작업. 기존 변경을 보존하며 design/dry_run을 유지한다.

결정: pinch는 select_focused로 전달한다. approve/discard 의미의 외부 제스처는
metadata.expected_candidate_id를 필수로 받으며, 기존 후보 ID 일치 검사를 재사용한다.
외부 입력이 없는 네이티브 Display 버튼·새 화면·배포는 이번 범위에 포함하지 않는다.

검증 명령(server 디렉터리):
- `./.venv/bin/python smoke_test.py`
- `./.venv/bin/python visit_session_smoke_test.py`
- `./.venv/bin/python mrbd_hud_smoke_test.py`
- 프로젝트 루트의 `git diff --check`

완료 조건: 핀치는 결정 없이 웹 큐로 전달되고, ID 없는 결정은 거부되며,
오래된 ID가 현재 후보를 변경하지 않고, 올바른 ID의 명시적 결정은 동작한다.
실패 시 같은 검증으로 최소 원인을 수정하며, 서비스·자원 부족이 반복되면 중단하고 기록한다.

현재: 완료. 위 세 스모크 테스트와 `git diff --check` 모두 통과.

검증 근거:
- 핀치 후 후보 상태가 candidate로 유지됨.
- 웹 큐로 select_focused가 한 번 전달되고 네이티브 큐는 비어 있음.
- 승인/폐기 의미의 7개 제스처에서 ID 누락·오래된 ID를 거부하고 DB 후보 상태를 보존함.
- 올바른 ID를 담은 명시적 승인은 해당 후보를 confirmed_by_provider로 변경함.
- 기존 웹 후보 결정과 방문/녹화 흐름의 회귀 테스트도 통과.
- 로그: `/tmp/rayban-hud-input-smoke.log`, `/tmp/rayban-hud-input-visits.log`,
  `/tmp/rayban-hud-input-web.log`.

다음 단계: 실제 Neural Band에서 포커스 선택→후보 열기→보류→다시 열어 승인 확인.
실기기 입력 지연·오프라인 큐 재전달과 네이티브 HUD는 아직 미검증.
배포·생산 DB 쓰기·반복 작업 활성화는 수행하지 않았다.
