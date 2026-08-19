"""Conservative transcript-to-capture-event extraction for the Ray-Ban bridge.

This is deliberately a review-first semantic layer.  It does not diagnose,
finalize a clinical finding, or write to a clinical system.  It turns explicit
therapist language into timestamp-free draft evidence so a provider can review
it in the Encounter Room before any promotion.
"""

from __future__ import annotations

import re
from typing import Any


TRANSCRIPT_CAPTURE_EXTRACTOR_VERSION = "rayban_transcript_rules_v4"

CAPTURE_ACTION_TYPES = {
    "observation",
    "assessment",
    "instruction",
    "intervention",
    "reassessment",
    "home_program",
    "safety_check",
}


def capture_action_type(candidate_type: str) -> str:
    """Map evidence vocabulary to the provider-facing session action."""

    if candidate_type == "safety_check":
        return "safety_check"
    if candidate_type in {
        "assessment_started",
        "assessment_finding",
        "rom_measurement",
        "positioning_alignment",
    }:
        return "assessment"
    if candidate_type == "reassessment_outcome":
        return "reassessment"
    if candidate_type in {
        "intervention_started",
        "movement_correction",
        "orthosis_assistive_device",
    }:
        return "intervention"
    if candidate_type in {"exercise_instruction", "caregiver_education"}:
        return "instruction"
    if candidate_type == "home_program":
        return "home_program"
    return "observation"


SEMANTIC_EXTRACTOR_VERSION = "rayban_capture_semantics_v4"

PROVIDER_ROLE_DOMAINS = {
    "physical_therapist": "physical_rehabilitation",
    "occupational_therapist": "occupational_function",
    "pilates_instructor": "pilates_movement",
    "personal_trainer": "fitness_performance",
    "caregiver": "care_and_assistance",
    "other": "general_session",
    "unspecified": "general_session",
}


