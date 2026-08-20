# Pediatric Label Schema Mapping Spec (v1)

Fixes the mapping promised in `pediatric-labeling-and-soap-display-design.md` §2
("매핑 스펙은 별도 문서에서 고정한다"). Scope: 중증소아 재택방문 capture →
labeling → note 파이프라인의 세 시스템 사이 개념/enum/식별자 고정.

**Sources read (2026-08-20, 코드 그대로 인용, 추측 금지):**

| System | Path | What is authoritative here |
|---|---|---|
| `pediatric_home_v1` | `~/projects/home-rehab-labeling/review_apps/label_schemas/pediatric_home_v1.json` | `common_fields`, `task_fields` |
| labeling 레코드 | `~/projects/home-rehab-labeling/review_apps/exports/rehab_home_labels.jsonl`, `rehab_labeling.sqlite3` (`label_latest`, `label_reviews`) | clip 식별자, `evidence_spans` |
| rayban_pt taxonomy | `server/routers/charts.py` `LABEL_TAXONOMY_V0` (L61–301), `RehabLabelPayload` (L36–58), `upsert_label` (L504–577) | 라벨 enum, 저장 컬럼 |
| rayban_pt 저장 | `server/schema.sql` `rehab_labels` (L52–76), `capture_events` (L130–151), `visit_sessions` (L153–178) | 테이블 컬럼 |
| rayban_pt 추출 | `server/lib/transcript_capture.py`, `server/bridge_core.py` L418, L509–620 | `candidate_type`, payload 키, status |
| physio_app 코드 | `src/lib/utils/pediatric-complex-home-observations.ts` | `PEDIATRIC_COMPLEX_HOME_SYNC_CODES` (32개) |
| physio_app 스키마 | `src/lib/schemas/activity/pediatric-complex-home-note.schema.ts` | Zod enum |

**표기 규약**

- `[PROPOSED]` = 세 시스템 어디에도 없는, 이 문서가 신설을 제안하는 필드/값.
- 갭 판정: `add field`(신설 필요) / `map-with-loss`(기존 필드로 매핑하되 정보 손실) /
  `out-of-scope`(v1에서 매핑하지 않음).
- Zone: A 인계 / B 간호 / C 재활 (design doc §1).

---

## A. Canonical concept table

`—` = 해당 시스템에 대응 필드 없음.

