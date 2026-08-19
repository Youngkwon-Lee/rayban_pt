#!/usr/bin/env python3
"""Smoke test for cross-provider semantic coverage.

The extractor remains conservative and review-first, but every supported
provider role must preserve its identity/domain while extracting explicit
assessment, instruction, intervention, or assistance language.
"""

from __future__ import annotations

from lib.transcript_capture import extract_capture_semantics, extract_transcript_capture_candidates


CASES = (
    (
        "physical_therapist",
        "보행 평가를 시작합니다. 오른쪽 무릎 굴곡은 90도입니다. 관절 가동 중재를 합니다.",
        "physical_rehabilitation",
        {"rom_measurement", "intervention_started"},
    ),
    (
        "occupational_therapist",
        "일상생활동작 평가를 합니다. 손 기능 과제와 보조 도구 사용 훈련을 진행합니다.",
        "occupational_function",
        {"assessment_started", "exercise_instruction"},
    ),
    (
        "pilates_instructor",
        "필라테스 리포머 풋워크 중립 척추 중재를 하고 3세트 8회 반복합니다.",
        "pilates_movement",
        {"intervention_started", "exercise_instruction"},
    ),
    (
        "personal_trainer",
        "오버헤드 스쿼트 동작 평가 후 근력 운동을 3세트 10회 진행하고 점진적 과부하를 적용합니다.",
        "fitness_performance",
        {"functional_task", "exercise_instruction"},
    ),
    (
        "caregiver",
        "보호자 교육을 합니다. 휠체어 이동 훈련과 안전 확인을 설명합니다.",
        "care_and_assistance",
        {"caregiver_education", "safety_check"},
    ),
    (
        "other",
        "안전 확인 후 운동을 5회 반복하고 피로 반응을 기록합니다.",
        "general_session",
        {"safety_check", "exercise_instruction"},
    ),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    for role, text, expected_domain, expected_types in CASES:
        events = extract_transcript_capture_candidates(text, provider_role=role)
        types = {event["candidate_type"] for event in events}
        require(events, f"no candidates extracted for {role}")
        require(
            expected_types <= types,
            f"{role} missing candidates: expected={expected_types} actual={types}",
        )
        for event in events:
            semantic = event["payload"]["semantic"]
            require(
                semantic["provider_role"] == role,
                f"provider role lost for {role}: {semantic}",
            )
            require(
                semantic["provider_role_domain"] == expected_domain,
                f"provider domain mismatch for {role}: {semantic}",
            )
            require(
                event["payload"]["review_required"] == "true",
                f"{role} candidate must remain review-gated",
            )

        if role == "occupational_therapist":
            semantics = [event["payload"]["semantic"] for event in events]
            require(
                any(item.get("assessment_name") == "adl_assessment" for item in semantics),
                f"{role} should preserve the explicit ADL assessment name: {semantics}",
            )
            require(
                any(item.get("activity_name") == "fine_motor_task" for item in semantics),
                f"{role} should preserve the explicit fine-motor task: {semantics}",
            )

    custom = extract_capture_semantics(
        "exercise_instruction",
        "몬스터 워크 동작을 가르치고 발목 안정화 중재를 설명합니다.",
        provider_role="physical_therapist",
    )
    require(
        custom["activity_detail"] == "몬스터 워크 동작을 가르치고 발목 안정화 중재를 설명합니다.",
        f"custom movement detail should remain reviewable: {custom}",
    )
    require(
        custom["instruction_text"] == "몬스터 워크 동작을 가르치고 발목 안정화 중재를 설명합니다.",
        f"instruction source should remain reviewable: {custom}",
    )

    six_minute = extract_capture_semantics(
        "assessment_started",
        "6분 보행 검사(6MWT)를 실시합니다.",
        provider_role="physical_therapist",
    )
    require(
        six_minute.get("assessment_name") == "six_minute_walk_test",
        f"6MWT should be normalized: {six_minute}",
    )
    odi = extract_capture_semantics(
        "assessment_started",
        "ODI를 평가합니다.",
        provider_role="physical_therapist",
    )
    require(odi.get("assessment_name") == "odi", f"ODI should be normalized: {odi}")

    nmes = extract_capture_semantics(
        "intervention_started",
        "NMES 전기 자극을 적용합니다.",
        provider_role="physical_therapist",
    )
    require(
        nmes.get("intervention_type") == "electrical_stimulation",
        f"NMES should be normalized: {nmes}",
    )
    pnf = extract_capture_semantics(
        "intervention_started",
        "PNF 고유수용성 신경근 촉진을 적용합니다.",
        provider_role="physical_therapist",
    )
    require(pnf.get("intervention_type") == "pnf", f"PNF should be normalized: {pnf}")
    cadillac = extract_capture_semantics(
        "exercise_instruction",
        "캐딜락에서 동작을 가르칩니다.",
        provider_role="pilates_instructor",
    )
    require(
        cadillac.get("activity_name") == "pilates_cadillac",
        f"Cadillac should be normalized: {cadillac}",
    )
    plyometric = extract_capture_semantics(
        "intervention_started",
        "플라이오메트릭 훈련을 진행합니다.",
        provider_role="personal_trainer",
    )
    require(
        plyometric.get("intervention_type") == "plyometric_training",
        f"plyometric training should be normalized: {plyometric}",
    )

    print("provider_role_semantics_smoke_test: PASS")


if __name__ == "__main__":
    main()