def _first_match(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return pattern
    return ""


def _number_before(text: str, patterns: tuple[str, ...]) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def _core_task(text: str) -> str:
    patterns = (
        ("sit_to_stand", (r"앉았다\s*일어나", r"sit\s*[- ]?to\s*stand", r"chair\s*rise")),
        ("timed_up_and_go", (r"일어나\s*걷기\s*검사", r"timed\s*up\s*and\s*go", r"\btug\b")),
        ("adl_training", (r"일상\s*생활\s*동작", r"일상생활동작", r"자조\s*활동", r"\badl\b", r"\biadl\b", r"activities\s+of\s+daily\s+living")),
        ("transfer_training", (r"이동\s*훈련", r"침대\s*(?:에서|↔)\s*(?:휠체어|의자)", r"transfer\s*(training|practice)", r"bed\s*mobility")),
        ("fine_motor_control", (r"소근육", r"손\s*기능", r"미세\s*동작", r"fine\s*motor", r"hand\s*function")),
        ("sensory_integration", (r"감각\s*(통합|처리)", r"sensory\s*(integration|processing)")),
        ("cognitive_task", (r"인지\s*(훈련|평가|과제)", r"주의\s*(집중|력)", r"기억\s*훈련", r"cognitive", r"attention", r"memory")),
        ("gait_practice", (r"보행", r"걷기", r"gait", r"walking")),
        ("standing_balance", (r"서서.*균형", r"standing\s+balance")),
        ("sitting_balance", (r"앉은.*균형", r"sitting\s+balance")),
        ("balance_test", (r"균형\s*(검사|평가|테스트)", r"berg\s*balance", r"single\s*leg\s*stance", r"tandem\s*stance")),
        ("reaching", (r"팔을?\s*뻗", r"reach(?:ing)?")),
        ("strength_test", (r"근력\s*(검사|평가)", r"\bmmt\b", r"1\s*rm", r"one\s*rep\s*max")),
        ("movement_screen", (r"동작\s*(평가|검사)", r"움직임\s*(평가|검사)", r"overhead\s*squat", r"movement\s*screen", r"\bfms\b")),
        ("breathing_control", (r"호흡\s*(평가|훈련|연습|조절)", r"복식\s*호흡", r"breathing\s*(assessment|training|control)")),
        ("strength_training", (r"근력\s*(운동|훈련)", r"저항\s*운동", r"resistance\s*training", r"strength\s*training")),
        ("motor_control", (r"운동\s*조절", r"신경근", r"motor\s*control", r"neuromuscular")),
        ("conditioning", (r"컨디셔닝", r"심폐", r"유산소", r"conditioning", r"cardio", r"aerobic")),
        ("vestibular_rehabilitation", (r"전정s*(재활|훈련|운동)", r"vestibular\s*(rehab|rehabilitation|training)")),
        ("neurodevelopmental_training", (r"신경발달", r"신경s*발달", r"neurodevelopmental", r"bobath", r"n.?d.?t.?")),
        ("sport_specific_training", (r"스포츠s*(훈련|동작)", r"종목s*특화", r"sport[- ]specific")),
        ("pilates_control", (r"필라테스", r"pilates", r"리포머", r"reformer")),
        ("range_of_motion", (r"가동\s*범위", r"관절\s*가동", r"\brom\b", r"range\s+of\s+motion", r"굴곡", r"신전", r"flexion", r"extension")),
        ("positioning", (r"자세", r"정렬", r"position", r"alignment")),
    )
    for value, candidates in patterns:
        if _first_match(text, candidates):
            return value
    return ""


def _activity_name(text: str) -> str:
    """Return a concrete movement name only when the therapist says it explicitly."""

    patterns = (
        ("sit_to_stand", (r"앉았다\s*일어나", r"sit\s*[- ]?to\s*stand", r"chair\s*rise")),
        ("bridge", (r"브릿지", r"bridge")),
        ("dead_bug", (r"데드\s*버그", r"dead\s*bug")),
        ("bird_dog", (r"버드\s*독", r"bird\s*dog")),
        ("side_plank", (r"사이드\s*플랭크", r"side\s*plank")),
        ("plank", (r"플랭크", r"plank")),
        ("push_up", (r"푸시\s*업", r"팔굽혀펴기", r"push\s*up")),
        ("deadlift", (r"데드리프트", r"deadlift")),
        ("row", (r"로우", r"row")),
        ("overhead_press", (r"오버헤드\s*프레스", r"overhead\s*press")),
        ("squat", (r"스쿼트", r"squat")),
        ("lunge", (r"런지", r"lunge")),
        ("step_up", (r"스텝\s*업", r"계단\s*오르", r"step\s*up")),
        ("clamshell", (r"클램셸", r"clamshell", r"조개\s*운동")),
        ("heel_raise", (r"까치발", r"발뒤꿈치\s*들", r"heel\s*raise", r"calf\s*raise")),
        ("shoulder_flexion", (r"팔을?\s*앞으로\s*올", r"shoulder\s*flexion")),
        ("pelvic_tilt", (r"골반\s*기울", r"pelvic\s*tilt")),
        ("cat_cow", (r"캣\s*카우", r"고양이\s*소", r"cat\s*[- ]?cow")),
        ("functional_reach", (r"기능적\s*팔\s*뻗기", r"functional\s*reach")),
        ("step_test", (r"스텝\s*테스트", r"step\s*test")),
        ("timed_up_and_go", (r"일어나\s*걷기\s*검사", r"timed\s*up\s*and\s*go", r"\btug\b")),
        ("wheelchair_transfer", (r"휠체어\s*(?:이동|옮기|트랜스퍼)", r"wheelchair\s*transfer")),
        ("bed_mobility", (r"침대\s*(?:이동|돌기|뒤집기)", r"bed\s*mobility", r"rolling\s*in\s*bed")),
        ("fine_motor_task", (r"손\s*기능\s*과제", r"집기", r"단추", r"젓가락", r"fine\s*motor\s*task", r"pinch")),
        ("farmer_carry", (r"파머스\s*캐리", r"farmer[' ]?s\s*carry")),
        ("hip_hinge", (r"힙\s*힌지", r"hip\s*hinge")),
        ("calf_raise", (r"종아리\s*올리기", r"calf\s*raise")),
        ("mobility_drill", (r"가동성\s*운동", r"모빌리티", r"mobility\s*drill")),
        ("roll_down", (r"롤\s*다운", r"roll\s*down")),
        ("hundred", (r"필라테스\s*백", r"더\s*헌드레드", r"\bthe\s*hundred\b")),
        ("teaser", (r"티저", r"teaser")),
        ("reformer_footwork", (r"리포머\s*풋워크", r"reformer\s*footwork")),
        ("pilates_cadillac", (r"캐딜락", r"cadillac")),
        ("pilates_chair", (r"필라테스\s*체어", r"운다\s*체어", r"wunda\s*chair", r"pilates\s*chair")),
        ("pilates_barrel", (r"필라테스\s*배럴", r"스파인\s*코렉터", r"barrel", r"spine\s*corrector")),
        ("roll_up", (r"롤\s*업", r"roll\s*up")),
        ("leg_circle", (r"레그\s*서클", r"leg\s*circle")),
        ("kettlebell_swing", (r"케틀벨\s*스윙", r"kettlebell\s*swing")),
        ("box_jump", (r"박스\s*점프", r"box\s*jump")),
        ("plyometric_drill", (r"플라이오메트릭", r"plyometric")),
        ("sprint", (r"스프린트", r"전력\s*질주", r"\bsprint\b")),
        ("agility_drill", (r"민첩성\s*(훈련|드릴)", r"agility\s*drill", r"ladder\s*drill")),
        ("single_leg_stance", (r"한\s*발\s*서기", r"single\s*leg\s*stance")),
        ("tandem_stance", (r"일자\s*서기", r"tandem\s*stance")),
        ("gait_training", (r"보행\s*(연습|훈련)", r"gait\s*(practice|training)")),
        ("balance_training", (r"균형\s*(훈련|연습)", r"balance\s*(training|practice)")),
        ("breathing", (r"복식\s*호흡", r"호흡\s*(훈련|연습)", r"breathing")),
    )
    for value, candidates in patterns:
        if _first_match(text, candidates):
            return value
    return ""


def _assessment_name(text: str) -> str:
    patterns = (
        ("six_minute_walk_test", (r"6\s*분\s*(걷기|보행)\s*(검사|테스트)?", r"6\s*minute\s*walk\s*test", r"\b6mwt\b")),
        ("ten_meter_walk_test", (r"10\s*미터\s*(걷기|보행)\s*(검사|테스트)?", r"10\s*meter\s*walk\s*test", r"\b10mwt\b")),
        ("five_times_sit_to_stand", (r"5\s*회\s*앉았다\s*일어나", r"five\s*times\s*sit\s*to\s*stand", r"\b5xsts\b")),
        ("thirty_second_chair_stand", (r"30\s*초\s*(의자|chair)\s*(일어나|stand)", r"30\s*second\s*chair\s*stand")),
        ("berg_balance_scale", (r"버그\s*균형", r"berg\s*balance\s*scale")),
        ("functional_gait_assessment", (r"기능적\s*보행\s*평가", r"functional\s*gait\s*assessment", r"\bfga\b")),
        ("mini_bestest", (r"mini\s*bestest", r"미니\s*베스트\s*테스트")),
        ("y_balance_test", (r"y\s*균형\s*검사", r"y[- ]?balance\s*test")),
        ("gmfm", (r"gmfm", r"대동작\s*기능\s*평가", r"gross\s*motor\s*function\s*measure")),
        ("pedi", (r"pedi", r"소아\s*장애\s*평가")),
        ("fim", (r"\bfim\b", r"기능적\s*독립\s*측정")),
        ("barthel_index", (r"barthel", r"바델\s*지수")),
        ("quickdash", (r"quick\s*dash", r"quickdash")),
        ("dash", (r"\bdash\b", r"상지\s*기능\s*장애\s*평가")),
        ("odi", (r"(?<![A-Za-z0-9])odi(?![A-Za-z0-9])", r"odi(?=\s|를|가|는|을|에|로|평가)", r"오스웨스트리", r"요통\s*장애\s*지수")),
        ("ndi", (r"(?<![A-Za-z0-9])ndi(?![A-Za-z0-9])", r"ndi(?=\s|를|가|는|을|에|로|평가)", r"목\s*장애\s*지수")),
        ("lefs", (r"(?<![A-Za-z0-9])lefs(?![A-Za-z0-9])", r"lefs(?=\s|를|가|는|을|에|로|평가)", r"하지\s*기능\s*척도")),
        ("psfs", (r"(?<![A-Za-z0-9])psfs(?![A-Za-z0-9])", r"psfs(?=\s|를|가|는|을|에|로|평가)", r"환자\s*특이적\s*기능\s*척도")),
        ("dhi", (r"(?<![A-Za-z0-9])dhi(?![A-Za-z0-9])", r"dhi(?=\s|를|가|는|을|에|로|평가)", r"어지럼\s*장애\s*척도")),
        ("nine_hole_peg_test", (r"9\s*홀\s*페그", r"nine\s*hole\s*peg\s*test")),
        ("box_and_block_test", (r"박스\s*앤드\s*블록", r"box\s*and\s*block\s*test")),
        ("adl_assessment", (r"일상\s*생활\s*동작\s*(평가|검사)", r"자조\s*활동\s*(평가|검사)", r"\badl\s*(assessment|evaluation)\b", r"activities\s+of\s+daily\s+living")),
        ("timed_up_and_go_assessment", (r"일어나\s*걷기\s*검사", r"timed\s*up\s*and\s*go", r"\btug\b")),
        ("functional_reach_assessment", (r"기능적\s*팔\s*뻗기\s*(평가|검사)?", r"functional\s*reach\s*(assessment|test)?")),
        ("fine_motor_assessment", (r"소근육\s*(평가|검사)", r"손\s*기능\s*(평가|검사)", r"fine\s*motor\s*(assessment|test)", r"hand\s*function\s*(assessment|test)")),
        ("sensory_assessment", (r"감각\s*(평가|검사|처리)", r"sensory\s*(assessment|profile|processing)")),
        ("cognitive_assessment", (r"인지\s*(평가|검사)", r"주의\s*집중\s*(평가|검사)", r"cognitive\s*(assessment|screen)", r"attention\s*(assessment|test)")),
        ("transfer_assessment", (r"이동\s*(평가|검사)", r"트랜스퍼\s*(평가|검사)", r"transfer\s*(assessment|test)")),
        ("conditioning_assessment", (r"컨디셔닝\s*(평가|검사)", r"심폐\s*(평가|검사)", r"conditioning\s*(assessment|test)", r"cardio\s*(assessment|test)")),
        ("body_composition_assessment", (r"체성분\s*(평가|검사|분석)", r"body\s*composition\s*(assessment|analysis)")),
        ("pain_assessment", (r"통증\s*(평가|검사|점수)?", r"vas", r"nrs", r"pain\s*(score|assessment)")),
        ("range_of_motion", (r"가동\s*범위", r"관절\s*가동", r"\brom\b", r"range\s+of\s+motion")),
        ("manual_muscle_test", (r"근력\s*검사", r"도수\s*근력", r"\bmmt\b", r"manual\s*muscle")),
        ("gait_assessment", (r"보행\s*(평가|검사)", r"gait\s*(assessment|evaluation)")),
        ("balance_assessment", (r"균형\s*(평가|검사|테스트)", r"berg\s*balance", r"single\s*leg\s*stance", r"balance\s*(assessment|test)")),
        ("movement_screen", (r"동작\s*(평가|검사)", r"움직임\s*(평가|검사)", r"overhead\s*squat", r"movement\s*screen", r"\bfms\b")),
        ("posture_assessment", (r"자세\s*(평가|검사)", r"posture\s*(assessment|screen)")),
        ("breathing_assessment", (r"호흡\s*(평가|검사)", r"breathing\s*(assessment|pattern)")),
        ("strength_assessment", (r"근력\s*(평가|검사)", r"1\s*rm", r"one\s*rep\s*max", r"strength\s*(assessment|test)")),
        ("endurance_assessment", (r"지구력\s*(평가|검사)", r"endurance\s*(assessment|test)", r"plank\s*test")),
        ("special_test", (r"특수\s*검사", r"special\s*test")),
    )
    for value, candidates in patterns:
        if _first_match(text, candidates):
            return value
    return ""


def _body_position(text: str) -> str:
    patterns = (
        ("supine", (r"바로\s*누", r"supine", r"lying\s+on\s+your\s+back")),
        ("prone", (r"엎드", r"prone", r"lying\s+on\s+your\s+stomach")),
        ("side_lying", (r"옆으로\s*누", r"side\s*[- ]?lying")),
        ("quadruped", (r"네발", r"quadruped", r"all\s+fours")),
        ("kneeling", (r"무릎\s*꿇", r"kneeling")),
        ("sitting", (r"앉", r"sitting")),
        ("standing", (r"서서", r"standing")),
        ("walking", (r"걷", r"walking", r"gait")),
    )
    for value, candidates in patterns:
        if _first_match(text, candidates):
            return value
    return ""


def _assist_level(text: str) -> str:
    patterns = (
        ("dependent", (r"전적\s*도움", r"dependent")),
        ("maximal_assist", (r"최대\s*도움", r"max(?:imal)?\s*assist")),
        ("moderate_assist", (r"중등도\s*도움", r"moderate\s*assist")),
        ("minimal_assist", (r"최소\s*도움", r"minimal\s*assist")),
        ("contact_guard", (r"접촉\s*보조", r"contact\s*guard")),
        ("standby_assist", (r"대기\s*보조", r"standby\s*assist")),
        ("supervision", (r"감독", r"supervision")),
        ("independent", (r"혼자", r"독립", r"independent")),
    )
    for value, candidates in patterns:
        if _first_match(text, candidates):
            return value
    return ""


def _performance_level(text: str) -> str:
    patterns = (
        ("improved", (r"개선", r"향상", r"좋아", r"improv", r"better")),
        ("declined", (r"악화", r"나빠", r"worse", r"declin")),
        ("unable", (r"못\s*했", r"실패", r"불가", r"unable", r"failed")),
        ("stable", (r"유지", r"동일", r"변화\s*없", r"stable", r"unchanged")),
        ("variable", (r"들쭉날쭉", r"일관되\s*지\s*않", r"variable")),
    )
    for value, candidates in patterns:
        if _first_match(text, candidates):
            return value
    return ""


def _tolerance(text: str) -> str:
    if _first_match(text, (r"통증", r"아파", r"저림", r"힘들", r"견디기\s*어렵", r"pain", r"numb", r"difficult")):
        return "poor"
    if _first_match(text, (r"잘\s*견뎠", r"괜찮", r"문제\s*없", r"tolerated", r"no\s+problem")):
        return "good"
    if _first_match(text, (r"보통", r"참을\s*만", r"fair", r"moderate\s*tolerance")):
        return "fair"
    return ""


def _fatigue_level(text: str) -> str:
    if _first_match(text, (r"심한\s*피로", r"매우\s*피곤", r"high\s*fatigue", r"very\s*tired")):
        return "high"
    if _first_match(text, (r"중등도\s*피로", r"moderate\s*fatigue")):
        return "moderate"
    if _first_match(text, (r"약간\s*(피로|피곤)", r"경도\s*피로", r"mild\s*fatigue")):
        return "mild"
    if _first_match(text, (r"피로", r"피곤", r"fatigue", r"tired")):
        return "present"
    return ""


def _compensations(text: str) -> list[str]:
    patterns = (
        ("right_weight_shift", (r"오른쪽.*(체중|무게|중심)", r"우측.*(체중|무게|중심)", r"right.*weight\s*shift")),
        ("left_weight_shift", (r"왼쪽.*(체중|무게|중심)", r"좌측.*(체중|무게|중심)", r"left.*weight\s*shift")),
        ("trunk_lateral_flexion", (r"몸통.*(옆|측방)", r"trunk.*lateral", r"lateral\s*flexion")),
        ("excessive_extension", (r"과도한\s*신전", r"과신전", r"excessive\s*extension")),
        ("excessive_flexion", (r"과도한\s*굴곡", r"과굴곡", r"excessive\s*flexion")),
        ("shoulder_elevation", (r"어깨.*(올라|거상)", r"shoulder\s*elevation")),
        ("pelvic_rotation", (r"골반.*(회전|돌아)", r"pelvic\s*rotation")),
        ("caregiver_overassist", (r"보호자.*(과도|너무).*(도움|보조)", r"caregiver.*over\s*assist")),
        ("knee_valgus", (r"무릎.*(안쪽|모임|valgus)", r"knee\s*valgus")),
        ("pelvic_drop", (r"골반.*(떨어|하강|drop)", r"pelvic\s*drop")),
        ("forward_trunk_lean", (r"몸통.*(앞으로|전방).*(기울|숙)", r"forward\s*trunk\s*lean")),
        ("lumbar_extension", (r"허리.*(꺾|과신전)", r"lumbar\s*extension")),
        ("breath_holding", (r"숨을\s*참", r"호흡.*(멈|참)", r"breath\s*holding")),
        ("scapular_winging", (r"날개뼈.*(뜸|들)", r"scapular\s*winging")),
        ("foot_pronation", (r"발.*(과도한.*회내|안쪽으로).*(무너|붕괴)", r"foot\s*pronation")),
        ("cervical_extension", (r"목.*(과신전|뒤로 젖)", r"cervical\s*extension")),
    )
    return [value for value, candidates in patterns if _first_match(text, candidates)]


def _safety_flags(text: str) -> list[str]:
    patterns = (
        ("fall_risk", (r"낙상", r"넘어질", r"fall\s*risk")),
        ("pain", (r"통증", r"아파", r"pain")),
        ("fatigue", (r"피로", r"피곤", r"fatigue")),
        ("poor_tolerance", (r"견디기\s*어렵", r"내약성.*저하", r"poor\s*tolerance")),
        ("unsafe_environment", (r"미끄럽", r"장애물", r"unsafe\s*environment")),
        ("respiratory_concern", (r"숨이\s*차", r"호흡곤란", r"respiratory")),
        ("dizziness", (r"어지러", r"현기증", r"dizz")),
        ("chest_pain", (r"가슴.*통증", r"흉통", r"chest\s*pain")),
        ("shortness_of_breath", (r"호흡\s*곤란", r"숨이\s*차", r"shortness\s*of\s*breath")),
    )
    return [value for value, candidates in patterns if _first_match(text, candidates)]


def _assessment_type(candidate_type: str) -> str:
    return {
        "assessment_started": "general_assessment",
        "assessment_finding": "clinical_finding",
        "rom_measurement": "range_of_motion",
        "positioning_alignment": "positioning_alignment",
        "functional_task": "functional_task",
    }.get(candidate_type, "")


def _intervention_type(candidate_type: str, text: str) -> str:
    if candidate_type == "movement_correction":
        return "movement_correction"
    if candidate_type == "orthosis_assistive_device":
        return "orthosis_assistive_device"
    if candidate_type != "intervention_started":
        return ""
    patterns = (
        ("joint_mobilization", (r"관절\s*가동", r"mobiliz")),
        ("manual_therapy", (r"도수", r"manual")),
        ("massage", (r"마사지", r"massage")),
        ("soft_tissue_mobilization", (r"연부\s*조직", r"soft\s*tissue")),
        ("stretching", (r"스트레칭", r"stretch")),
        ("relaxation", (r"이완", r"release")),
        ("neuromuscular_reeducation", (r"신경근\s*재교육", r"neuromuscular\s*re[- ]?education")),
        ("therapeutic_exercise", (r"치료적\s*운동", r"therapeutic\s*exercise")),
        ("breathing_training", (r"호흡\s*(훈련|연습|중재)", r"breathing\s*(training|exercise)")),
        ("pnf", (r"pnf", r"고유수용성\s*신경근\s*촉진")),
        ("pilates_mat", (r"매트\s*필라테스", r"pilates\s*mat")),
        ("pilates_reformer", (r"리포머", r"reformer")),
        ("resistance_training", (r"저항\s*(운동|훈련)", r"resistance\s*training")),
        ("gait_training", (r"보행\s*(훈련|연습)", r"gait\s*(training|practice)")),
        ("balance_training", (r"균형\s*(훈련|연습)", r"balance\s*(training|practice)")),
        ("taping", (r"테이핑", r"taping")),
        ("cueing", (r"큐잉", r"촉진", r"cueing", r"facilitat")),
        ("adl_training", (r"일상\s*생활\s*동작\s*훈련", r"자조\s*활동\s*훈련", r"adl\s*training")),
        ("task_specific_training", (r"과제\s*지향", r"과제\s*특이적", r"task[- ]specific", r"task[- ]oriented")),
        ("fine_motor_training", (r"소근육\s*훈련", r"손\s*기능\s*훈련", r"fine\s*motor\s*training", r"hand\s*therapy")),
        ("sensory_integration", (r"감각\s*통합", r"감각\s*처리", r"sensory\s*integration")),
        ("cognitive_training", (r"인지\s*훈련", r"주의\s*집중\s*훈련", r"cognitive\s*training")),
        ("energy_conservation", (r"에너지\s*보존", r"에너지\s*절약", r"energy\s*conservation")),
        ("adaptive_equipment_training", (r"보조\s*도구\s*사용", r"적응\s*장비", r"adaptive\s*equipment")),
        ("transfer_training", (r"이동\s*훈련", r"트랜스퍼\s*훈련", r"transfer\s*training")),
        ("wheelchair_skills", (r"휠체어\s*(기술|훈련)", r"wheelchair\s*skills")),
        ("strength_training", (r"근력\s*(운동|훈련)", r"strength\s*(training|workout)")),
        ("conditioning", (r"컨디셔닝", r"심폐\s*훈련", r"conditioning", r"cardio")),
        ("electrical_stimulation", (r"전기\s*자극", r"전기\s*치료", r"nmes", r"tens", r"electrical\s*stimulation")),
        ("ultrasound_therapy", (r"초음파\s*(치료|중재)?", r"ultrasound\s*therapy")),
        ("thermotherapy", (r"온열\s*(치료|요법)", r"냉\s*치료", r"cryotherapy", r"thermotherapy")),
        ("traction", (r"견인\s*(치료|요법)?", r"traction")),
        ("vestibular_rehabilitation", (r"전정\s*재활", r"vestibular\s*rehabilitation")),
        ("plyometric_training", (r"플라이오메트릭", r"plyometric\s*training")),
        ("agility_training", (r"민첩성\s*훈련", r"agility\s*training")),
        ("power_training", (r"파워\s*훈련", r"power\s*training")),
        ("hypertrophy_training", (r"근비대", r"hypertrophy")),
        ("interval_training", (r"인터벌\s*훈련", r"interval\s*training")),
        ("sport_specific_training", (r"스포츠\s*특화", r"sport[- ]specific\s*training")),
        ("mobility_training", (r"가동성\s*(운동|훈련)", r"모빌리티", r"mobility\s*training")),
        ("progressive_overload", (r"점진적\s*과부하", r"중량을?\s*(늘|증가)", r"progressive\s*overload")),
        ("warmup", (r"준비\s*운동", r"워밍업", r"warm[- ]?up")),
        ("cooldown", (r"정리\s*운동", r"쿨다운", r"cool[- ]?down")),
    )
    for value, candidates in patterns:
        if _first_match(text, candidates):
            return value
    return "other_intervention"


def _instruction_type(candidate_type: str) -> str:
    return {
        "exercise_instruction": "exercise_instruction",
        "caregiver_education": "caregiver_education",
        "home_program": "home_program",
    }.get(candidate_type, "")


def _equipment(text: str) -> list[str]:
    patterns = (
        ("reformer", (r"리포머", r"reformer")),
        ("mat", (r"매트", r"mat")),
        ("resistance_band", (r"밴드", r"탄력\s*밴드", r"resistance\s*band")),
        ("dumbbell", (r"덤벨", r"아령", r"dumbbell")),
        ("kettlebell", (r"케틀벨", r"kettlebell")),
        ("foam_roller", (r"폼\s*롤러", r"foam\s*roller")),
        ("cane", (r"지팡이", r"cane")),
        ("walker", (r"워커", r"walker")),
        ("parallel_bars", (r"평행봉", r"parallel\s*bars")),
        ("wheelchair", (r"휠체어", r"wheelchair")),
        ("splint", (r"스플린트", r"부목", r"splint")),
        ("theraband", (r"세라밴드", r"theraband")),
        ("cable_machine", (r"케이블\s*머신", r"cable\s*machine")),
        ("pull_up_bar", (r"철봉", r"pull[- ]?up\s*bar")),
        ("stair", (r"계단", r"stair")),
    )
    return [value for value, candidates in patterns if _first_match(text, candidates)]


def _pain_score(text: str) -> float | None:
    match = re.search(r"(?:vas|nrs|통증)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*/\s*(10|100)", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _rpe_score(text: str) -> float | None:
    match = re.search(r"(?:rpe|자각적\s*운동\s*강도|운동\s*강도)\s*[:=]?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _set_count(text: str) -> int | None:
    return _number_before(text, (r"(\d+)\s*(?:세트|sets?)\b",))


def _rest_duration(text: str) -> int | None:
    return _number_before(
        text,
        (r"(?:휴식|rest)\s*(?:은|:)?\s*(\d+)\s*(?:초(?:간|로|동안)?|sec(?:ond)?s?)",),
    )


def _instruction_detail(text: str) -> str:
    patterns = (
        ("breathing_cue", (r"숨을\s*(?:들이|내쉬)", r"호흡을?\s*(?:유지|내쉬|들이)", r"breath(?:e|ing)")),
        ("alignment_cue", (r"정렬", r"중립\s*척추", r"무릎.*발끝", r"alignment", r"neutral\s*spine")),
        ("safety_instruction", (r"안전", r"조심", r"통증.*중지", r"safety", r"stop\s*if")),
        ("dosage_instruction", (r"\d+\s*(?:회|세트|초|reps?|sets?|seconds?)\b", r"tempo", r"휴식", r"rest")),
        ("equipment_setup", (r"리포머", r"밴드", r"덤벨", r"기구", r"reformer", r"equipment")),
    )
    for value, candidates in patterns:
        if _first_match(text, candidates):
            return value
    return ""


def _source_sentence(text: str, limit: int) -> str:
    """Keep an explicit source sentence available for provider review."""

    return " ".join(text.strip().split())[:limit]


def _custom_semantic_details(
    candidate_type: str,
    text: str,
    *,
    activity_name: str,
    assessment_name: str,
) -> dict[str, str]:
    """Preserve explicit but unclassified details without inventing labels."""

    source = _source_sentence(text, 1_000)
    details: dict[str, str] = {}
    assessment_candidates = {
        "assessment_started",
        "assessment_finding",
        "rom_measurement",
        "positioning_alignment",
        "functional_task",
    }
    intervention_candidates = {
        "intervention_started",
        "movement_correction",
        "orthosis_assistive_device",
    }
    instruction_candidates = {"exercise_instruction", "caregiver_education", "home_program"}
    response_candidates = {"response_tolerance", "reassessment_outcome"}

    if (
        candidate_type in assessment_candidates
        and not assessment_name
        and re.search(r"평가|검사|테스트|assessment|evaluation|test|screen", source, re.IGNORECASE)
    ):
        details["assessment_tool_detail"] = source[:500]
    if (
        candidate_type in intervention_candidates | instruction_candidates | {"functional_task"}
        and not activity_name
    ):
        details["activity_detail"] = source[:500]
    if candidate_type in intervention_candidates:
        details["intervention_detail"] = source[:500]
    if candidate_type in instruction_candidates:
        details["instruction_text"] = source
    if candidate_type in response_candidates:
        details["response_note"] = source
    return details


def extract_capture_semantics(
    candidate_type: str,
    text: str,
    *,
    provider_role: str | None = None,
) -> dict[str, Any]:
    """Extract only explicit, reviewable session labels from one sentence."""

    domain_by_candidate = {
        "safety_check": "safety",
        "assessment_started": "assessment",
        "assessment_finding": "assessment",
        "rom_measurement": "assessment",
        "positioning_alignment": "assessment",
        "functional_task": "observation",
        "pose": "observation",
        "video_evidence": "observation",
        "response_tolerance": "response",
        "reassessment_outcome": "reassessment",
        "intervention_started": "intervention",
        "movement_correction": "intervention",
        "orthosis_assistive_device": "intervention",
        "exercise_instruction": "instruction",
        "caregiver_education": "instruction",
        "home_program": "home_program",
    }
    semantics: dict[str, Any] = {
        "version": SEMANTIC_EXTRACTOR_VERSION,
        "domain": domain_by_candidate.get(candidate_type, "observation"),
    }
    clean_provider_role = (provider_role or "").strip()
    if clean_provider_role:
        semantics["provider_role"] = clean_provider_role
        semantics["provider_role_domain"] = PROVIDER_ROLE_DOMAINS.get(
            clean_provider_role,
            "general_session",
        )
    assessment_type = _assessment_type(candidate_type)
    intervention_type = _intervention_type(candidate_type, text)
    instruction_type = _instruction_type(candidate_type)
    if assessment_type:
        semantics["assessment_type"] = assessment_type
    if intervention_type:
        semantics["intervention_type"] = intervention_type
    if instruction_type:
        semantics["instruction_type"] = instruction_type
    values = {
        "activity_name": _activity_name(text),
        "assessment_name": _assessment_name(text),
        "core_task": _core_task(text),
        "body_position": _body_position(text),
        "assist_level": _assist_level(text),
        "performance_level": _performance_level(text),
        "tolerance": _tolerance(text),
        "fatigue_level": _fatigue_level(text),
        "compensations": _compensations(text),
        "safety_flags": _safety_flags(text),
        "equipment": _equipment(text),
        "instruction_detail": _instruction_detail(text),
    }
    semantics.update({key: value for key, value in values.items() if value not in ("", [])})
    semantics.update(
        _custom_semantic_details(
            candidate_type,
            text,
            activity_name=values["activity_name"],
            assessment_name=values["assessment_name"],
        )
    )
    repetition_count = _number_before(
        text,
        (r"(\d+)\s*(?:회|번|reps?|repetitions?)\b",),
    )
    if repetition_count is not None:
        semantics["repetition_count"] = repetition_count
    hold_duration = _number_before(
        text,
        (r"(\d+)\s*(?:초|초간|sec(?:ond)?s?)\b",),
    )
    if hold_duration is not None:
        semantics["hold_duration_seconds"] = hold_duration
    set_count = _set_count(text)
    if set_count is not None:
        semantics["set_count"] = set_count
    rest_duration = _rest_duration(text)
    if rest_duration is not None:
        semantics["rest_duration_seconds"] = rest_duration
    pain_score = _pain_score(text)
    if pain_score is not None:
        semantics["pain_score"] = pain_score
    rpe_score = _rpe_score(text)
    if rpe_score is not None:
        semantics["rpe_score"] = rpe_score
    return semantics


_RULES: tuple[dict[str, Any], ...] = (
    {
        "candidate_type": "pose",
        "patterns": (
            r"자세\s*분석",
            r"pose\s*analysis",
            r"pose\b",
            r"skeleton",
        ),
        "confidence": 0.62,
    },
    {
        "candidate_type": "safety_check",
        "patterns": (
            r"안전",
            r"낙상",
            r"어지러",
            r"fall\s*risk",
            r"safety",
            r"dizziness",
            r"괜찮으세요",
        ),
        "confidence": 0.72,
    },
    {
        "candidate_type": "assessment_started",
        "patterns": (
            r"평가\s*(를|을)?\s*(시작|해볼|하겠습니다|합니다)",
            r"검사\s*(를|을)?\s*(시작|해볼|하겠습니다|합니다)",
            r"평가\s*(를|을)?\s*(진행|실시|시행|완료)",
            r"검사\s*(를|을)?\s*(진행|실시|시행|완료)",
            r"assessment",
            r"let[' ]?s test",
            r"we[' ]?ll assess",
        ),
        "confidence": 0.7,
    },
    {
        "candidate_type": "rom_measurement",
        "patterns": (
            r"가동\s*범위",
            r"관절\s*가동",
            r"range\s*of\s*motion",
            r"\brom\b",
            r"굴곡",
            r"신전",
            r"flexion",
            r"extension",
            r"\d+(?:\.\d+)?\s*(?:도|deg(?:ree)?s?|°)",
        ),
        "confidence": 0.78,
    },
    {
        "candidate_type": "positioning_alignment",
        "patterns": (
            r"자세",
            r"정렬",
            r"alignment",
            r"position",
            r"골반",
            r"pelvis",
            r"무릎",
            r"knee",
            r"어깨",
            r"shoulder",
            r"척추",
            r"spine",
        ),
        "confidence": 0.66,
    },
    {
        "candidate_type": "functional_task",
        "patterns": (
            r"앉았다\s*일어나",
            r"보행",
            r"걷기",
            r"균형",
            r"계단",
            r"reach(?:ing)?",
            r"sit\s*to\s*stand",
            r"gait",
            r"walking",
            r"balance",
            r"squat",
            r"스쿼트",
        ),
        "confidence": 0.74,
    },
    {
        "candidate_type": "assessment_finding",
        "patterns": (
            r"관찰",
            r"소견",
            r"압통",
            r"부종",
            r"근력",
            r"긴장도",
            r"보상",
            r"tenderness",
            r"swelling",
            r"strength",
            r"tone",
            r"compensation",
            r"positive",
            r"negative",
            r"양성",
            r"음성",
        ),
        "confidence": 0.7,
    },
    {
        "candidate_type": "response_tolerance",
        "patterns": (
            r"통증",
            r"아파",
            r"저림",
            r"당김",
            r"피로",
            r"힘들",
            r"견딜",
            r"반응",
            r"pain",
            r"numb",
            r"tingling",
            r"fatigue",
            r"tolerance",
            r"response",
        ),
        "confidence": 0.68,
    },
    {
        "candidate_type": "reassessment_outcome",
        "patterns": (
            r"재평가",
            r"다시\s*(확인|측정|검사)",
            r"전보다",
            r"개선",
            r"변화",
            r"reassess",
            r"re[- ]?test",
            r"improv",
            r"better",
            r"worse",
        ),
        "confidence": 0.72,
    },
    {
        "candidate_type": "intervention_started",
        "patterns": (
            r"도수",
            r"마사지",
            r"관절\s*가동",
            r"스트레칭",
            r"이완",
            r"훈련",
            r"치료",
            r"중재",
            r"manual",
            r"mobiliz",
            r"massage",
            r"stretch",
            r"release",
            r"intervention",
        ),
        "confidence": 0.7,
    },
    {
        "candidate_type": "movement_correction",
        "patterns": (
            r"교정",
            r"정렬을?\s*(잡|맞|유지)",
            r"무릎.*(안쪽|바깥|정렬)",
            r"엉덩이",
            r"호흡",
            r"숨을?\s*(참|쉬|내쉬|들이)",
            r"cue",
            r"correct",
            r"align",
            r"keep your",
        ),
        "confidence": 0.7,
    },
    {
        "candidate_type": "orthosis_assistive_device",
        "patterns": (
            r"보조기",
            r"지팡이",
            r"워커",
            r"목발",
            r"밴드",
            r"테이핑",
            r"orthos",
            r"brace",
            r"cane",
            r"walker",
            r"crutch",
            r"band",
            r"tape",
        ),
        "confidence": 0.78,
    },
    {
        "candidate_type": "exercise_instruction",
        "patterns": (
            r"운동",
            r"훈련",
            r"과제",
            r"반복",
            r"세트",
            r"유지",
            r"따라하세요",
            r"exercise",
            r"repeat",
            r"reps?",
            r"sets?",
            r"hold",
            r"perform",
        ),
        "confidence": 0.7,
    },
    {
        "candidate_type": "caregiver_education",
        "patterns": (
            r"보호자",
            r"교육",
            r"설명",
            r"가르쳐",
            r"caregiver",
            r"family",
            r"educat",
            r"teach",
            r"explain",
        ),
        "confidence": 0.72,
    },
    {
        "candidate_type": "home_program",
        "patterns": (
            r"집에서",
            r"홈\s*프로그램",
            r"숙제",
            r"매일",
            r"하루",
            r"home\s*(exercise|program)",
            r"at\s*home",
            r"daily",
        ),
        "confidence": 0.74,
    },
)

_COMPILED_RULES = tuple(
    (rule, tuple(re.compile(pattern, re.IGNORECASE) for pattern in rule["patterns"]))
    for rule in _RULES
)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _sentences(text: str) -> list[str]:
    clean = _clean_text(text)
    if not clean:
        return []
    chunks = re.split(r"(?<=[.!?。！？])\s+|\s*[\n\r]+\s*", clean)
    return [chunk.strip(" .,!?:;。！？") for chunk in chunks if chunk.strip(" .,!?:;。！？")]


def _laterality(text: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ("좌측", "왼쪽", "left", " lt ")):
        return "left"
    if any(token in lower for token in ("우측", "오른쪽", "right", " rt ")):
        return "right"
    if any(token in lower for token in ("양측", "both", "bilateral")):
        return "bilateral"
    return ""


def _body_region(text: str) -> str:
    lower = text.lower()
    for region, tokens in (
        ("low_back", ("허리", "요추", "lumbar", "low back")),
        ("neck", ("목", "경추", "cervical", "neck")),
        ("shoulder", ("어깨", "shoulder")),
        ("knee", ("무릎", "knee")),
        ("hip", ("고관절", "엉덩이", "hip")),
        ("ankle", ("발목", "ankle")),
        ("upper_limb", ("팔", "손목", "elbow", "wrist", "arm")),
    ):
        if any(token in lower for token in tokens):
            return region
    return ""


def _value(text: str) -> str:
    match = re.search(r"-?\d+(?:\.\d+)?\s*(?:도|deg(?:ree)?s?|°|/\s*(?:10|100))?", text, re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _candidate(
    candidate_type: str,
    sentence: str,
    confidence: float,
    *,
    test: str = "",
    provider_role: str | None = None,
) -> dict[str, Any]:
    semantics = extract_capture_semantics(candidate_type, sentence, provider_role=provider_role)
    payload: dict[str, Any] = {
        "label": sentence[:240],
        "note": sentence[:1000],
        "source_text": sentence[:1000],
        "extractor": TRANSCRIPT_CAPTURE_EXTRACTOR_VERSION,
        "semantic_role": candidate_type,
        "action_type": capture_action_type(candidate_type),
        "semantic": semantics,
        "review_required": "true",
    }
    for key, value in semantics.items():
        if key not in {"version", "domain"}:
            payload[key] = value
    laterality = _laterality(sentence)
    body_region = _body_region(sentence)
    value = _value(sentence)
    if laterality:
        payload["laterality"] = laterality
    if body_region:
        payload["body_region"] = body_region
    if value:
        payload["value"] = value
    if test:
        payload["test"] = test
    return {
        "event_type": candidate_type,
        "candidate_type": candidate_type,
        "confidence": max(0.5, min(0.95, confidence)),
        "payload": payload,
    }


def extract_transcript_capture_candidates(
    text: str,
    *,
    provider_role: str | None = None,
) -> list[dict[str, Any]]:
    """Extract conservative, provider-reviewable draft candidates.

    A sentence may produce multiple candidates when it explicitly contains
    multiple clinical roles (for example, an exercise correction plus a home
    program instruction).  Duplicates are removed by semantic role and source
    sentence.  The function intentionally returns no candidate for vague text.
    """

    sentences = _sentences(text)
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for sentence in sentences:
        for rule, patterns in _COMPILED_RULES:
            if not any(pattern.search(sentence) for pattern in patterns):
                continue
            candidate_type = str(rule["candidate_type"])
            key = (candidate_type, sentence.casefold())
            if key in seen:
                continue
            seen.add(key)
            output.append(
                _candidate(
                    candidate_type,
                    sentence,
                    float(rule["confidence"]),
                    provider_role=provider_role,
                )
            )
    return output[:40]
