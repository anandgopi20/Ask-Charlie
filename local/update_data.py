"""
update_data.py — Ask Charlie Local Data Updater
Automatically updates:
  1. professors.json — from UNH directory
  2. courses.json    — from UNH catalog pages

Run: python3 update_data.py
Schedule: runs automatically every 7 days from main_local.py
"""

import requests, json, re, time, string, os
import urllib3
from bs4 import BeautifulSoup
from pathlib import Path

urllib3.disable_warnings()

DATA_DIR  = Path(__file__).parent / "data"
PROF_OUT  = DATA_DIR / "professors.json"
COURSE_OUT = DATA_DIR / "courses.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Known coordinator data ────────────────────────────────────────────────────
KNOWN_FACULTY = {
    "pithadia": {
        "title": "Assistant Professor of Practice; Coordinator M.S. Data Science; Coordinator B.S. Artificial Intelligence",
        "office": "Maxcy Hall, Room 327", "phone": "(203) 479-4875", "email": "DPithadia@newhaven.edu"
    },
    "behzadan": {
        "title": "Associate Professor; Coordinator M.S. Artificial Intelligence; Director SAIL Lab",
        "office": "Maxcy Hall 120A", "phone": "(203) 479-4723", "email": "VBehzadan@newhaven.edu"
    },
    "mekni": {
        "title": "Professor; Coordinator B.S. Cybersecurity",
        "office": "Echlin Hall 212A", "phone": "(203) 479-4874", "email": "MMekni@newhaven.edu"
    },
    "rossi, thomas": {
        "title": "Associate Professor of Practice; Coordinator B.S. Computer Science",
        "office": "Maxcy Hall 120B", "phone": "(203) 479-4728", "email": "TRossi@newhaven.edu"
    },
    "rusu": {
        "title": "Professor; Coordinator M.S. Computer Science",
        "office": "Maxcy Hall 120G", "phone": "(203) 932-7165", "email": "ARusu@newhaven.edu"
    },
    "duman": {
        "title": "Assistant Professor; Coordinator M.S. Business Analytics",
        "office": "Maxcy Hall 102-E", "phone": "(203) 479-4564", "email": "GDuman@newhaven.edu"
    },
    "nassar": {
        "title": "Assistant Professor, Computer Science",
        "office": "Echlin Hall 203", "phone": "(203) 932-7333", "email": "MNassar@newhaven.edu"
    },
    "sula": {
        "title": "Associate Professor of Practice; Co-Coordinator M.S. Data Science",
        "office": "ECECS Department", "phone": "Not listed", "email": "ASula@newhaven.edu"
    },
    "khare": {
        "title": "Assistant Professor, Computer Science; SARI Security and AI Lab",
        "office": "Maxcy Hall 120B", "phone": "(203) 479-4872", "email": "SKhare@newhaven.edu"
    },
    "martinez, christopher": {
        "title": "Associate Professor; Coordinator B.S. Electrical & Computer Engineering",
        "office": "Maxcy Hall 310", "phone": "(203) 931-2924", "email": "CMartinez@newhaven.edu"
    },
    "ozkul": {
        "title": "Associate Professor, Economics and Business Analytics",
        "office": "Orange Campus N125A", "phone": "(203) 479-4862", "email": "AOzkul@newhaven.edu"
    },
    "page, liberty": {
        "title": "Associate Professor of Practice, Computer Science",
        "office": "Echlin Hall 115B", "phone": "(203) 932-1037", "email": "LPage@newhaven.edu"
    },
}

# ── Faculty pages to scrape for phones and offices ───────────────────────────
FACULTY_PAGES = [
    "https://www.newhaven.edu/engineering/academic-departments/electrical-computer-engineering-computer-science-faculty.php",
    "https://www.newhaven.edu/engineering/graduate-programs/data-science/faculty.php",
    "https://www.newhaven.edu/engineering/graduate-programs/artificial-intelligence/faculty.php",
    "https://www.newhaven.edu/engineering/undergraduate-programs/computer-science/faculty.php",
    "https://www.newhaven.edu/business/graduate-programs/information-science/faculty.php",
]

