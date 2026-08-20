# Pediatric Home Visit: What to Label, What to Show in the Note

Decided: 2026-08-20. Integrates three sources that converged independently:

- Codex analysis (saved in second-brain
  `research/pediatric-complex-home-care-records-and-product-landscape-2026-08-20.md`):
  split nursing vs visiting-rehab records, shared handoff summary on top,
  demote the hip-angle number from demo protagonist to research candidate.
- This repo's vision-to-SOAP survey
  (`vision-to-soap-research-and-v1-architecture.md`): numbers come from pose
  pipelines, narrative evidence from VLM, S/A/P substance from audio; no VLM
  reliably measures angles from egocentric video (unvalidated).
- Existing labeling infrastructure:
  `~/projects/home-rehab-labeling` ("MOAI Rehab Label Studio v1") already has a
  severe-pediatric schema `review_apps/label_schemas/pediatric_home_v1.json`
  with dual-rater gold samples and a PHI-safe workflow. Do not build a new
  labeling tool.

The two independent conclusions agree: the egocentric hip-flexion delta
(72°→88°) is not the story. The story is safety continuity (nursing) plus
functional-task performance under stated conditions (rehab), with the
therapist/nurse as reviewer of AI drafts.

## 1. Note structure (display contract)

Three zones, replacing one mixed "중증소아 방문재활 요약":

### Zone A — 공통 인계 요약 (shared, both professions, always on top)
1. 보호자가 오늘 가장 걱정하는 것
2. 평소 기준상태(baseline) 대비 달라진 점
3. 현재 안전상태와 red flag (아동별 기준으로 표시, 범용 숫자 금지)
4. 재활·자세·의료기기 관련 변경 사항
5. 누가 무엇을 언제 확인할 것인지 (담당·기한)

Every item carries a **source tag** (also stored per field):
`보호자 보고 | 간호사 관찰 | 치료사 측정 | 기기 측정 | AI 초안 | 의료진 확인 완료`.
This is the anti-"AI가 판단했다" mechanism and doubles as training-data
provenance.

### Zone B — 간호기록 (nursing)
Existing physio_app fields cover: safetySnapshot (SpO2/HR baseline, stability,
pressure risk, escalation, lines/tubes), respiratorySupport (device, IPAP/EPAP,
FiO2, hours), feedingRoute, caregiverReport, caregiverEducation.

**Fields to ADD to `pediatric-complex-home-note.schema.ts`:**
- 흡인: 횟수/일, 분비물 양·색 변화 (평소 대비)
- feeding tolerance: 구토, 복부팽만, 흡인 의심, 배변·배뇨 변화
- 경련: 횟수, 지속시간, 구조약(rescue med) 사용 여부
- 투약: 변경, 누락, PRN 사용, 발열, ER/입원/외래 이용
- r-FLACC 점수 (+ 보호자가 알려준 이 아동 특유의 통증 행동 텍스트)
- 보호자 teach-back: 시연 여부(boolean) — 현재는 understanding enum만 있음
- 아동별 응급계획: 연락 기준 텍스트, 연락 대상

### Zone C — 방문재활 (visiting rehab)
Existing fields cover most of Codex's table: functionalTasks(taskCode,
assistanceLevel, tolerance, qualityNote), postureAndMobility(GMFCS, head/trunk
control), equipmentAndOrthosis(seating fit, tolerance), carePlan.

**Adjustments:**
- 보호자 우선 목표 필드 신설 (생활 연결 목표 문장; GAS의 방문 단위 앵커)
- functionalTasks에 유지시간(초)·반복 수·중단 사유 구조화 필드 추가
- 치료 전·중·후 r-FLACC와 호흡 내성(간호 red-flag와 공유)
- GMFCS는 배경 분류로만 표시, 방문별 변화 지표로 쓰지 않음 (문헌 근거)
- 측정 주기 계층: 방문마다(보조수준·유지시간·내성·통증) / 몇 주(GAS) /
  정기(GMFM, SATCo) / 장기(CPCHILD)

