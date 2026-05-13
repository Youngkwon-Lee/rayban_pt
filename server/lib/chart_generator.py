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
{today}

S>
{_to_bullets(transcript_text)}

O>
{_to_bullets(o_section)}

P/E>
{_to_bullets(pe_section)}

A>
{_to_bullets(a_section)}

PTx.>
{_to_bullets(p_section)}
"""
    return body


def _to_bullets(text: str) -> str:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if not lines:
        return "(내용 없음)"
    return "\n".join(lines[:20])


def save_chart(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
