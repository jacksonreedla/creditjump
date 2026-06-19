"""
CreditJump — articulation table scraper (v0)

Pulls transfer-equivalency tables and writes them into the same schema the
matching engine reads (articulations/<school>.json).

Order of preference (cheapest + most reliable first):
  1. Official / structured data (state systems, downloadable files) — no scraper.
  2. A site's internal JSON endpoint  -> scrape_json_endpoint()  (clean, stable)
  3. A rendered HTML table            -> scrape_html_table()      (last resort)

Politeness / good citizenship (all built in):
  - checks the site's robots.txt before fetching, and refuses if disallowed
  - honors Crawl-delay; otherwise waits DEFAULT_DELAY between requests
  - sends a real, identifiable User-Agent
ALWAYS also read each site's Terms of Service. Scraping public data is broadly
allowed in the US, but ToS or robots can forbid it (many .edu sites do). This is
not legal advice — for a real business, get a quick consult.

Usage:
    pip install -r requirements-scrape.txt
    python scraper.py sample_scrape_config.json     # run a configured job
    python scraper.py --demo                         # parse a built-in sample
"""

import os
import re
import sys
import json
import time
from datetime import date
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "CreditJumpBot/0.1 (+https://YOUR-DOMAIN/about; contact@YOUR-DOMAIN)"
DEFAULT_DELAY = 2.0          # seconds between requests
OUT_DIR = "articulations"


# ---------------------------------------------------------------- helpers ----
def normalize(code: str) -> str:
    c = re.sub(r"\s+", "", (code or "").upper())
    c = re.sub(r"([A-Z]{2,4}\d{3,4})[A-Z]$", r"\1", c)
    return c


