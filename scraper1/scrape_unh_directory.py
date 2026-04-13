"""
scraper/scrape_unh_directory.py

Gets all UNH staff from the directory grid, then:
1. Guesses email from UNH naming convention: first_initial + lastname @newhaven.edu
2. Tries to verify emails by scraping department faculty pages
3. Saves confirmed + guessed emails

Run: python3 scraper/scrape_unh_directory.py
"""

import requests
import urllib3
from bs4 import BeautifulSoup
import json, time, string, logging, re
from pathlib import Path

urllib3.disable_warnings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_URL = "https://newhaven-web-01.newhaven.edu/ad/addata.aspx"
OUTPUT   = Path(__file__).parent.parent / "data" / "professors.json"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": BASE_URL,
}

# Public UNH pages that list faculty with contact info
CONTACT_PAGES = [
    "https://www.newhaven.edu/engineering/index.php",
    "https://www.newhaven.edu/business/index.php",
    "https://www.newhaven.edu/arts-sciences/index.php",
    "https://www.newhaven.edu/lee-college/index.php",
    "https://www.newhaven.edu/health-sciences/index.php",
    "https://www.newhaven.edu/about/departments/index.php",
    "https://www.newhaven.edu/about/administration/index.php",
]


def get_tokens(session):
    resp = session.get(BASE_URL, headers=HEADERS, timeout=15, verify=False)
    soup = BeautifulSoup(resp.text, "html.parser")
    return {f: (soup.find("input", {"name": f}) or {}).get("value", "")
            for f in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]}


def parse_grid(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "GridView2"})
    if not table:
        return []
    people = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        name  = cells[1].get_text(strip=True)
        dept  = cells[2].get_text(strip=True)
        title = cells[3].get_text(strip=True)
        if name and name not in ("-", ""):
            people.append({"name": name, "department": dept, "title": title})
    return people


def guess_email(name: str) -> str:
    """
    Guess UNH email from name.
    UNH pattern: first_initial + lastname @newhaven.edu
    e.g. "Pithadia, Div" → "dpithadia@newhaven.edu"
    e.g. "Smith, John" → "jsmith@newhaven.edu"
    """
    # Format is "LastName, FirstName" or "LastName, FirstName MiddleName"
    parts = name.split(",")
    if len(parts) < 2:
        return ""
    last  = parts[0].strip().lower()
    first = parts[1].strip().lower().split()[0]  # just first name, ignore middle

    # Clean special characters
    last  = re.sub(r"[^a-z]", "", last)
    first = re.sub(r"[^a-z]", "", first)

    if first and last:
        return f"{first[0]}{last}@newhaven.edu"
    return ""


def scrape_emails_from_page(url: str) -> dict:
    """
    Scrape a UNH page for any mailto links and return {name: email} dict.
    """
    emails = {}
    try:
        r = requests.get(url, timeout=15,
                        headers={"User-Agent": "AskCharlieBot/3.0"})
        if r.status_code != 200:
            return emails
        soup = BeautifulSoup(r.text, "html.parser")

        # Find all mailto links
        for a in soup.find_all("a", href=re.compile(r"^mailto:")):
            email = a["href"].replace("mailto:", "").strip().lower()
            if "@newhaven.edu" in email:
                # Try to find associated name near this link
                parent = a.find_parent(["li", "div", "td", "p", "article"])
                if parent:
                    # Look for name in nearby heading
                    heading = parent.find(["h2", "h3", "h4", "strong", "b"])
                    if heading:
                        name = heading.get_text(strip=True)
                        if name:
                            emails[name.lower()] = email
                emails[email.split("@")[0]] = email  # also index by username

    except Exception as e:
        log.debug(f"Email scrape failed for {url}: {e}")
    return emails


def run():
    session = requests.Session()
    all_people = []
    seen = set()

    # ── Step 1: Get all names from A-Z grid ──────────────────────────────────
    log.info("Step 1: Scraping A-Z directory grid...")
    for letter in string.ascii_uppercase:
        log.info(f"  '{letter}'...")
        try:
            tokens = get_tokens(session)
            payload = {**tokens, "txtName": "", "txtLastName": letter,
                      "Dept": "-1", "btnSearch": "SEARCH"}
            resp = session.post(BASE_URL, data=payload, headers=HEADERS,
                               timeout=25, verify=False)
            people = parse_grid(resp.text)
            added = 0
            for p in people:
                name = p["name"].strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                all_people.append(p)
                added += 1
            log.info(f"    +{added} (total: {len(all_people)})")
        except Exception as e:
            log.warning(f"  '{letter}' failed: {e}")
        time.sleep(0.5)

    # ── Step 2: Guess emails using UNH naming convention ─────────────────────
    log.info("\nStep 2: Guessing emails from UNH naming convention...")
    guessed = 0
    for p in all_people:
        email = guess_email(p["name"])
        if email:
            p["email"] = email
            guessed += 1
    log.info(f"  Guessed {guessed} emails")

    # ── Step 3: Scrape public pages to find confirmed emails ──────────────────
    log.info("\nStep 3: Finding confirmed emails from public pages...")
    all_emails = {}
    for url in CONTACT_PAGES:
        log.info(f"  Scraping {url}...")
        page_emails = scrape_emails_from_page(url)
        all_emails.update(page_emails)
        time.sleep(0.5)
    log.info(f"  Found {len(all_emails)} confirmed emails")

    # Match confirmed emails back to people
    confirmed = 0
    for p in all_people:
        # Try to match by guessed email username
        guessed_email = p.get("email", "")
        if guessed_email:
            username = guessed_email.split("@")[0]
            if username in all_emails:
                p["email"] = all_emails[username]  # confirmed!
                p["email_confirmed"] = True
                confirmed += 1

    log.info(f"  Confirmed {confirmed} emails")

    # ── Step 4: Add phone/office for known faculty ────────────────────────────
    # Pithadia is a known test case - add manually as example
    # In reality these come from the directory which we can't scrape
    # The guessed email approach gives ~2000 emails which is most useful

    # ── Clean and save ─────────────────────────────────────────────────────────
    cleaned = [
        p for p in all_people
        if p.get("name") and
        "test" not in p.get("name", "").lower() and
        len(p.get("department", "")) < 200
    ]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False))

    log.info(f"\n✅ Saved {len(cleaned)} records → {OUTPUT}")
    log.info(f"With email: {sum(1 for p in cleaned if p.get('email'))}")
    log.info(f"With phone: {sum(1 for p in cleaned if p.get('phone'))}")

    # Show Pithadia
    for p in cleaned:
        if "pithadia" in p.get("name", "").lower():
            log.info(f"\nPithadia: {json.dumps(p, indent=2)}")
            break


if __name__ == "__main__":
    run()