| # | 개념 | pediatric_home_v1 | rayban_pt (`rehab_labels` / `capture_events`) | physio_app (observation code / schema field) | Zone | 갭 판정 |
|---|---|---|---|---|---|---|
| 1 | clip/segment 종류 | `common_fields.clip_type` (11값), `custom_clip_type`, `primary_task` | `rehab_labels.core_task` (17값) + `custom_task`; `capture_events.candidate_type` (15 rule값) | `functionalTasks[].taskCode` (`FunctionalTaskCodeSchema`, 7값) | C | map-with-loss — §B.1 crosswalk. 세 어휘 모두 상호 부분집합이 아님 |
| 2 | 구간 시간 경계 | `evidence_spans[].{start_sec,end_sec,label}` (초, float, `end_sec` null 허용) | `capture_events.start_ms` / `end_ms` (INTEGER ms). `rehab_labels`에는 시간 컬럼 **없음** | — (physio_app 노트에 구간 개념 없음) | C | add field — §C.2. 단위(sec↔ms) + `rehab_labels` 무시간 문제 |
| 3 | 보조 수준 (assistance) | `supported_sitting.sitting_support_level`, `standing_gait.standing_support_level`, `feeding_swallow.caregiver_assistance_level` | `rehab_labels.assist_level` / `LABEL_TAXONOMY_V0["assist_level"]` (9값); `capture_events.payload.assist_level` | `AssistanceLevelSchema` (7값) → `rolling_status` / `sitting_status` observation의 **연결 문자열 일부** | C | map-with-loss — §B.2. physio 쪽은 `createTaskStatusObservation`이 `[assistanceLevel, tolerance, qualityNote].join(' \| ')` 로 뭉갬 |
| 4 | 내성 (tolerance) | `supine_posture.exercise_tolerance`, `supported_sitting.exercise_tolerance` (5값) | `rehab_labels.tolerance` / taxonomy `tolerance` (5값) | `TaskToleranceSchema`(과제), `SessionToleranceSchema`(세션) → `session_tolerance` | B(호흡 내성 공유) / C | map-with-loss — §B.3. `stopped`에 대응 없음 |
| 5 | 동작 품질 0–4 rubric | — | — | — | C | **add field (3개 시스템 전부)** — §A.1 |
| 6 | distress red flag | `common_fields.distress_red_flag` (`yes\|no\|uncertain`) | `rehab_labels.flags` JSON (taxonomy `flags`에 `pain`,`safety_risk`,`needs_review`); `capture_events.payload.safety_flags` (9값, `_safety_flags()`) | `safetySnapshot.redFlags`(자유 텍스트) + `escalationNeeded`(boolean) → `escalation_needed` | A, B | map-with-loss — §B.5. `uncertain` 표현 불가 |
| 7 | 기기 red flag (긴급) | `device_tcan/device_peg/device_vent/device_airway.urgent_device_red_flag` | — (타입 필드 없음; `flags`에 `safety_risk`로만) | `escalationNeeded` boolean + `medicalDeviceNotes` 텍스트 | A, B | add field — bridge에 타입 필드 없음 |
| 8 | 기관절개 부위 상태 | `device_tcan.{tcan_visible, tcan_position_issue, stoma_redness, stoma_discharge, bleeding_visible, secretion_burden}` | — (`capture_events.payload` 자유 JSON에만 적재 가능) | `linesTubesPresent[]`(문자열) + `medicalDeviceNotes` | B | map-with-loss (bridge), add field (physio 타입화) |
| 9 | PEG 부위 상태 | `device_peg.{peg_visible, peg_position_issue, site_redness, site_discharge, leakage_visible, granulation_tissue_suspected}` | — | `feedingRoute='peg'` + `medicalDeviceNotes` | B | map-with-loss — 육아조직/누출은 자유 텍스트로만 |
| 10 | vent/airway 인터페이스 상태 | `device_vent.{vent_interface_visible, mask_position_issue, strap_issue, visible_respiratory_distress, alarm_present}`, `device_airway.{airway_device_type, interface_position_issue, secretion_burden}` | — | `respiratorySupport.{deviceType, ipap, epap, backupRate, fio2, hoursPerDay, airwayNotes}` → `resp_support_type`, `resp_support_settings_text` | B | map-with-loss — 영상 관찰(마스크 위치/알람)이 설정값 필드로 흡수 불가 |
| 11 | r-FLACC | — | — | — | B(치료 전·중·후는 C 공유) | **add field (3개 시스템 전부)** — §A.2 |
| 12 | review status | `common_fields.review_status` (`pending\|labeled\|skipped\|excluded`), `skip_reason`(8값), `label_reviews.review_status` | `rehab_labels.review_status` (taxonomy 5값, 기본 `'reviewed'`); `capture_events.status` = `CAPTURE_EVENT_STATUSES = {"draft","edited","approved","rejected"}` (`bridge_core.py:418`) | `InferredSidecarSourceSchema` (`unknown\|ai_suggested\|clinician_confirmed\|clinician_entered`) | 전 Zone | map-with-loss — §B.4. 3중 어휘, `skipped` 무대응 |
| 13 | confidence | `common_fields.label_confidence` (`low\|medium\|high`) | `rehab_labels.label_confidence` REAL (0≤x≤1, `charts.py:509`); `capture_events.confidence` REAL (transcript는 `max(0.5,min(0.95,c))`로 클램프) | `sidecar.inferredLabels.*.confidence` (`number \| null`) | 전 Zone | map-with-loss — §B.6. 서수↔연속 변환 규칙이 코드에 없음 |
| 14 | video quality | `common_fields.video_quality` (`adequate\|limited\|poor`) | — | — | (노트 비표시, 품질게이트 전용) | out-of-scope (노트) / labeling 전용 유지 |
| 15 | occlusion / assessable | `common_fields.occlusion`(4값), `assessable`(`true\|false`) | — | — | (노트 비표시) | out-of-scope (노트) |
| 16 | laterality | `common_fields.laterality` (`left\|right\|bilateral\|midline\|not_applicable`) | `capture_events.payload.laterality` (`_laterality()` → `left\|right\|bilateral\|""`) | observation `laterality?: 'left'\|'right'\|'bilateral'` | C | map-with-loss — `midline`, `not_applicable` 표현 불가 |
| 17 | 자세/체위 | `clip_type` 자체가 체위를 겸함 (`supine_posture`, `supported_sitting`, `prone_on_elbows`, `standing_gait`) | `rehab_labels.body_position` (taxonomy 9값) | — (체위 전용 필드 없음) | C | map-with-loss — physio는 `qualityNote` 텍스트로만 |
| 18 | 두부/체간 조절 | `supported_sitting.{head_control_supported_sitting, trunk_control_supported_sitting}` (`absent\|poor\|partial\|fair\|not_assessable`) | — | `ControlLevelSchema` (`good\|fair\|poor\|none\|unknown`) → `head_control_level`, `trunk_control_level` | C | map-with-loss — §B.7. `partial`↔`good` 비대칭 |
| 19 | 유지시간 / 반복 수 | — | `rehab_labels.hold_duration_seconds` REAL, `repetition_count` INTEGER; `capture_events.payload.{hold_duration_seconds, repetition_count, set_count, rest_duration_seconds}` | — | C | add field (labeling + physio) — design doc §Zone C "유지시간(초)·반복 수" 요구 |
| 20 | 중단 사유 | `exercise_tolerance='stopped'` 로만 (사유 없음) | `rehab_labels.notes` 텍스트 | — | C | add field (3개 시스템) |
| 21 | 보상 동작 | `postural_asymmetry`, `involuntary_movement`, `scoliosis_screen.*` (부분 대응) | `rehab_labels.compensations` JSON (taxonomy 15값) | — | C | map-with-loss — 세 어휘가 서로 다른 축 |
| 22 | 피로 | — | `rehab_labels.fatigue_level` (taxonomy `none\|mild\|moderate\|severe\|uncertain`) — 단 `_fatigue_level()`은 `high\|moderate\|mild\|present` 반환 (**taxonomy 밖**) | `caregiverReport.fatigueScore` (int 1–5, nullable) | B, C | map-with-loss + 기존 불일치 (§B.8) |
| 23 | GMFCS | — | — | `GmfcsLevelSchema` (`I\|II\|III\|IV\|V\|unknown`) → `GMFCS` | C (배경 분류만) | out-of-scope for labeling — design doc §Zone C "방문별 변화 지표로 쓰지 않음" |
| 24 | ROM / 각도 | `rom_clip.{joint, motion, side, rom_assessable, end_range_visible, angle_bin, angle_method}` | `capture_events.candidate_type='rom_measurement'`, `source_type='pose'`, `payload.{metric_id, side, value}`; taxonomy `core_task='range_of_motion'` | `hip_status`, `scoliosis_summary` (자유 문자열) | C 보조자료만 | map-with-loss — `angle_bin`은 구간, bridge는 연속값. `review_required` 유지 |
| 25 | 섭식 경로 / 연하 | `feeding_swallow.{feeding_route_visible, swallow_safety_concern, coughing_choking_visible, secretion_or_drooling, positioning_for_feeding}` | — | `FeedingRouteSchema` (`oral\|peg\|ng\|mixed\|other\|unknown`) → `feeding_route` | B | map-with-loss — §B.9. `tube`가 `peg`/`ng` 붕괴 |
| 26 | 보조기/좌석 | `device_afo.{afo_visible, afo_side, fit_concern, skin_pressure_visible, ankle_alignment, foot_contact_pattern}` | `capture_events.candidate_type='orthosis_assistive_device'`; taxonomy `intervention_type='orthosis_assistive_device'` | `AfoUseStatusSchema`, `DeviceFrequencySchema`, `RehabilitationDeviceEntrySchema.{type,useStatus,fitStatus,action}` → `orthosis_afo_use`, `seating_system_status`, `rehab_device_*` | A(변경), C | map-with-loss — 영상 착용상태 vs 사용빈도는 다른 축 |
| 27 | 보호자 동석 / 보조 | `feeding_swallow.caregiver_assistance_level` | `rehab_labels.caregiver_present` INTEGER(nullable); taxonomy `flags`에 `caregiver_assist` | `caregiverReport.*`, `caregiverEducation.{topics, completedTopics, understanding}` | A, B | map-with-loss — teach-back 시연 boolean 없음(design doc §Zone B 요구) |
| 28 | 보호자 우선 목표 (GAS 앵커) | — | — | — (`nextVisitFocus.priorities` 자유 텍스트만) | A, C | add field — design doc §Zone C "보호자 우선 목표 필드 신설" |
| 29 | 학습용 사용 가능 | — | `rehab_labels.usable_for_training` INTEGER | — | (노트 비표시) | add field (labeling) — 게이트 정렬용 |
| 30 | 출처 태그 (6종) | `annotator`, `annotator_email`, `ai_draft_fields[]`, `ai_draft_note`, `ai_draft_at` | `capture_events.source_type`(현재 리터럴 `'transcript'`,`'pose'`만 삽입), `payload.{derived_from, capture_origin, review_required}`, `rehab_labels.{provider_role, reviewer_person_id}` | observation `source_type: 'manual'` (하드코딩), `sidecar.*.source` | 전 Zone (필드별 표시) | add field — §C.3 |

