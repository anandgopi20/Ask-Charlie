"""
Ask Charlie — University of New Haven Chatbot Backend
Full auto-crawler: discovers and reads ALL public pages on newhaven.edu automatically.
No manual URL lists. Just crawls the whole site like Google does.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
import requests
import re
import os
import json
import csv
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urljoin, urlparse
from collections import deque

app = FastAPI(title="Ask Charlie API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ROOT           = Path(__file__).parent
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL          = "google/gemini-2.0-flash-exp:free"

SYSTEM_PROMPT = """You are Charlie, the friendly and knowledgeable AI assistant for the University of New Haven (UNH).
You help students, faculty, staff, and visitors with ANY question about UNH.

You have knowledge about everything publicly available on newhaven.edu including:
- All academic programs, colleges, majors, and departments
- Admissions process, deadlines, tuition, financial aid, scholarships
- Campus buildings, locations, parking, and maps
- Student life: clubs, housing, dining, health & wellness, safety & security
- Career Development Center (CDC)
- IT support (UIS): Canvas, MFA, WiFi, printing, campus card, login help
- Library resources and hours
- Graduate and international student services
- Academic calendar, course schedules, registration
- Faculty and staff: name, title, department, phone, office, email
- Research labs, SURF program, grants
- Inclusion, veterans services, spiritual life, Myatt Center
- Athletics, alumni, campus events, news
- Parents and families information

Rules:
- Be warm, concise, and helpful
- Use bullet points for lists
- Always use the provided context — never make up facts
- For buildings, include the Google Maps link when available
- Keep answers under 250 words unless truly needed
- If unsure, direct to newhaven.edu or (203) 932-7000

Key contacts:
- Main: (203) 932-7000
- UIS (IT Support): 203-932-7371
- CDC: 203-479-4858 | careerdevelopmentcenter@newhaven.edu
- Admissions: admissions@newhaven.edu
- Health Services: (203) 932-7079
- Emergency: (203) 932-7070

When you receive DIRECTORY RESULTS in the context, format each person as:
👤 **[Full Name]**
🏫 **Department:** [Department]
📌 **Title:** [Title]
📞 **Phone:** [Phone or "Not listed"]
🏢 **Office:** [Building + Office or "Not listed"]
📧 **Email:** [email or "Not listed"]