### 영상 각도의 자리
`MET.ROM.*` pose 지표는 Zone C의 보조자료 + 연구 후보로만 표시
(`review_required`, goniometer 대조 검증 전까지 기록의 주 지표 금지).
파일럿 체크리스트에 goniometer 대조 측정 추가.

## 2. Labeling plan (what gets labeled, in which tool)

Tool: `home-rehab-labeling` pediatric_home_v1 — already has the right clip
taxonomy. Workbench: `python3 scripts/serve_workbench.py --port 8877`.

Per pilot session (target 1–2h labeling):

| Layer | What | Feeds |
|---|---|---|
| 구간 | temporal segments, clip_type from pediatric_home_v1 (supported_sitting, supine_posture, device_tcan/peg/vent, scoliosis_screen, feeding_swallow, rom_clip …) | exercise/event recognition eval; future VLM prompting |
| 인스턴스 | assistance level, 유지시간, tolerance, 0–4 quality rubric, distress_red_flag, r-FLACC(해당 시) | Gate 3 skeleton quality scorer (30+ pts, 수백 clips 시점) |
| 기기 | device site state (stoma 발적/분비물, PEG 누출/육아조직, vent 인터페이스/알람) — pediatric_home_v1에 이미 정의됨 | nursing-record vision evidence; never auto-conclusion |
| 세션 | gold note 1개 = Zone A 5항목 + Zone B + Zone C, 치료사 직접 작성 | LLM draft eval (누락률 채점), few-shot pool |
| 공통 | review_status, video_quality, occlusion, label_confidence | data quality gates (기존 rayban_pt readiness와 정렬) |

Alignment note: rayban_pt `rehab_labels` (facet: core_task, assist_level,
performance, usable_for_training)과 pediatric_home_v1은 개념이 겹치지만 코드가
다르다. 매핑 스펙은 별도 문서
(`pediatric-label-schema-mapping.md`, 작성 예정)에서 고정한다.

## 3. What the AI drafts vs what humans own

| Zone | AI 초안 가능 (3-lane) | 반드시 사람 |
|---|---|---|
| A 인계 | 기준 대비 변화 후보, 기기 변경 감지 | red-flag 판정, 연계 결정 |
| B 간호 | 전사에서 흡인/주입/투약 이벤트 추출, 기기 부위 사진 evidence 첨부 | 상태 판정, 응급기준 적용 |
| C 재활 | 구간별 과제 식별, 보조수준·시간 후보, 포즈 보조지표 | 품질 판단(0–4), 목표 조정, 홈프로그램 |

All drafts remain `requires_approval: true`; source tag `AI 초안` until a
clinician flips it to `의료진 확인 완료`.

## 4. Demo (9/4) synthetic case

합성 사례: 중증 CP, GMFCS V, PEG 영양, 야간 BiPAP, 기관흡인 필요.
간호 화면 → Zone B 항목, 재활 화면 → Zone C(보호자 목표 "식사 전 지지 앉기
편안하게"), 마지막 공유 화면 → Zone A 다섯 줄. physio_app 데모 픽스처
(GMFCS IV, hip +16° 중심)는 전면 교체 필요 — Codex 판정과 동일.

## 5. Chart form provenance (차트양식)

원본 개인 차트 서식(.hwp)은 로컬에 없음. Google Drive `snuh/프린트 자료`
(ROM & MMT, BBS, DEMMI, 보행능력평가양식)와 `기록지` 폴더(중증소아재택의료
업무 프로토콜 포함)에만 존재 — 현재 연결된 Drive 커넥터 계정에서는 안 보이므로
yonsei 계정 접근이 필요. physio_app의 `assessment_form_templates` DB가
디지털 재구현본이며, .hwp 원본 필드와의 명시적 매핑은 아직 없다.