**매핑된 개념 30개 / 갭 표시된 개념 25개** (14·15·23만 의도적 out-of-scope, 나머지 22개는 add field 또는 map-with-loss).

### A.1 [PROPOSED] 동작 품질 0–4 rubric

세 시스템 어디에도 존재하지 않음. design doc §2 "인스턴스" 레이어 요구사항.

- `pediatric_home_v1.common_fields.movement_quality_0_4`: `["0","1","2","3","4","not_assessable"]` [PROPOSED]
- rayban_pt `rehab_labels.quality_score INTEGER` [PROPOSED] + `capture_events.payload.quality_0_4` [PROPOSED]
- physio_app `functionalTasks[].qualityScore: z.number().int().min(0).max(4).nullable()` [PROPOSED]
- rubric 정의 자체(0=수행 불가 … 4=정상 수준)는 이 문서 밖. 치료사 합의 후 별도 고정.

### A.2 [PROPOSED] r-FLACC

`grep -ri flacc` 결과 세 저장소 어디에도 없음 (physio_app `panel-exam-form.tsx`의
`0 (flaccid)`는 근긴장도이며 무관).

- `pediatric_home_v1.task_fields.<clip>.r_flacc_total`: `0`–`10` 정수 [PROPOSED],
  `r_flacc_assessable`: `["yes","no"]` [PROPOSED]
