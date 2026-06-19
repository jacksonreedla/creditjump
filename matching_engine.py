"""
CreditJump — matching engine (v0)

Takes the parser's output + a destination school's articulation database and
decides, per course, what happens to it on transfer. Mirrors how a real
registrar evaluates a transfer: check the articulation agreement first, then
fall back to general elective credit, then reject what can't move.

Each course comes back with:
    status      direct | elective | pending | none | ineligible
    ui_status   go | review | no            (maps to the 3 buckets in the UI)
    applies_to  major | gen_ed | elective | None
    confidence  verified | estimated | projected
    plus the destination course it maps to (when known)
"""

import re
import json
from transcript_parser import parse_transcript

# grade handling -------------------------------------------------------------
GRADE_RANK = {
    "A+": 12, "A": 11, "A-": 10, "B+": 9, "B": 8, "B-": 7,
    "C+": 6, "C": 5, "C-": 4, "D+": 3, "D": 2, "D-": 1, "F": 0,
    "P": 5, "S": 5, "CR": 5,         # pass-type grades treated as ~C
}
MIN_CREDIT_GRADE = 1     # D- or better earns transfer credit at all
MIN_MAJOR_GRADE  = 5     # C or better to satisfy a major/gen-ed requirement

# courses that never transfer, identified by pattern not by a DB row
INSTITUTIONAL_KEYWORDS = (
    "academic success", "student success", "college success",
    "orientation", "first year seminar", "freshman seminar",
    "study skills", "college prep", "developmental",
)


def normalize(code: str) -> str:
    """ENGL 1001 / ENGL1001 / ENGL 1001H  ->  ENGL1001  (drops honors suffix)."""
    c = re.sub(r"\s+", "", code.upper())
    c = re.sub(r"([A-Z]{2,4}\d{3,4})[A-Z]$", r"\1", c)   # strip trailing H/E etc.
    return c


def load_articulation(path: str) -> dict:
    with open(path) as f:
        db = json.load(f)
    return {normalize(e["source_code"]): e for e in db["equivalencies"]}, db["destination"]


def course_number(code: str) -> int:
    m = re.search(r"(\d{3,4})", code)
    return int(m.group(1)) if m else 0


def is_institutional(course: dict) -> bool:
    title = (course["title"] or "").lower()
    return any(k in title for k in INSTITUTIONAL_KEYWORDS)


def evaluate_course(course: dict, table: dict) -> dict:
    code_n = normalize(course["code"])
    grade = course["grade"]
    pending = course["status"] == "in_progress"
    rank = GRADE_RANK.get(grade, None)

    base = {
        "code": course["code"],
        "title": course["title"],
        "credits": course["credits"],
        "grade": grade,
        "dest_code": None,
        "dest_title": None,
        "applies_to": None,
        "status": None,
        "ui_status": None,
        "confidence": None,
        "note": None,
    }

    # 1. failed / withdrawn completed courses never transfer
    if not pending and rank is not None and rank < MIN_CREDIT_GRADE:
        base.update(status="ineligible", ui_status="no", confidence="verified",
                    note=f"Grade of {grade} — earns no transferable credit.")
        return base

    # 2. institutional / developmental courses never transfer
    if is_institutional(course) or course_number(course["code"]) < 1000:
        base.update(status="none", ui_status="no", confidence="verified",
                    note="Institutional or developmental course — not accepted in transfer.")
        return base

    # 3. articulation agreement hit -> direct equivalent
    hit = table.get(code_n)
    if hit:
        applies = hit["applies_to"]
        # a completed grade below C still transfers, but only as elective credit
        if not pending and rank is not None and rank < MIN_MAJOR_GRADE and applies != "elective":
            applies = "elective"
            note = f"Transfers, but a {grade} is below the grade needed to satisfy the requirement."
        else:
            note = None
        base.update(
            dest_code=hit["dest_code"], dest_title=hit["dest_title"],
            credits=hit["credits"], applies_to=applies,
            status="pending" if pending else "direct",
            ui_status="go",
            confidence="projected" if pending else "verified",
            note=note or ("In progress — will transfer once you complete it." if pending else None),
        )
        return base

    # 4. no agreement, but it's a real college-level course -> elective credit
    base.update(
        dest_code="—", dest_title="General elective credit",
        applies_to="elective",
        status="pending" if pending else "elective",
        ui_status="review",
        confidence="projected" if pending else "estimated",
        note="No direct equivalent on file — should transfer as elective credit (confirm with the registrar).",
    )
    return base


def match(parsed: dict, articulation_path: str) -> dict:
    table, destination = load_articulation(articulation_path)
    results = [evaluate_course(c, table) for c in parsed["courses"]]

    def credits(statuses):
        return sum(r["credits"] for r in results if r["status"] in statuses)

    confirmed   = credits({"direct", "elective"})
    projected   = credits({"pending"})
    not_transfer= credits({"none", "ineligible"})
    total       = sum(r["credits"] for r in results)

    return {
        "destination": destination,
        "summary": {
            "total_credits": total,
            "confirmed_transfer": confirmed,
            "toward_major_or_gened": credits({"direct"}) if True else 0,
            "as_elective": sum(r["credits"] for r in results
                               if r["status"] in {"direct", "elective"} and r["applies_to"] == "elective"),
            "projected_in_progress": projected,
            "not_transferring": not_transfer,
        },
        "courses": results,
    }


if __name__ == "__main__":
    import sys
    pdf = sys.argv[1] if len(sys.argv) > 1 else "UnofficialTranscript.pdf"
    parsed = parse_transcript(pdf)
    out = match(parsed, "articulation_db.json")
    print(json.dumps(out, indent=2))
