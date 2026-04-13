"""
scraper/scrape_faculty_pages.py

Scrapes UNH public faculty pages to get:
- Coordinator roles
- Phone numbers
- Office locations
- Email addresses

Merges this data into professors.json automatically.
Run: python3 scraper/scrape_faculty_pages.py
Also runs automatically as part of weekly update.
"""

import json, re, requests, time, logging
from bs4 import BeautifulSoup
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

OUTPUT = Path(__file__).parent.parent / "data" / "professors.json"

HEADERS = {"User-Agent": "AskCharlieBot/3.0 (University Chatbot)"}

# ── All UNH faculty pages to scrape ──────────────────────────────────────────
FACULTY_PAGES = [
    # Engineering
    "https://www.newhaven.edu/engineering/academic-departments/electrical-computer-engineering-computer-science-faculty.php",
    "https://www.newhaven.edu/engineering/graduate-programs/data-science/faculty.php",
    "https://www.newhaven.edu/engineering/graduate-programs/artificial-intelligence/faculty.php",
    "https://www.newhaven.edu/engineering/undergraduate-programs/computer-science/faculty.php",
    "https://www.newhaven.edu/engineering/academic-departments/mechanical-industrial/faculty.php",
    "https://www.newhaven.edu/engineering/academic-departments/civil-environmental/faculty.php",
    # Business
    "https://www.newhaven.edu/business/graduate-programs/information-science/faculty.php",
    "https://www.newhaven.edu/business/departments/accounting-taxation/faculty/index.aspx",
    "https://www.newhaven.edu/business/departments/management/faculty/index.aspx",
    # Arts & Sciences
    "https://www.newhaven.edu/arts-sciences/departments/biology/faculty/index.aspx",
    "https://www.newhaven.edu/arts-sciences/departments/chemistry/faculty/index.aspx",
    "https://www.newhaven.edu/arts-sciences/departments/mathematics-physics/faculty/index.aspx",
    "https://www.newhaven.edu/arts-sciences/departments/psychology/faculty/index.aspx",
    "https://www.newhaven.edu/arts-sciences/departments/communication-film-media/faculty/index.aspx",
    # Lee College
    "https://www.newhaven.edu/lee-college/departments/criminal-justice/faculty/index.aspx",
    "https://www.newhaven.edu/lee-college/departments/forensic-science/faculty/index.aspx",
    # Health Sciences
    "https://www.newhaven.edu/health-sciences/departments/health-administration/faculty/index.aspx",
]


def scrape_faculty_page(url: str) -> list:
    """
    Scrape a single UNH faculty page.
    Returns list of dicts with: name, title, office, phone, email
    """
    results = []
    try:
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code != 200:
            log.warning(f"  {url.split('/')[-1]} → {r.status_code}")
            return results

        soup = BeautifulSoup(r.text, "lxml")
        text = soup.get_text("\n")

        # Pattern 1: Look for faculty cards with structured data
        cards = soup.select(".faculty-member, .people-card, .staff-member, article.person, .faculty-listing li, .faculty-card")

        if cards:
            for card in cards:
                name_el  = card.select_one("h2, h3, h4, .name, .faculty-name, strong")
                email_el = card.select_one('a[href^="mailto:"]')
                phone_el = card.select_one(".phone, .tel")
                office_el= card.select_one(".office, .location, .room")
                title_el = card.select_one(".title, .position, .rank")
                if name_el:
                    results.append({
                        "name":   name_el.get_text(strip=True),
                        "email":  email_el["href"].replace("mailto:","").strip() if email_el else "",
                        "phone":  phone_el.get_text(strip=True) if phone_el else "",
                        "office": office_el.get_text(strip=True) if office_el else "",
                        "title":  title_el.get_text(strip=True) if title_el else "",
                    })

        # Pattern 2: Extract emails and nearby info from page text
        # UNH faculty pages often have: Name | Office | Phone | Email
        email_re = re.compile(r'([A-Za-z]\w+@newhaven\.edu)', re.IGNORECASE)
        phone_re = re.compile(r'\(203\)\s*\d{3}[-\s]\d{4}')
        office_re = re.compile(r'((?:Maxcy|Echlin|Bergami|Sheffield|Bixler|Dodds|Buckman|York|Marvin)\s+Hall[\w\s,]+\d+\w*)', re.IGNORECASE)

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            email_match = email_re.search(line)
            if email_match:
                email = email_match.group(1)
                # Look for phone in nearby lines
                phone = ""
                office = ""
                title = ""
                for nearby in lines[max(0,i-5):i+5]:
                    pm = phone_re.search(nearby)
                    om = office_re.search(nearby)
                    if pm and not phone: phone = pm.group()
                    if om and not office: office = om.group().strip()

                # Check if already in results
                already = any(r.get("email","").lower() == email.lower() for r in results)
                if not already:
                    results.append({
                        "name":   "",  # will be matched by email username
                        "email":  email,
                        "phone":  phone,
                        "office": office,
                        "title":  title,
                    })

        log.info(f"  {url.split('/')[-2]}/{url.split('/')[-1]} → {len(results)} faculty found")
    except Exception as e:
        log.warning(f"  Failed {url}: {e}")
    return results