def parse_credits(text: str):
    m = re.search(r"\d+(?:\.\d+)?", text or "")
    return float(m.group()) if m else None


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def robots_allows(url: str, user_agent: str = USER_AGENT):
    """Return (allowed: bool, delay: float). Missing robots.txt -> allowed, polite."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
    except Exception:
        return True, DEFAULT_DELAY
    allowed = rp.can_fetch(user_agent, url)
    crawl_delay = rp.crawl_delay(user_agent)
    return allowed, max(DEFAULT_DELAY, crawl_delay or 0)


def fetch(url, session, delay, method="GET", **kwargs):
    time.sleep(delay)                       # be gentle
    resp = session.request(method, url, timeout=20, **kwargs)
    resp.raise_for_status()
    return resp


def _row(source_code, dest_code, dest_title, credits, applies_to, source_url):
    """Build one equivalency row + provenance, or None if unusable."""
    if not source_code or not dest_code:
        return None
    return {
        "source_code": source_code.strip(),
        "dest_code": dest_code.strip(),
        "dest_title": (dest_title or "").strip(),
        "credits": parse_credits(credits) if not isinstance(credits, (int, float)) else float(credits),
        "applies_to": applies_to,
        "source_url": source_url,          # provenance — extra keys are ignored by the engine
        "pulled": date.today().isoformat(),
    }


# ------------------------------------------------------------ HTML tables ----
def parse_table_html(html, columns, table_selector=None,
                     applies_to_default="elective", source_url="(local)"):
    """
    columns maps our fields -> cell index, e.g.
        {"source_code": 0, "dest_code": 1, "dest_title": 2, "credits": 3}
    source_code and dest_code are required; the rest are optional.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one(table_selector) if table_selector else soup.find("table")
    if table is None:
        return []
    need = max(columns.values())
    rows = []
    for tr in table.select("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) <= need:                       # header or malformed row
            continue
        row = _row(
            cells[columns["source_code"]],
            cells[columns["dest_code"]],
            cells[columns["dest_title"]] if "dest_title" in columns else "",
            cells[columns["credits"]] if "credits" in columns else None,
            applies_to_default,
            source_url,
        )
        if row:
            rows.append(row)
    return rows


def scrape_html_table(url, columns, table_selector=None,
                      applies_to_default="elective", session=None):
    allowed, delay = robots_allows(url)
    if not allowed:
        raise PermissionError(f"robots.txt disallows scraping {url}")
    session = session or make_session()
    resp = fetch(url, session, delay)
    return parse_table_html(resp.text, columns, table_selector, applies_to_default, url)


# --------------------------------------------------------- JSON endpoints ----
def _dig(data, path):
    """Follow a dotted path into nested JSON, e.g. 'data.results'."""
    if not path:
        return data
    for key in path.split("."):
        data = data[key]
    return data


def scrape_json_endpoint(url, fields, method="GET", params=None, payload=None,
                         records_path=None, applies_to_default="elective", session=None):
    """
    fields maps our fields -> keys in each JSON record, e.g.
        {"source_code":"fromCourse","dest_code":"toCourse","dest_title":"toTitle","credits":"hours"}
    """
    allowed, delay = robots_allows(url)
    if not allowed:
        raise PermissionError(f"robots.txt disallows scraping {url}")
    session = session or make_session()
    resp = fetch(url, session, delay, method=method, params=params, json=payload)
    records = _dig(resp.json(), records_path)
    rows = []
    for rec in records:
        row = _row(
            rec.get(fields["source_code"]),
            rec.get(fields["dest_code"]),
            rec.get(fields.get("dest_title", ""), ""),
            rec.get(fields.get("credits", ""), None),
            applies_to_default,
            url,
        )
        if row:
            rows.append(row)
    return rows


# ----------------------------------------------------------------- output ----
def write_articulation(destination, rows, out_dir=OUT_DIR):
    """De-dupe by normalized source_code, then write articulations/<slug>.json."""
    seen, deduped = set(), []
    for r in rows:
        key = normalize(r["source_code"])
        if key and key not in seen:
            seen.add(key)
            deduped.append(r)
    slug = re.sub(r"[^a-z0-9]+", "_", destination.lower()).strip("_")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{slug}.json")
    with open(path, "w") as f:
        json.dump({"destination": destination, "source": "scraped",
                   "equivalencies": deduped}, f, indent=2)
    return path, len(deduped)


def run_config(config: dict):
    mode = config.get("mode", "html")
    if mode == "html":
        rows = scrape_html_table(
            config["url"], config["columns"],
            config.get("table_selector"),
            config.get("applies_to_default", "elective"),
        )
    elif mode == "json":
        rows = scrape_json_endpoint(
            config["url"], config["fields"],
            method=config.get("method", "GET"),
            params=config.get("params"), payload=config.get("payload"),
            records_path=config.get("records_path"),
            applies_to_default=config.get("applies_to_default", "elective"),
        )
    else:
        raise ValueError(f"Unknown mode: {mode}")
    path, n = write_articulation(config["destination"], rows)
    print(f"Wrote {n} equivalencies for '{config['destination']}' -> {path}")
    return path


# ------------------------------------------------------------------- demo ----
DEMO_HTML = """
<table class="equiv">
  <tr><th>Their Course</th><th>Our Course</th><th>Title</th><th>Credits</th></tr>
  <tr><td>ENGL 1010</td><td>ENGL 1001</td><td>English Composition I</td><td>3</td></tr>
  <tr><td>MATH 1530</td><td>MATH 1021</td><td>College Algebra</td><td>3</td></tr>
  <tr><td>BIOL 1110</td><td>BIOL 1201</td><td>Biology I</td><td>4</td></tr>
</table>
"""


def demo():
    cols = {"source_code": 0, "dest_code": 1, "dest_title": 2, "credits": 3}
    rows = parse_table_html(DEMO_HTML, cols, "table.equiv", source_url="(demo)")
    path, n = write_articulation("Demo University", rows)
    print(f"Parsed {n} rows from the built-in sample table -> {path}\n")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--demo":
        demo()
    elif len(sys.argv) == 2:
        with open(sys.argv[1]) as f:
            run_config(json.load(f))
    else:
        print(__doc__)
        sys.exit(1)