- rayban_pt `capture_events.candidate_type = 'pain_behavior'` [PROPOSED],
  `payload.{r_flacc_total, r_flacc_timing}` (`timing ∈ pre|intra|post`) [PROPOSED]
- physio_app: observation code `r_flacc_total` [PROPOSED] +
  `painBehavior: { rFlaccPre, rFlaccIntra, rFlaccPost, childSpecificPainBehaviorText }` [PROPOSED]
- 기존 `_pain_score()`(VAS/NRS 정규식)와 **혼용 금지** — 자기보고 척도이므로 중증소아에 부적합.

---

## B. Enum value crosswalks

### B.1 clip/segment 종류

| pediatric_home_v1 `clip_type` | rayban_pt `core_task` | rayban_pt `candidate_type` | physio_app `taskCode` | 비고 |
|---|---|---|---|---|
| `supine_posture` | `positioning` | `positioning_alignment` | — | physio에 앙와위 과제 코드 없음 |
| `supported_sitting` | `sitting_balance` | `functional_task` | `supported_sitting` | 3자 일치 |
| `standing_gait` | `gait_practice` (또는 `standing_balance`) | `functional_task` | `gait` | 1:多 — 라벨툴이 서기/보행 미분리 |
| `prone_on_elbows` | `prone_head_control` | `functional_task` | `head_control` | 근사 매핑 |
| `reach_grasp` | `reaching` | `functional_task` | — | physio 무대응 |
| `rom_clip` | `range_of_motion` | `rom_measurement` | — | Zone C 보조자료만 |
| `scoliosis_screen` | `positioning` | `positioning_alignment` | — | physio는 `scoliosis_summary` 텍스트 |
| `device_tcan` | — | `safety_check` | — | 간호 축, rehab core_task 없음 |
| `device_peg` | — | `safety_check` | — | 동일 |
| `device_vent` | — | `safety_check` | — | 동일 |
| `device_airway` (`custom_clip_type`으로 실제 사용됨) | — | `safety_check` | — | 배치3 데이터에서 `clip_type='other'` + `custom_clip_type='device_airway'` 형태로 존재 |
| `device_afo` | — | `orthosis_assistive_device` | — | physio는 `orthosis_afo_use` |
| `feeding_swallow` | — | `safety_check` | — | physio `feeding_route` |
| `other` + `custom_clip_type` | `other` + `custom_task` | — | `other` | 3자 모두 escape hatch 보유 |
| — | `sit_to_stand`, `balance_test`, `strength_test`, `movement_screen`, `breathing_control`, `strength_training`, `motor_control`, `pilates_control`, `caregiver_handling` | — | `rolling`, `pull_to_sit`, `side_lying_transition` | 라벨툴 무대응. `rolling`/`pull_to_sit`은 중증소아에서 실제 쓰임 → `pediatric_home_v1.clip_type`에 `rolling`, `pull_to_sit` 추가 [PROPOSED] |

### B.2 보조 수준 (assistance level)

라벨툴 필드 3개(`sitting_support_level`, `standing_support_level`,
`caregiver_assistance_level`)가 같은 값집합을 쓰되 마지막 값만 다름
(앞 둘은 `not_assessable`, `caregiver_assistance_level`은 `uncertain`).

| pediatric_home_v1 | rayban_pt `assist_level` | physio_app `AssistanceLevelSchema` | 손실 |
|---|---|---|---|
| `max` | `maximal_assist` | `max_assist` | 없음 |
| `mod` | `moderate_assist` | `mod_assist` | 없음 |
| `min` | `minimal_assist` | `min_assist` | 없음 |
| `supervision` | `supervision` | — | **physio 무대응**. `verbal_cue`로 강등 금지(의미 다름). `AssistanceLevelSchema`에 `supervision` 추가 [PROPOSED] |
| `independent` | `independent` | `independent` | 없음 |
| `not_assessable` / `uncertain` | `not_tested` | `not_tested` | 의미 손실 — "평가 불가"와 "미실시"가 한 값으로 붕괴 |
| — | `standby_assist` | — | 라벨툴·physio 무대응 → 라벨링 시 `min`으로 흡수(손실 기록 필요) |
| — | `contact_guard` | — | 동일 |
| — | `dependent` | `dependent` | **라벨툴 무대응** — GMFCS V 아동에서 `max`와 구분 불가. `pediatric_home_v1`에 `dependent` 추가 [PROPOSED] |
| — | — | `verbal_cue` | 나머지 둘 무대응 |

정규 방향: **labeling → bridge → physio**. 역방향(physio→labeling) 변환은 v1에서 하지 않음.