# ── Course catalog data (manually maintained from catalog.newhaven.edu) ───────
COURSE_CATALOG = {
    "last_updated": "2026-04-17",
    "note": "Update by visiting catalog.newhaven.edu and pasting program data",
    "programs": {
        "data_science_ms": {
            "name": "Data Science, M.S.",
            "credits": 33,
            "stem": True,
            "coordinator": "Div Pithadia | DPithadia@newhaven.edu | (203) 479-4875 | Maxcy Hall 327",
            "catalog": "https://catalog.newhaven.edu/preview_program.php?catoid=34&poid=9596",
            "description": "Prepares students for opportunities in data science with strong foundation in data analysis, machine learning, AI, and statistical modeling. STEM designated.",
            "required_courses": [
                "DSCI 6001 - Math for Data Scientists",
                "DSCI 6002 - Introduction to Data Science",
                "DSCI 6003 - Machine Learning",
                "DSCI 6004 - Natural Language Processing",
                "DSCI 6007 - Distributed & Scalable Data Engineering",
                "DSCI 6011 - Deep Learning",
                "DSCI 6612 - Introduction to Artificial Intelligence"
            ],
            "capstone": [
                "DSCI 6010 - Data Science Internship",
                "DSCI 6051 - Data Science Capstone Project"
            ],
            "special_topics": [
                "CSCI 6680 - Computer Vision",
                "DSCI 6006 - Leadership and Entrepreneurism",
                "DSCI 6015 - AI and Cybersecurity",
                "DSCI 6653 - Bayesian Data Analysis",
                "ELEC 6101 - Autonomous Vehicles I: Modeling, Perception, State Estimation",
                "ELEC 6102 - Autonomous Vehicles II: AI-enabled Decision-making"
            ],
            "electives": [
                "BANL 6310 - Data Visualization and Communication",
                "BANL 6600 - Power BI and Dashboarding",
                "CMBI 6620 - Bioinformatics",
                "CSCI 6624 - Advanced Database Systems",
                "CSCI 6627 - Distributed Database Systems",
                "CSCI 6634 - Cryptography and Data Security",
                "CSCI 6638 - Small-Scale Digital Forensic Science",
                "CSCI 6646 - Introduction to Computer Security",
                "ECON 6635 - Business Forecasting",
                "EGRM 6611 - Decision Making Under Uncertainty",
                "INDE 6620 - Optimization and Applications",
                "INDE 6645 - Data Analytics",
                "INDE 6647 - Supply Chain Analytics and Resilience",
                "PUBH 6680 - Health Analytics"
            ],
            "prerequisite": "DSCI 6602 - Introduction to Programming for Data Science (may be waived)",
            "course_plan": {
                "semester_1": ["DSCI 6001", "DSCI 6002", "DSCI 6612"],
                "semester_2": ["DSCI 6003", "DSCI 6007", "Elective/Special Topic"],
                "semester_3": ["DSCI 6004", "DSCI 6011", "Elective/Special Topic"],
                "semester_4": ["DSCI 6010 or DSCI 6051"]
            }
        },
        "artificial_intelligence_ms": {
            "name": "Artificial Intelligence, M.S.",
            "credits": 30,
            "stem": True,
            "coordinator": "Vahid Behzadan | VBehzadan@newhaven.edu | (203) 479-4723 | Maxcy Hall 120A",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=34",
            "description": "Focuses on AI theory, machine learning, deep learning, and real-world AI applications. STEM designated.",
            "required_courses": [
                "CSCI 6612 - Introduction to Artificial Intelligence",
                "CSCI 6640 - Machine Learning",
                "CSCI 6641 - Deep Learning",
                "CSCI 6642 - Reinforcement Learning",
                "CSCI 6643 - AI Ethics and Policy"
            ]
        },
        "computer_science_ms": {
            "name": "Computer Science, M.S.",
            "credits": 30,
            "stem": True,
            "coordinator": "Adrian Rusu | ARusu@newhaven.edu | (203) 932-7165 | Maxcy Hall 120G",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=34",
            "description": "Advanced graduate program in computer science covering algorithms, systems, and software engineering. STEM designated."
        },
        "cybersecurity_bs": {
            "name": "Cybersecurity, B.S.",
            "credits": 120,
            "stem": True,
            "coordinator": "Mehdi Mekni | MMekni@newhaven.edu | (203) 479-4874 | Echlin Hall 212A",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=33",
            "description": "Comprehensive undergraduate program covering network security, digital forensics, cryptography, and ethical hacking."
        },
        "computer_science_bs": {
            "name": "Computer Science, B.S.",
            "credits": 120,
            "stem": True,
            "coordinator": "Thomas Rossi | TRossi@newhaven.edu | (203) 479-4728 | Maxcy Hall 120B",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=33",
            "description": "Core computer science undergraduate program with tracks in software development, AI, and systems."
        },
        "artificial_intelligence_bs": {
            "name": "Artificial Intelligence, B.S.",
            "credits": 120,
            "stem": True,
            "coordinator": "Div Pithadia | DPithadia@newhaven.edu | (203) 479-4875 | Maxcy Hall 327",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=33",
            "description": "Undergraduate AI program covering machine learning, neural networks, and AI applications. STEM designated."
        },
        "electrical_computer_engineering_bs": {
            "name": "Electrical & Computer Engineering, B.S.",
            "credits": 128,
            "stem": True,
            "coordinator": "Christopher Martinez | CMartinez@newhaven.edu | (203) 931-2924 | Maxcy Hall 310",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=33",
            "description": "Covers circuits, systems, embedded systems, and computer engineering fundamentals."
        },
        "business_analytics_ms": {
            "name": "Business Analytics, M.S.",
            "credits": 30,
            "stem": True,
            "coordinator": "Gazi Duman | GDuman@newhaven.edu | (203) 479-4564 | Maxcy Hall 102-E",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=34",
            "description": "Combines data analytics with business strategy for data-driven decision making. STEM designated."
        },
        "mba": {
            "name": "Business Administration, M.B.A.",
            "credits": 36,
            "stem": False,
            "coordinator": "Pompea College of Business | business@newhaven.edu | (203) 932-7000",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=34",
            "description": "Comprehensive MBA covering finance, marketing, operations, and leadership."
        },
        "criminal_justice_ms": {
            "name": "Criminal Justice, M.S.",
            "credits": 36,
            "stem": False,
            "coordinator": "Henry C. Lee College | leecollege@newhaven.edu | (203) 932-7000",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=34",
            "description": "Advanced study in criminal justice policy, research methods, and law enforcement."
        },
        "forensic_science_ms": {
            "name": "Forensic Science, M.S.",
            "credits": 36,
            "stem": True,
            "coordinator": "Henry C. Lee College | leecollege@newhaven.edu | (203) 932-7000",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=34",
            "description": "Graduate forensic science with concentrations in crime scene investigation and laboratory analysis."
        },
        "national_security_ms": {
            "name": "National Security, M.S.",
            "credits": 36,
            "stem": False,
            "coordinator": "Henry C. Lee College | leecollege@newhaven.edu | (203) 932-7000",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=34",
            "description": "Covers homeland security, intelligence, policy, and emergency management."
        },
        "health_administration_mha": {
            "name": "Health Administration, M.H.A.",
            "credits": 36,
            "stem": False,
            "coordinator": "School of Health Sciences | health@newhaven.edu | (203) 932-7000",
            "catalog": "https://catalog.newhaven.edu/index.php?catoid=34",
            "description": "Healthcare management, policy, and administration graduate program."
        }
    }
}


