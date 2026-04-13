"""
Run this script to update professors.json with real coordinator/faculty data
scraped from UNH's public faculty pages.

Run: python3 scraper/update_coordinators.py
"""

import json, re, requests, time
from pathlib import Path
from bs4 import BeautifulSoup

OUTPUT = Path(__file__).parent.parent / "data" / "professors.json"

# ── Real data from UNH website ────────────────────────────────────────────────
# Scraped from newhaven.edu faculty pages
KNOWN_FACULTY = {
    "pithadia": {
        "title": "Assistant Professor of Practice; Coordinator M.S. Data Science; Coordinator B.S. Artificial Intelligence",
        "office": "Maxcy Hall, Room 327",
        "phone": "(203) 479-4875",
        "email": "DPithadia@newhaven.edu"
    },
    "behzadan": {
        "title": "Associate Professor; Coordinator M.S. Artificial Intelligence",
        "office": "Maxcy Hall 120A",
        "phone": "(203) 479-4723",
        "email": "VBehzadan@newhaven.edu"
    },
    "martinez": {
        "title": "Associate Professor, Computer Engineering; Coordinator B.S. Electrical & Computer Engineering",
        "office": "Maxcy Hall 310",
        "phone": "(203) 931-2924",
        "email": "CMartinez@newhaven.edu"
    },
    "mekni": {
        "title": "Professor, Computer Science and Cybersecurity; Coordinator B.S. Cybersecurity",
        "office": "Echlin Hall 212A",
        "phone": "(203) 479-4874",
        "email": "MMekni@newhaven.edu"
    },
    "rossi": {
        "title": "Associate Professor of Practice; Coordinator B.S. Computer Science",
        "office": "Maxcy Hall 120B",
        "phone": "(203) 479-4728",
        "email": "TRossi@newhaven.edu"
    },
    "rusu": {
        "title": "Professor; Coordinator M.S. Computer Science",
        "office": "Maxcy Hall 120G",
        "phone": "(203) 932-7165",
        "email": "ARusu@newhaven.edu"
    },
    "marks": {
        "title": "Associate Professor of Practice; Executive Director Entrepreneurship and Innovation Program",
        "office": "Maxcy Hall 118B-5",
        "phone": "(203) 479-4150",
        "email": "BMarks@newhaven.edu"
    },
    "duman": {
        "title": "Assistant Professor; Chair Department of Economics and Business Analytics; Coordinator M.S. Business Analytics",
        "office": "Maxcy Hall, Office 102-E",
        "phone": "(203) 479-4564",
        "email": "GDuman@newhaven.edu"
    },
    "khare": {
        "title": "Assistant Professor, Computer Science; SARI Security and AI Lab",
        "office": "Maxcy Hall 120B",
        "phone": "(203) 479-4872",
        "email": "SKhare@newhaven.edu"
    },
    "nassar": {
        "title": "Assistant Professor, Computer Science",
        "office": "Echlin Hall 203",
        "phone": "(203) 932-7333",
        "email": "MNassar@newhaven.edu"
    },
    "ozkul": {
        "title": "Associate Professor, Department of Economics and Business Analytics",
        "office": "Orange Campus N125A",
        "phone": "(203) 479-4862",
        "email": "AOzkul@newhaven.edu"
    },
    "page": {
        "title": "Associate Professor of Practice, Computer Science",
        "office": "Echlin Hall 115B",
        "phone": "(203) 932-1037",
        "email": "LPage@newhaven.edu"
    },
}

# ── Faculty pages to scrape for more data ────────────────────────────────────
FACULTY_PAGES = [
    "https://www.newhaven.edu/engineering/academic-departments/electrical-computer-engineering-computer-science-faculty.php",
    "https://www.newhaven.edu/engineering/graduate-programs/data-science/faculty.php",
    "https://www.newhaven.edu/engineering/graduate-programs/artificial-intelligence/faculty.php",
    "https://www.newhaven.edu/business/graduate-programs/information-science/faculty.php",
    "https://www.newhaven.edu/engineering/undergraduate-programs/computer-science/faculty.php",
]

def scrape_faculty_page(url: str) -> list:
    """Scrape a UNH faculty page for contact info."""
    results = []
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "AskCharlieBot/3.0"})
        if r.status_code != 200:
            return results
        soup = BeautifulSoup(r.text, "lxml")
        text = soup.get_text()

        # Pattern: Name | Office | Phone | Email
        email_pattern = re.compile(r'([A-Z][a-z]+@newhaven\.edu)', re.IGNORECASE)
        phone_pattern  = re.compile(r'\(203\)\s*[\d\-]+')

        for email_match in email_pattern.finditer(text):
            email = email_match.group(1)
            username = email.split("@")[0].lower()
            results.append({"email": email, "username": username})

        print(f"  Found {len(results)} emails from {url.split('/')[-1]}")
    except Exception as e:
        print(f"  Failed {url}: {e}")
    return results


def update_professors():
    if not OUTPUT.exists():
        print(f"ERROR: {OUTPUT} not found!")
        return

    data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    print(f"Loaded {len(data)} professors")

    updated = 0

    # Step 1 — Apply known faculty data
    for p in data:
        name = (p.get("name") or "").lower()
        for key, info in KNOWN_FACULTY.items():
            if key in name:
                if info.get("title"):  p["title"]  = info["title"]
                if info.get("office"): p["office"] = info["office"]
                if info.get("phone"):  p["phone"]  = info["phone"]
                if info.get("email"):  p["email"]  = info["email"]
                updated += 1
                print(f"  ✅ Updated: {p['name']}")
                break

    print(f"\nStep 1 done: {updated} professors updated from known data")

    # Step 2 — Scrape faculty pages for more emails
    print("\nStep 2: Scraping faculty pages...")
    scraped_emails = {}
    for url in FACULTY_PAGES:
        results = scrape_faculty_page(url)
        for r in results:
            scraped_emails[r["username"]] = r["email"]
        time.sleep(1)

    print(f"Found {len(scraped_emails)} emails from faculty pages")

    # Match scraped emails to professors
    email_updated = 0
    for p in data:
        name = (p.get("name") or "").lower()
        parts = name.split(",")
        if len(parts) >= 2:
            last  = re.sub(r"[^a-z]", "", parts[0].strip())
            first = re.sub(r"[^a-z]", "", parts[1].strip().split()[0]) if parts[1].strip() else ""
            if first and last:
                username = f"{first[0]}{last}"
                if username in scraped_emails and not p.get("email"):
                    p["email"] = scraped_emails[username]
                    email_updated += 1

    print(f"Added {email_updated} emails from scraped pages")

    # Save
    OUTPUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\n✅ Saved {len(data)} professors to {OUTPUT}")
    print(f"With email: {sum(1 for p in data if p.get('email'))}")
    print(f"With phone: {sum(1 for p in data if p.get('phone'))}")
    print(f"With office: {sum(1 for p in data if p.get('office'))}")

    # Show Pithadia
    for p in data:
        if "pithadia" in (p.get("name") or "").lower():
            print(f"\nPithadia: {json.dumps(p, indent=2)}")
            break


if __name__ == "__main__":
    update_professors()