### B.3 내성 (tolerance)

| pediatric_home_v1 `exercise_tolerance` | rayban_pt `tolerance` | physio_app `TaskToleranceSchema` / `SessionToleranceSchema` | 손실 |
|---|---|---|---|
| `good` | `good` | `good` | 없음 |
| `reduced` | `fair` | `limited` | 라벨만 다름, 순서 동일 |
| `poor` | `poor` | `poor` | 없음 |
| `stopped` | — (`poor` + `flags`에 기록) | — | **중단 사실이 소실됨**. 세 시스템에 `stopped` 추가 [PROPOSED] |
| `uncertain` | `not_observed` | `unknown` | 의미 손실 — "불확실"과 "미관찰" 붕괴 |
| — | `tolerated` | — | rayban 전용, `good`과 축퇴. `_tolerance()`는 `good\|fair\|poor`만 반환하므로 실사용 없음 |

### B.4 review status

| pediatric_home_v1 `review_status` | rayban `rehab_labels.review_status` | rayban `capture_events.status` | physio_app `InferredSidecarSourceSchema` |
|---|---|---|---|
| `pending` | `unreviewed` | `draft` | `ai_suggested`(AI 초안 존재 시) / `unknown` |
| `labeled` | `reviewed` | `edited` | `clinician_entered` |
| — | `corrected` | `edited` | `clinician_confirmed` |
| — | `approved` | `approved` | `clinician_confirmed` |
| `excluded` | `rejected` | `rejected` | — |
| `skipped` (+ `skip_reason` 8값) | — | — | — | 

`skipped`는 bridge/physio에 대응이 없음 → labeling 내부 상태로만 유지하고 export에서 제외
(map-with-loss 아님, **의도적 필터**).

### B.5 distress / red flag

| pediatric_home_v1 `distress_red_flag`, `urgent_device_red_flag` | rayban `rehab_labels.flags` / `capture_events.payload.safety_flags` | physio_app |
|---|---|---|
| `yes` | `flags += "safety_risk"` (기기건은 `safety_flags += "respiratory_concern"` 등) | `escalationNeeded = true` + `safetySnapshot.redFlags` 텍스트 |
| `no` | 플래그 미포함 | `escalationNeeded = false` |
| `uncertain` | `flags += "needs_review"` | — (**boolean이라 표현 불가**) → `escalationUncertain: boolean` [PROPOSED] |

`_safety_flags()` 반환값 9종 = `fall_risk`, `pain`, `fatigue`, `poor_tolerance`,
`unsafe_environment`, `respiratory_concern`, `dizziness`, `chest_pain`,
`shortness_of_breath`. 이 중 `dizziness`/`chest_pain`은 중증소아 자기보고 불가 →
파일럿에서 무시(전사 규칙이 보호자 발화에 오작동할 수 있음).

taxonomy `flags` 8종 = `fatigue`, `postural_sway`, `pain`, `caregiver_assist`,
`safety_risk`, `low_attention`, `equipment_used`, `needs_review`.
**주의**: `upsert_label`은 `safety_flags`(또는 `flags`)를 `rehab_labels.flags`
**한 컬럼**에 JSON으로 넣는다 (`charts.py:507,569`). 별도 `safety_flags` 컬럼 없음.
따라서 두 어휘가 한 배열에 섞여 저장됨 — 소비 측에서 구분 불가.

### B.6 confidence

| pediatric_home_v1 `label_confidence` | rayban REAL 구간 [PROPOSED] | physio `sidecar.*.confidence` |
|---|---|---|
| `low` | `0.00 ≤ c < 0.60` | 그대로 전달 |
| `medium` | `0.60 ≤ c < 0.80` | 그대로 전달 |
| `high` | `0.80 ≤ c ≤ 1.00` | 그대로 전달 |
| (미기재) | `NULL` | `null` |

구간 경계는 **어느 코드에도 정의되어 있지 않음** → 이 문서가 신설.
`_create_transcript_capture_events`가 confidence를 `max(0.5, min(0.95, c))`로
클램프하므로 (`transcript_capture.py:961`) **전사 기반 후보는 구조적으로 `low`가 될 수 없다**.
`_RULES`의 원 confidence도 0.62–0.78 범위뿐. 사람이 `low`를 매긴 clip과
기계 후보를 같은 축에서 비교하면 안 됨.

### B.7 두부/체간 조절

| pediatric_home_v1 `head_control_supported_sitting` / `trunk_control_supported_sitting` | physio_app `ControlLevelSchema` (`head_control_level`, `trunk_control_level`) |
|---|---|
| `absent` | `none` |
| `poor` | `poor` |
| `partial` | — (**무대응**; `fair`로 올리면 과대평가) → `partial` 추가 [PROPOSED] |
| `fair` | `fair` |
| — | `good` (라벨툴 무대응; 중증소아 파일럿에서는 미사용 예상) |
| `not_assessable` | `unknown` |

