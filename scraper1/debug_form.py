"""
Run this FIRST to find the real form field names:
  python3 scraper/debug_form.py
"""
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings()

BASE_URL = "https://newhaven-web-01.newhaven.edu/ad/addata.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

print("Fetching UNH directory page...")
resp = requests.get(BASE_URL, headers=HEADERS, timeout=15, verify=False)
soup = BeautifulSoup(resp.text, "html.parser")

print("\n=== ALL FORM INPUT FIELDS ===")
for inp in soup.find_all("input"):
    print(f"  name={inp.get('name')!r:60} type={inp.get('type')!r} value={str(inp.get('value',''))[:30]!r}")

print("\n=== ALL SELECT / DROPDOWN FIELDS ===")
for sel in soup.find_all("select"):
    print(f"  name={sel.get('name')!r}")
    for opt in sel.find_all("option")[:5]:
        print(f"    option value={opt.get('value')!r} text={opt.get_text(strip=True)!r}")

print("\n=== ALL BUTTONS ===")
for btn in soup.find_all(["button", "input"], type=["submit","button"]):
    print(f"  name={btn.get('name')!r} value={btn.get('value')!r} text={btn.get_text(strip=True)!r}")

# Now try a test search with letter A and print raw HTML snippet
print("\n=== TRYING TEST SEARCH FOR LAST NAME 'p' ===")
tokens = {}
for f in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]:
    el = soup.find("input", {"name": f})
    if el:
        tokens[f] = el.get("value", "")
        print(f"  Found token: {f}")
    else:
        print(f"  MISSING token: {f}")

# Try the search - we'll print raw HTML so we can see what comes back
session = requests.Session()
session.get(BASE_URL, headers=HEADERS, verify=False)  # get cookies

# Try common ASP.NET field name patterns
test_payloads = [
    # Pattern 1: ContentPlaceHolder1
    {**tokens,
     "ctl00$ContentPlaceHolder1$txtFirst": "",
     "ctl00$ContentPlaceHolder1$txtLast": "p",
     "ctl00$ContentPlaceHolder1$ddlDept": "",
     "ctl00$ContentPlaceHolder1$btnSearch": "SEARCH"},
    # Pattern 2: no prefix  
    {**tokens,
     "txtFirst": "",
     "txtLast": "p",
     "ddlDept": "",
     "btnSearch": "SEARCH"},
    # Pattern 3: MainContent
    {**tokens,
     "ctl00$MainContent$txtFirst": "",
     "ctl00$MainContent$txtLast": "p",
     "ctl00$MainContent$ddlDept": "",
     "ctl00$MainContent$btnSearch": "SEARCH"},
]

for i, payload in enumerate(test_payloads):
    print(f"\n--- Testing payload pattern {i+1} ---")
    try:
        r = session.post(BASE_URL, data=payload, headers=HEADERS, timeout=15, verify=False)
        # Print a snippet of the response to see if results came back
        snippet = r.text[2000:4000]  # middle section likely has results
        print(snippet[:800])
        # Check if it has typical result-like content
        if any(kw in r.text.lower() for kw in ["department", "pithadia", "professor", "email"]):
            print(f"\n✅ PATTERN {i+1} WORKS! Results found.")
        else:
            print(f"\n❌ Pattern {i+1}: no results detected")
    except Exception as e:
        print(f"  Error: {e}")