def normalize_name(name: str) -> str:
    """Normalize a name for matching: 'Pithadia, Div' → 'pithadia div'"""
    return re.sub(r"[^a-z\s]", "", name.lower().replace(",", " ")).strip()


def email_username(email: str) -> str:
    """Get username from email: 'DPithadia@newhaven.edu' → 'dpithadia'"""
    return email.split("@")[0].lower()


def run():
    if not OUTPUT.exists():
        log.error(f"professors.json not found at {OUTPUT}")
        return

    professors = json.loads(OUTPUT.read_text(encoding="utf-8"))
    log.info(f"Loaded {len(professors)} professors")

    # Build lookup by normalized name and email username
    name_lookup  = {normalize_name(p.get("name","")): p for p in professors if p.get("name")}
    email_lookup = {email_username(p.get("email","")): p for p in professors if p.get("email")}

    # Scrape all faculty pages
    all_faculty = []
    for url in FACULTY_PAGES:
        log.info(f"Scraping {url.split('/')[-2]}...")
        faculty = scrape_faculty_page(url)
        all_faculty.extend(faculty)
        time.sleep(1)

    log.info(f"\nTotal faculty data found: {len(all_faculty)}")

    # Merge faculty data into professors
    updated = 0
    for f in all_faculty:
        target = None

        # Try match by name first
        if f.get("name"):
            nkey = normalize_name(f["name"])
            if nkey in name_lookup:
                target = name_lookup[nkey]

        # Try match by email username
        if not target and f.get("email"):
            ekey = email_username(f["email"])
            if ekey in email_lookup:
                target = email_lookup[ekey]
            else:
                # Try to find professor with matching guessed email
                for p in professors:
                    if email_username(p.get("email","")) == ekey:
                        target = p
                        break

        if target:
            changed = False
            # Only update if the new data is better (not empty)
            if f.get("email") and not target.get("email"):
                target["email"] = f["email"]
                changed = True
            if f.get("phone") and not target.get("phone"):
                target["phone"] = f["phone"]
                changed = True
            if f.get("office") and not target.get("office"):
                target["office"] = f["office"]
                changed = True
            if f.get("title") and len(f["title"]) > len(target.get("title","")):
                target["title"] = f["title"]  # use longer/more detailed title
                changed = True
            if changed:
                updated += 1

    log.info(f"Updated {updated} professors from faculty pages")

    # Save
    OUTPUT.write_text(json.dumps(professors, indent=2, ensure_ascii=False))
    log.info(f"\n✅ Saved {len(professors)} professors")
    log.info(f"With email:  {sum(1 for p in professors if p.get('email'))}")
    log.info(f"With phone:  {sum(1 for p in professors if p.get('phone'))}")
    log.info(f"With office: {sum(1 for p in professors if p.get('office'))}")

    # Show Pithadia as sample
    for p in professors:
        if "pithadia" in (p.get("name") or "").lower():
            log.info(f"\nSample (Pithadia):\n{json.dumps(p, indent=2)}")
            break


if __name__ == "__main__":
    run()