rayban_pt에는 대응 필드 없음 (`core_task='prone_head_control'`은 과제이지 등급이 아님).

### B.8 피로 (기존 불일치)

`LABEL_TAXONOMY_V0["fatigue_level"]` = `none`, `mild`, `moderate`, `severe`, `uncertain`.
`transcript_capture._fatigue_level()` 반환 = `high`, `moderate`, `mild`, `present`.

→ `high`와 `present`는 **taxonomy에 없는 값**이며 `capture_events.payload.fatigue_level`에
그대로 들어간다. 라벨 승격 시 `high → severe`, `present → mild` 로 정규화할 것 [PROPOSED].
(코드 수정은 이 문서 범위 밖. 별도 이슈로 처리.)

physio `caregiverReport.fatigueScore`는 1–5 정수라 위 어휘와 축이 다름 → v1에서 연결하지 않음.

### B.9 섭식 경로

| pediatric_home_v1 `feeding_route_visible` | physio_app `FeedingRouteSchema` (`feeding_route`) |
|---|---|
| `oral` | `oral` |
| `tube` | `peg` 또는 `ng` (**영상만으로 구분 불가 → 승격 금지**, 노트값은 간호사 입력 우선) |
| `mixed` | `mixed` |
| `uncertain` / `not_assessable` | `unknown` |
| — | `other` |

규칙: `feeding_route_visible`는 **evidence**로만 쓰고 `feeding_route` observation을
자동 갱신하지 않는다 (design doc §3 "never auto-conclusion").

---

## C. Identity & provenance

### C.1 현재 식별자 (코드에 존재하는 것만)

**labeling clip 레코드** (`rehab_home_labels.jsonl`, `label_latest`):
`clip_id`, `asset_uid`, `patient_uid`, `visit_id`, `media_uri`, `source_root_label`,
`extension`, `media_type`, `schema_version`, `annotator`, `annotator_email`, `saved_at`,
`project_id`, `team_id`.
관측된 실값: `clip_id="PB3_0001"`, `asset_uid="asset_2d833168eaea1dc1ce62"`,
`patient_uid="patient_4510cb15d43f2f9e"`, `visit_id="2025-05-16"` (**날짜 문자열이지 세션 ID가 아님**).

**rayban_pt `capture_events`**: `id`, `visit_session_id`, `encounter_id`,
`organization_id`, `provider_person_id`, `subject_person_id`, `source_media_id`,
`source_event_id`, `source_type`, `event_type`, `candidate_type`, `start_ms`, `end_ms`,
`confidence`, `status`, `payload_json`, `reviewed_by`, `reviewed_at`.
`payload.extraction_key` = `sha256(f"{source_event_id}:{index}:{candidate_type}:{source_text}")`
(멱등 키, `bridge_core.py:559`).

**rayban_pt `rehab_labels`**: `event_id TEXT PRIMARY KEY … FOREIGN KEY (event_id) REFERENCES events(id)`.
→ **미디어 이벤트 1건당 라벨 1행**. `capture_events`를 참조하지 않고, 시간 컬럼도 없다.

### C.2 [PROPOSED] clip ↔ capture_event 연결 규약

세 시스템 사이에 공통 키가 **하나도 없다**. 최소 개입으로 연결하려면:

1. clip 레코드에 bridge 좌표를 실어 보낸다 (labeling 쪽 필드 추가):
   - `rayban_visit_session_id` [PROPOSED] ← `visit_sessions.id`
   - `rayban_source_event_id` [PROPOSED] ← `capture_events.source_event_id` (= `events.id`, 미디어 단위)
   - `rayban_capture_event_id` [PROPOSED] ← `capture_events.id` (해당 clip이 특정 후보에서 파생된 경우에만)
   - `rayban_encounter_id`, `rayban_subject_person_id` [PROPOSED] — PHI-safe 워크플로 상
     라벨러 UI에는 노출하지 않고 export 단계에서만 채운다.
2. 시간 좌표 변환: `start_ms = round(evidence_spans[i].start_sec * 1000)`,
   `end_ms = round(end_sec * 1000)`. `end_sec ∈ {null, ""}`(열린 구간)이면 `end_ms = NULL`.
   구간 없는 clip 레벨 라벨은 `start_ms = NULL, end_ms = NULL`.
3. 라벨 승격 대상은 `rehab_labels`가 **아니라** `capture_events`:
   `source_type = 'human_label'` [PROPOSED], `candidate_type = clip_type`,
   `status = 'edited'`(사람이 매김) 또는 `'approved'`(리뷰 통과),
   `payload_json` = clip 필드 원본 + `{"label_schema":"pediatric_home_v1","schema_version":"rehab-v1.0","clip_id":…}`.
   멱등 키: `payload.extraction_key = sha256(f"{rayban_source_event_id}:human_label:{clip_id}:{annotator}")` [PROPOSED].