# ═══════════════════════════════════════════════════════
# PART 1: Update Professors
# ═══════════════════════════════════════════════════════

def guess_email(name: str) -> str:
    parts = name.split(",")
    if len(parts) < 2: return ""
    last  = re.sub(r"[^a-z]", "", parts[0].strip().lower())
    first = re.sub(r"[^a-z]", "", parts[1].strip().lower().split()[0]) if parts[1].strip() else ""
    return f"{first[0]}{last}@newhaven.edu" if first and last else ""

def get_form_tokens(session) -> dict:
    url = "https://newhaven-web-01.newhaven.edu/ad/addata.aspx"
    r = session.get(url, headers=HEADERS, timeout=15, verify=False)
    soup = BeautifulSoup(r.text, "html.parser")
    return {f: (soup.find("input", {"name": f}) or {}).get("value", "")
            for f in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]}

def parse_grid(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "GridView2"})
    if not table: return []
    people = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4: continue
        name  = cells[1].get_text(strip=True)
        dept  = cells[2].get_text(strip=True)
        title = cells[3].get_text(strip=True)
        if name and name != "-":
            people.append({"name": name, "department": dept, "title": title})
    return people

def scrape_faculty_pages(professors: list) -> list:
    """Scrape faculty pages for phones and offices"""
    email_to_prof = {}
    for p in professors:
        if p.get("email"):
            email_to_prof[p["email"].lower()] = p

    for url in FACULTY_PAGES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            text = soup.get_text("\n")

            # Find emails and nearby info
            email_re  = re.compile(r'([A-Za-z]\w+@newhaven\.edu)', re.IGNORECASE)
            phone_re  = re.compile(r'\(203\)\s*\d{3}[-\s]\d{4}')
            office_re = re.compile(
                r'((?:Maxcy|Echlin|Bergami|Sheffield|Bixler|Dodds|Buckman|York|Marvin|Orange)\s+(?:Hall|Campus)[\w\s,]+\d+\w*)',
                re.IGNORECASE
            )
            lines = [l.strip() for l in text.split("\n") if l.strip()]

            for i, line in enumerate(lines):
                em = email_re.search(line)
                if not em: continue
                email = em.group(1).lower()
                if email not in email_to_prof: continue
                p = email_to_prof[email]
                nearby = lines[max(0,i-5):i+5]
                for nl in nearby:
                    pm = phone_re.search(nl)
                    om = office_re.search(nl)
                    if pm and not p.get("phone"): p["phone"] = pm.group()
                    if om and not p.get("office"): p["office"] = om.group().strip()

            print(f"  ✅ Scraped {url.split('/')[-2]}")
            time.sleep(1)
        except Exception as e:
            print(f"  ⚠️  {url.split('/')[-2]}: {e}")

    return professors

