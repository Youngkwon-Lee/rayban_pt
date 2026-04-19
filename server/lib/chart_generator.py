from pathlib import Path
from datetime import datetime


def generate_chart(
    template_name: str,
    uuid: str,
    date: str,
    transcript_text: str,
    image_notes: str = "",
    prior_summary: str = "",
    objective: str = "",
    assessment: str = "",
    plan: str = "",
) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    pe_section = image_notes or "(수동 입력 필요)"
    o_section = objective or "(수동 입력 필요)"
    a_section = assessment or "(임상 검수 필요)"
    p_section = plan or "· all major joint GPROM ex.\n· shorten muscle stretching ex.\n· trunk mobilization"

    body = f"""F/U>
# Auto-generated draft ({today})
# UUID: {uuid}

Dx.>
# (기존 진단 유지/검토 필요)

S>
# 음성기록 기반 요약
{_to_bullets(transcript_text)}

O>
# 객관적 측정값
{_to_bullets(o_section)}

P/E>
# 이미지/관찰 기반 요약
{_to_bullets(pe_section)}

A>
# 임상 해석
{_to_bullets(a_section)}

rehab device>
# (수동 입력 필요)

PTx.>
{_to_bullets(p_section)}

Comment>
# prior summary
{_to_bullets(prior_summary or "(이전기록 요약 없음)")}
# 주의: 본 문서는 자동 생성 초안이며, 최종 서명 전 임상 검수가 필요함.
"""
    return body


def _to_bullets(text: str) -> str:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if not lines:
        return "# (내용 없음)"
    return "\n".join([f"# {line}" for line in lines[:20]])


def save_chart(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