4. `rehab_labels`는 **세션 요약 1행**으로만 계속 쓴다 (PK 제약 때문). 구간별 라벨을
   여기에 넣으려 하지 말 것.

### C.3 출처 태그 ↔ 저장 위치

design doc §1의 6종 태그를 각 라벨 클래스에 고정한다.

| 출처 태그 | 어떤 라벨 클래스가 이 태그를 다는가 | labeling 저장 | rayban_pt 저장 | physio_app 저장 |
|---|---|---|---|---|
| `보호자 보고` | Zone A ①②, Zone B 흡인 횟수·경련·투약·ER 이용, `home_program_adherence` | — [PROPOSED] `source_tag` | `capture_events.source_type='caregiver_report'` [PROPOSED] | `caregiverReport.*`, observation `source_type:'manual'` (구분 불가 → `measurement_context.source_tag` [PROPOSED]) |
| `간호사 관찰` | Zone B 기기 부위 상태, 안정성, 분비물 | `annotator` + `users.expert_type` | `capture_events.source_type='clinician_observation'` [PROPOSED], `provider_role='caregiver'`가 아님에 주의 | `safetySnapshot.*` |
| `치료사 측정` | Zone C 보조수준·유지시간·내성·품질 0–4 | `annotator` + `label_confidence` | `rehab_labels.provider_role='physical_therapist'` + `reviewer_person_id` | `functionalTasks[]` |
| `기기 측정` | SpO2/HR baseline, vent 설정, pose 각도 | — | `capture_events.source_type='pose'` (기존) / `'device'` [PROPOSED]; `payload.derived_from='video_pose'` | `respiratorySupport.*`, `safetySnapshot.{spo2Baseline,hrBaseline}` |
| `AI 초안` | 전사 추출 후보 전부, LLM 노트 초안 | `ai_draft_fields[]`, `ai_draft_note`, `ai_draft_at` (기존 필드) | `capture_events.source_type='transcript'`, `payload.{derived_from:'transcript', review_required:'true', extractor_version}`; `status='draft'` | `sidecar.inferredLabels.*.source = 'ai_suggested'` |
| `의료진 확인 완료` | 위 어느 것이든 사람이 승인한 순간 | `label_reviews.{reviewer, reviewer_email, review_status, reviewed_at}` | `capture_events.{status:'approved', reviewed_by, reviewed_at}`; `rehab_labels.review_status='approved'` | `sidecar.*.source = 'clinician_confirmed'` 또는 `'clinician_entered'` |

**불변 규칙**

- 초안은 `status='draft'` + `payload.review_required='true'`로만 생성된다
  (`transcript_capture.py:942`, `bridge_core.py`의 pose 경로도 동일). 사람 승인 없이
  `approved`로 전이하는 경로를 만들지 않는다.
- physio_app observation은 `createStringObservation`/`createBooleanObservation`이
  `source_type: 'manual'`을 **하드코딩**하므로, 6종 태그는 현재 `measurement_context`에
  실어야 한다: `measurement_context.source_tag` [PROPOSED],
  `measurement_context.capture_event_id` [PROPOSED].
- `PEDIATRIC_COMPLEX_HOME_SYNC_CONTEXT = { profileKey:'pediatric_complex_home',
  syncSource:'soap_structured_section' }`가 이미 모든 observation에 붙으므로 확장 지점은 여기다.

---

## D. Gold-note eval linkage

세션당 gold note 1개(치료사 직접 작성) ↔ LLM 초안 1개를 짝지어 **누락률**을 채점한다.
파일 1개 = 1 세션. 위치: `~/projects/home-rehab-labeling/review_apps/exports/gold_notes/<visit_session_id>.json` [PROPOSED].

