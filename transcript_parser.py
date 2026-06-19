"""
CreditJump — transcript parser (v0)

Turns a transcript PDF into structured course data the matching engine can use.

Tested against a PowerCampus / SSRS-exported transcript (LSUA). The design goal
is to be format-tolerant: course rows are detected by pattern, not by fixed
column positions, so other schools' layouts have a fair chance of working too.

Output shape per course:
    {
      "term": "2025 Fall",
      "institution": "Louisiana State University at Alexandria",
      "code": "ENGL 1001",
      "subject": "ENGL",
      "number": "1001",
      "title": "English Composition",
      "subtype": "Lecture",
      "grade": "A",
      "credits": 3.0,
      "quality_points": 12.0,
      "status": "completed"            # or "in_progress" (no grade yet)
    }
"""

import re
import json
import pdfplumber

# --- vocabulary the parser keys off of ---------------------------------------

SUBTYPES = {
    "Lecture", "Laboratory", "Lab", "Seminar", "Clinical", "Studio",
    "Recitation", "Practicum", "Internship", "Independent", "Online",
    "Discussion", "Field", "Research", "Thesis", "Activity",
}

# grades we recognize. anything positionally in the grade slot is still captured
# even if it's not in this set — this just helps validation/eligibility.
GRADES = {
    "A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-",
    "F", "P", "S", "U", "W", "I", "IP", "NC", "CR", "AU", "WF",
}

# grades that earn transferable credit (the matching engine can override this
# per destination school, but this is a sane default)
PASSING = {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "P", "S", "CR"}

TERM_RE   = re.compile(r"^(\d{4})\s+(Fall|Spring|Summer|Winter)\b", re.I)
COURSE_RE = re.compile(r"^([A-Z]{2,4})\s+(\d{3,4}[A-Z]?)\s+(.*\S)\s*$")
NUM_RE    = re.compile(r"^\d+(?:\.\d+)?$")
GPA_RE    = re.compile(r"Cumulative GPA:\s*([\d.]+)", re.I)

# lines that are never courses — skip fast
NOISE = (
    "Attempted", "Earned", "Total", "GPA", "Transfer", "Quality",
    "Course Title", "Office of the Registrar", "Unofficial Transcript",
    "Program /", "Honors:", "Previous Institution:", "Test Scores",
    "Degree Awarded", "Page ", "Term ", "Overall ",
)


def _looks_like_institution(line: str) -> bool:
    """The line right under a term header is the school the courses were taken at."""
    l = line.strip()
    if not l or COURSE_RE.match(l) or TERM_RE.match(l):
        return False
    return bool(re.search(r"(University|College|Institute|School|Community)", l, re.I))


def _parse_course_tail(remainder: str):
    """
    Given everything after the course code, pull out:
        title, subtype, grade, credits, quality_points
    Works back-to-front because the title is the only variable-length piece.
    """
    tokens = remainder.split()
    if len(tokens) < 2:
        return None

    # last two numeric tokens are credits + quality points
    if not (NUM_RE.match(tokens[-1]) and NUM_RE.match(tokens[-2])):
        return None
    quality_points = float(tokens[-1])
    credits = float(tokens[-2])
    body = tokens[:-2]

    # find the subtype keyword nearest the end; everything before it is the title
    subtype = None
    subtype_idx = None
    for i in range(len(body) - 1, -1, -1):
        if body[i] in SUBTYPES:
            subtype, subtype_idx = body[i], i
            break

    if subtype_idx is None:
        # no subtype found — treat any trailing single token as a grade
        title = " ".join(body[:-1]) if len(body) > 1 else " ".join(body)
        grade = body[-1] if body and body[-1] in GRADES else None
        if grade:
            title = " ".join(body[:-1])
    else:
        title = " ".join(body[:subtype_idx]).strip()
        between = body[subtype_idx + 1:]          # tokens between subtype and numbers
        grade = between[0] if between else None    # 0 tokens => in progress

    return {
        "title": title,
        "subtype": subtype,
        "grade": grade,
        "credits": credits,
        "quality_points": quality_points,
    }


def parse_transcript(pdf_path: str) -> dict:
    # 1. flatten the PDF to text lines
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        issuing_institution = None
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.split("\n"):
                line = raw.strip()
                if line:
                    lines.append(line)

    # issuing institution = first school-looking line at the top
    issuing_institution = next(
        (l for l in lines[:5] if re.search(r"(University|College)", l, re.I)), None
    )

    # 2. walk the lines, tracking term + institution context
    courses = []
    current_term = None
    current_institution = issuing_institution
    expect_institution = False
    cumulative_gpa = None

    for line in lines:
        gpa_match = GPA_RE.search(line)
        if gpa_match:
            cumulative_gpa = float(gpa_match.group(1))

        term_match = TERM_RE.match(line)
        if term_match:
            current_term = f"{term_match.group(1)} {term_match.group(2).title()}"
            expect_institution = True
            continue

        if expect_institution:
            if _looks_like_institution(line):
                current_institution = line.strip()
                expect_institution = False
                continue
            # column header or blank — keep waiting one more line
            if line.startswith("Course Title"):
                continue

        if any(line.startswith(n) for n in NOISE):
            continue

        m = COURSE_RE.match(line)
        if not m:
            continue
        subject, number, remainder = m.group(1), m.group(2), m.group(3)
        parsed = _parse_course_tail(remainder)
        if not parsed:
            continue

        grade = parsed["grade"]
        status = "completed" if grade else "in_progress"
        courses.append({
            "term": current_term,
            "institution": current_institution,
            "code": f"{subject} {number}",
            "subject": subject,
            "number": number,
            "title": parsed["title"],
            "subtype": parsed["subtype"],
            "grade": grade,
            "credits": parsed["credits"],
            "quality_points": parsed["quality_points"],
            "status": status,
            "passing": (grade in PASSING) if grade else None,
        })

    return {
        "issuing_institution": issuing_institution,
        "cumulative_gpa": cumulative_gpa,
        "course_count": len(courses),
        "courses": courses,
    }


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "UnofficialTranscript.pdf"
    result = parse_transcript(path)
    print(json.dumps(result, indent=2))