If no match found: "I couldn't find that person. Try https://www.newhaven.edu/directory or call (203) 932-7000."
"""

# ─────────────────────────────────────────────────────────────────────────────
# Crawler config
# ─────────────────────────────────────────────────────────────────────────────
SEED_URLS = [
    "https://www.newhaven.edu/index.php",
    "https://www.newhaven.edu/academics/index.php",
    "https://www.newhaven.edu/admissions/index.php",
    "https://www.newhaven.edu/student-life/index.php",
    "https://www.newhaven.edu/about/index.php",
    "https://studentsupport.newhaven.edu/",
]

ALLOWED_DOMAINS = {
    "www.newhaven.edu",
    "newhaven.edu",
    "studentsupport.newhaven.edu",
}

# Skip these — not useful content
SKIP_PATTERNS = re.compile(
    r"\.(pdf|jpg|jpeg|png|gif|svg|css|js|ico|zip|doc|docx|xls|xlsx|mp4|mp3|webp)$"
    r"|/give/|/alumni/give|/_resources/|/img/|/lib/|/prod/"
    r"|logout|login|portal|mycharger|banner|self-service"
    r"|facebook\.com|twitter|instagram|linkedin|youtube|tiktok",
    re.IGNORECASE
)

MAX_PAGES   = 800    # crawl up to 800 pages
MAX_CHUNKS  = 6000   # memory safety limit

# ─────────────────────────────────────────────────────────────────────────────
# In-memory stores
# ─────────────────────────────────────────────────────────────────────────────
_knowledge:   list = []
_buildings:   list = []
_professors:  list = []
_ready              = False
_pages_crawled      = 0

# ─────────────────────────────────────────────────────────────────────────────
# HTML helpers
# ─────────────────────────────────────────────────────────────────────────────
def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer",
                     "nav", "aside", "form", "iframe", "svg", "button",
                     "meta", "link"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{3,}", " ", text)
    return text.strip()

def extract_links(html: str, base_url: str) -> list:
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript"):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        # Only follow links within allowed domains
        if parsed.netloc not in ALLOWED_DOMAINS:
            continue
        # Skip unwanted patterns
        if SKIP_PATTERNS.search(full):
            continue
        # Normalize — remove fragments and trailing slashes
        clean = parsed._replace(fragment="").geturl().rstrip("/")
        links.append(clean)
    return links

def chunk_text(text: str, max_chars: int = 1000) -> list:
    chunks, i = [], 0
    while i < len(text):
        end = min(len(text), i + max_chars)
        chunk = text[i:end].strip()
        if len(chunk) > 100:   # skip tiny chunks
            chunks.append(chunk)
        if end == len(text):
            break
        i = max(0, end - 100)
    return chunks

# ─────────────────────────────────────────────────────────────────────────────
# Web crawler
# ─────────────────────────────────────────────────────────────────────────────
def crawl_site():
    """
    BFS crawler — starts from seed URLs, follows every internal link,
    reads and chunks all page content into _knowledge.
    """
    global _pages_crawled

    visited = set()
    queue   = deque(SEED_URLS)
    session = requests.Session()
    session.headers["User-Agent"] = "AskCharlieBot/3.0 (UNH student project)"

    print(f"\nStarting crawler (max {MAX_PAGES} pages)...")

    while queue and _pages_crawled < MAX_PAGES and len(_knowledge) < MAX_CHUNKS:
        url = queue.popleft()

        # Normalize URL
        parsed = urlparse(url)
        norm   = parsed._replace(fragment="").geturl().rstrip("/")

        if norm in visited:
            continue
        visited.add(norm)

        try:
            resp = session.get(url, timeout=15, allow_redirects=True)
            if resp.status_code != 200:
                continue
            if "text/html" not in resp.headers.get("content-type", ""):
                continue

            html  = resp.text
            text  = clean_html(html)
            label = urlparse(url).path.strip("/").replace("/", "-") or "home"

            chunks = chunk_text(text)
            for chunk in chunks:
                _knowledge.append({"text": chunk, "type": label, "url": url})

            _pages_crawled += 1
            if _pages_crawled % 50 == 0 or _pages_crawled <= 5:
                print(f"  [{_pages_crawled}] {url} → {len(chunks)} chunks "
                      f"(total: {len(_knowledge)})")

            # Add discovered links to queue
            for link in extract_links(html, url):
                link_norm = urlparse(link)._replace(fragment="").geturl().rstrip("/")
                if link_norm not in visited:
                    queue.append(link)

        except Exception as e:
            pass   # silently skip failed pages

    print(f"Crawler done: {_pages_crawled} pages, {len(_knowledge)} chunks")

# ─────────────────────────────────────────────────────────────────────────────
# Knowledge search
# ─────────────────────────────────────────────────────────────────────────────
def keyword_search(query: str, k: int = 6) -> list:
    words = set(re.findall(r'\w+', query.lower()))
    scored = []
    for doc in _knowledge:
        score = len(words & set(re.findall(r'\w+', doc["text"].lower())))
        if score > 0:
            scored.append((score, doc["text"]))
    scored.sort(reverse=True)
    return [t for _, t in scored[:k]]

def find_buildings(query: str) -> list:
    q = query.lower()
    return [b for b in _buildings
            if any(w in b["name"].lower() for w in q.split() if len(w) > 3)
            or any(w in q for w in b["name"].lower().split() if len(w) > 3)][:3]

# ─────────────────────────────────────────────────────────────────────────────
# Professor search
# ─────────────────────────────────────────────────────────────────────────────
PERSON_RE = re.compile(
    r"\b(professor|prof|dr\.?|doctor|faculty|coordinator|director|instructor|"
    r"lecturer|advisor|adviser|chair|dean|who is|contact|email|phone|office|"
    r"staff|department head|teach|teaches|researcher|scientist)\b",
    re.IGNORECASE
)
STOPWORDS = {
    "who","is","the","a","an","for","of","in","at","me","tell","find","get",
    "what","are","does","do","his","her","their","this","that","can","you",
    "i","my","how","about","give","show","need","want",
}

def load_professors():
    global _professors
    path = ROOT / "data" / "professors.json"
    if path.exists():
        _professors = json.loads(path.read_text(encoding="utf-8"))
        print(f"  OK  [professors] {len(_professors)} records loaded")
    else:
        _professors = []
        print("  WARN [professors] No professors.json — run scraper/scrape_unh_directory.py")

def _prof_score(p: dict, terms: list) -> int:
    score = 0
    name  = (p.get("name") or "").lower()
    dept  = (p.get("department") or "").lower()
    title = (p.get("title") or "").lower()
    email = (p.get("email") or "").lower()
    for t in terms:
        if t in name:  score += 10
        if t in dept:  score += 6
        if t in title: score += 4
        if t in email: score += 2
    return score

def search_professors(query: str, top_k: int = 5) -> list:
    if not _professors:
        return []
    terms = [w for w in re.sub(r"[^\w\s]", "", query).lower().split()
             if w not in STOPWORDS and len(w) > 2]
    if not terms:
        return []
    scored = sorted(
        [(p, _prof_score(p, terms)) for p in _professors],
        key=lambda x: -x[1]
    )
    return [p for p, s in scored if s > 0][:top_k]

def build_professor_context(message: str) -> str:
    if not PERSON_RE.search(message):
        return ""
    results = search_professors(message)
    if not results:
        return ""
    lines = ["[DIRECTORY RESULTS — format each as a card]"]
    for p in results:
        lines.append(
            f"Name: {p.get('name','?')} | "
            f"Dept: {p.get('department','N/A')} | "
            f"Title: {p.get('title','N/A')} | "
            f"Phone: {p.get('phone','Not listed')} | "
            f"Building: {p.get('building','')} | "
            f"Office: {p.get('office','Not listed')} | "
            f"Email: {p.get('email','Not listed')}"
        )
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global _ready
    print("\n=== Ask Charlie — Starting up ===")

    # 1. Buildings CSV
    csv_path = ROOT / "data" / "buildings.csv"
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = row.get("building_name", "").strip()
                if name:
                    maps = (f"https://www.google.com/maps/search/?api=1&query="
                            f"{name.replace(' ','+')}+University+of+New+Haven+CT")
                    _buildings.append({
                        "name": name, "category": row.get("category",""),
                        "notes": row.get("notes",""), "maps": maps
                    })
                    _knowledge.append({
                        "text": f"Building: {name}. {row.get('category','')}. "
                                f"{row.get('notes','')} Maps: {maps}",
                        "type": "building", "url": ""
                    })
        print(f"  OK  [buildings] {len(_buildings)} loaded")

    # 2. CDC markdown
    cdc_path = ROOT / "data" / "cdc.md"
    if cdc_path.exists():
        for chunk in chunk_text(cdc_path.read_text(encoding="utf-8")):
            _knowledge.append({"text": chunk, "type": "cdc", "url": ""})
        print("  OK  [cdc.md] loaded")

    # 3. Professor directory
    load_professors()

    # 4. Crawl entire newhaven.edu automatically
    crawl_site()

    _ready = True
    print(f"\n=== Ready — {len(_knowledge)} chunks | "
          f"{len(_professors)} professors | "
          f"{_pages_crawled} pages crawled ===\n")

# ─────────────────────────────────────────────────────────────────────────────
# Chat
# ─────────────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.post("/chat")
async def chat(req: ChatRequest):
    if not OPENROUTER_KEY:
        raise HTTPException(500, "OPENROUTER_API_KEY not set")

    question = req.message.strip()

    # Context layers
    prof_ctx  = build_professor_context(question)
    buildings = find_buildings(question)
    bctx = ""
    if buildings:
        bctx = "Campus buildings:\n" + "\n".join(
            f"- {b['name']} ({b['category']}): {b['notes']} → {b['maps']}"
            for b in buildings) + "\n\n"

    hits = keyword_search(question, k=6)
    rctx = ("Context:\n" + "\n---\n".join(hits[:5])) if hits else ""
    ctx  = "\n\n".join(filter(None, [prof_ctx, bctx, rctx]))

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in req.history[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({
        "role": "user",
        "content": f"{ctx}\n\nQuestion: {question}" if ctx else question
    })

    async def generate():
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST", "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://newhaven.edu",
                    "X-Title": "Ask Charlie UNH",
                },
                json={
                    "model": MODEL,
                    "messages": messages,
                    "max_tokens": 512,
                    "stream": True,
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "): continue
                    d = line[6:].strip()
                    if d == "[DONE]": break
                    try:
                        t = json.loads(d)["choices"][0]["delta"].get("content","")
                        if t: yield f"data: {json.dumps({'text':t})}\n\n"
                    except: continue
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

# ─────────────────────────────────────────────────────────────────────────────
# Utility endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "ready": _ready,
        "pages_crawled": _pages_crawled,
        "knowledge_chunks": len(_knowledge),
        "professors": len(_professors),
        "buildings": len(_buildings),
    }

@app.get("/professors/search")
def prof_search(q: str):
    return {"query": q, "results": search_professors(q)}

@app.post("/professors/reload")
def prof_reload():
    load_professors()
    return {"message": f"Reloaded {len(_professors)} records"}

@app.get("/")
def root():
    return {"message": "Ask Charlie — UNH Assistant", "ready": _ready}