```json
{
  "record_type": "pediatric_gold_note_pair/v1",
  "pair_id": "gnp_<uuid4>",
  "identity": {
    "rayban_visit_session_id": "<visit_sessions.id>",
    "rayban_encounter_id": "<visit_sessions.encounter_id>",
    "rayban_source_event_ids": ["<events.id>", "..."],
    "subject_person_id": "<pseudonymized>",
    "labeling_patient_uid": "patient_...",
    "labeling_visit_id": "YYYY-MM-DD",
    "clip_ids": ["PB3_0001", "..."]
  },
  "gold": {
    "author_person_id": "",
    "author_role": "physical_therapist | nurse",
    "authored_at": "ISO8601",
    "zone_a": [
      {"item_key": "caregiver_top_concern",       "text": "", "source_tag": "보호자 보고"},
      {"item_key": "baseline_delta",              "text": "", "source_tag": "간호사 관찰"},
      {"item_key": "safety_and_red_flag",         "text": "", "source_tag": "의료진 확인 완료"},
      {"item_key": "device_and_posture_change",   "text": "", "source_tag": "치료사 측정"},
      {"item_key": "who_checks_what_when",        "text": "", "source_tag": "의료진 확인 완료"}
    ],
    "zone_b": { "_schema": "pediatric-complex-home-note.schema.ts@nursing_subset", "safetySnapshot": {}, "respiratorySupport": {}, "feedingAndMedicalDevices": {}, "caregiverReport": {}, "caregiverEducation": {} },
    "zone_c": { "_schema": "pediatric-complex-home-note.schema.ts@rehab_subset", "postureAndMobility": {}, "functionalTasks": [], "equipmentAndOrthosis": {}, "carePlanAndCoordination": {}, "nextVisitFocus": {} }
  },
  "draft": {
    "generator": "rayban_bridge | physio_app",
    "model": "",
    "prompt_version": "",
    "generated_at": "ISO8601",
    "input_refs": {
      "capture_event_ids": [],
      "clip_ids": [],
      "transcript_extractor_version": "rayban_transcript_rules_v4",
      "semantic_extractor_version": "rayban_capture_semantics_v4"
    },
    "zone_a": [],
    "zone_b": {},
    "zone_c": {}
  },
  "scoring": {
    "rubric_version": "pediatric_omission_v1",
    "scored_by": "",
    "scored_at": "ISO8601",
    "items": [
      {
        "zone": "A",
        "item_key": "safety_and_red_flag",
        "gold_present": true,
        "draft_present": false,
        "verdict": "omission",
        "severity": "critical",
        "note": ""
      }
    ],
    "totals": {
      "gold_item_count": {"A": 5, "B": 0, "C": 0},
      "omission_count": {"A": 0, "B": 0, "C": 0},
      "omission_rate": {"A": 0.0, "B": 0.0, "C": 0.0, "overall": 0.0},
      "critical_omission_count": 0,
      "hallucination_count": 0,
      "contradiction_count": 0
    }
  }
}
```

**규칙**

- `zone_a[].item_key`는 5개 고정: `caregiver_top_concern`, `baseline_delta`,
  `safety_and_red_flag`, `device_and_posture_change`, `who_checks_what_when`
  (design doc §1 Zone A 1–5 순서).
- `zone_b` / `zone_c`의 항목 단위는 `pediatric-complex-home-note.schema.ts`의
  **leaf 필드 경로**(예: `safetySnapshot.escalationNeeded`)를 `item_key`로 쓴다.
  스키마에 없는 신설 항목(흡인/경련/투약/r-FLACC/teach-back)은
  `item_key`에 `[PROPOSED]` 경로를 그대로 쓰고 `"proposed": true`를 덧붙인다.
- `verdict` enum: `match` | `partial` | `omission` | `hallucination` | `contradiction`.
- `severity` enum: `critical` | `major` | `minor`. red flag·응급계획·기기 변경은
  누락 시 무조건 `critical`.
- `omission_rate[zone] = omission_count[zone] / gold_item_count[zone]`.
  gold에 없는데 draft에만 있으면 `hallucination`이며 분모에 넣지 않는다.
- 채점 단위는 **gold 기준**이다. draft가 더 장황해도 누락률은 영향받지 않는다.
- 같은 세션의 few-shot pool 사용 시 `draft.prompt_version`에 pool 버전을 기록해
  train/eval 누수를 추적한다.

---

## E. Out-of-scope for v1

1. **pose 각도(`MET.ROM.*`)의 기록 주지표 승격.** goniometer 대조 검증 전까지
   `review_required` 유지, Zone C 보조자료로만 표시 (design doc §1).
2. **GMFCS를 방문별 변화 지표로 사용.** 배경 분류 표시만.
3. **사람 없는 자동 승인.** `capture_events.status`가 `draft`→`approved`로
   자동 전이하는 경로를 만들지 않는다.
4. **labeling ↔ bridge 실시간 양방향 동기화.** v1은 배치 export 단방향
   (labeling → bridge → physio).
5. **physio_app→labeling 역방향 enum 변환.** §B의 crosswalk은 정방향만 정의.
6. **PHI 원본 미디어의 physio_app 이동.** `media_uri`는 labeling 쪽에 머문다.
7. **이중 평가자 신뢰도(κ/ICC) 자동 계산 파이프라인.** `label_history`
   테이블은 존재하나 v1에서 지표를 산출하지 않는다.
8. **정기 측정(GMFM, SATCo, CPCHILD, GAS) 스케줄러/주기 관리.**
   측정 주기 계층은 design doc에 기술되어 있으나 스키마 매핑 대상 아님.
9. **.hwp 원본 차트 서식 ↔ `assessment_form_templates` 필드 매핑.**
   Drive(yonsei 계정) 접근 미해결 (design doc §5).
10. **`video_quality` / `occlusion` / `assessable`의 노트 표출.**
    데이터 품질 게이트 전용, 임상 노트에 표시하지 않는다.
11. **`_fatigue_level()` / `_tolerance()` 등 전사 추출기 코드 수정.**
    §B.8 불일치는 기록만 하고 별도 이슈로 처리.
12. **다국어(영문) 노트 출력.** 한국어 임상 용어 원형 유지.