def apply_known_coordinators(professors: list) -> list:
    """Apply hardcoded coordinator data"""
    updated = 0
    for p in professors:
        name = (p.get("name") or "").lower()
        for key, info in KNOWN_FACULTY.items():
            if key in name:
                for field in ["title","office","phone","email"]:
                    if info.get(field): p[field] = info[field]
                updated += 1
                break
    print(f"  ✅ Applied coordinator fixes to {updated} professors")
    return professors

def update_professors():
    print("\n" + "="*50)
    print("📚 STEP 1: Updating professors from UNH directory")
    print("="*50)

    BASE = "https://newhaven-web-01.newhaven.edu/ad/addata.aspx"
    session = requests.Session()
    all_people = []
    seen = set()

    for letter in string.ascii_uppercase:
        try:
            tokens = get_form_tokens(session)
            payload = {
                **tokens,
                "txtName": "", "txtLastName": letter,
                "Dept": "-1", "btnSearch": "SEARCH"
            }
            r = session.post(BASE, data=payload, headers=HEADERS, timeout=25, verify=False)
            people = parse_grid(r.text)
            added = 0
            for p in people:
                name = p["name"].strip()
                if not name or name in seen: continue
                seen.add(name)
                email = guess_email(name)
                if email: p["email"] = email
                all_people.append(p)
                added += 1
            print(f"  '{letter}' → +{added} (total: {len(all_people)})")
        except Exception as e:
            print(f"  '{letter}' failed: {e}")
        time.sleep(0.3)

    # Enrich with faculty page data
    print("\n📡 Scraping faculty pages for phones and offices...")
    all_people = scrape_faculty_pages(all_people)

    # Apply known coordinators
    print("\n📌 Applying coordinator fixes...")
    all_people = apply_known_coordinators(all_people)

    # Clean
    cleaned = [p for p in all_people
               if p.get("name")
               and "test" not in (p.get("name","")).lower()
               and len(p.get("department","")) < 200]

    DATA_DIR.mkdir(exist_ok=True)
    PROF_OUT.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False))

    print(f"\n✅ Saved {len(cleaned)} professors → {PROF_OUT}")
    print(f"   With email:  {sum(1 for p in cleaned if p.get('email'))}")
    print(f"   With phone:  {sum(1 for p in cleaned if p.get('phone'))}")
    print(f"   With office: {sum(1 for p in cleaned if p.get('office'))}")


# ═══════════════════════════════════════════════════════
# PART 2: Update Courses
# ═══════════════════════════════════════════════════════

def update_courses():
    print("\n" + "="*50)
    print("📖 STEP 2: Updating courses catalog")
    print("="*50)

    # Load existing if it exists
    if COURSE_OUT.exists():
        existing = json.loads(COURSE_OUT.read_text())
        # Merge — keep existing manual data, update timestamp
        existing["last_updated"] = time.strftime("%Y-%m-%d")
        # Add any new programs from COURSE_CATALOG
        for key, prog in COURSE_CATALOG["programs"].items():
            if key not in existing.get("programs", {}):
                existing.setdefault("programs", {})[key] = prog
                print(f"  ✅ Added new program: {prog['name']}")
        COURSE_OUT.write_text(json.dumps(existing, indent=2))
        print(f"✅ Updated existing courses.json ({len(existing['programs'])} programs)")
    else:
        DATA_DIR.mkdir(exist_ok=True)
        COURSE_OUT.write_text(json.dumps(COURSE_CATALOG, indent=2))
        print(f"✅ Created courses.json ({len(COURSE_CATALOG['programs'])} programs)")


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    print("🚀 Ask Charlie Data Updater")
    print(f"📁 Data directory: {DATA_DIR}")

    if "--courses-only" in sys.argv:
        update_courses()
    elif "--professors-only" in sys.argv:
        update_professors()
    else:
        update_professors()
        update_courses()

    print("\n✅ All done! Restart Charlie to use updated data.")
    print("   Run: python3 main_local.py")
