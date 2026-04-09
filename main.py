"""
Ask Charlie — University of New Haven Chatbot Backend
Starts instantly, crawls newhaven.edu in background after startup.
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
import asyncio
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urljoin, urlparse
from collections import deque

app = FastAPI(title="Ask Charlie API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ROOT           = Path(__file__).parent
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL          = "openrouter/auto""

SYSTEM_PROMPT = """You are Charlie, the friendly and knowledgeable AI assistant for the University of New Haven (UNH).
You help students, faculty, staff, and visitors with ANY question about UNH.

You have knowledge about everything on newhaven.edu including:
- All academic programs, colleges, majors, and departments
- Admissions, tuition, financial aid, and scholarships
- Campus buildings, locations, parking, and maps
- Student life: clubs, housing, dining, health & wellness, safety
- Career Development Center (CDC)
- IT support (UIS): Canvas, MFA, WiFi, printing, campus card
- Library resources, graduate and international student services
- Academic calendar, course schedules
- Faculty and staff: name, title, department, phone, office, email
- Research labs, athletics, alumni, campus events

Rules:
- Be warm, concise, and helpful. Use bullet points for lists.
- Always use the provided context. Never make up facts.
- For buildings, include the Google Maps link when available.
- Keep answers under 250 words unless truly needed.
- If unsure, direct to newhaven.edu or (203) 932-7000.

Key contacts:
- Main: (203) 932-7000
- UIS: 203-932-7371
- CDC: 203-479-4858 | careerdevelopmentcenter@newhaven.edu
- Admissions: admissions@newhaven.edu
- Health Services: (203) 932-7079

When you receive DIRECTORY RESULTS in the context, format each person as:
👤 **[Full Name]**
🏫 **Department:** [Department]
📌 **Title:** [Title]
📞 **Phone:** [Phone or "Not listed"]
🏢 **Office:** [Building + Office or "Not listed"]
📧 **Email:** [email or "Not listed"]

If no match: "I couldn't find that person. Try https://www.newhaven.edu/directory or call (203) 932-7000."
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
    "https://www.newhaven.edu/engineering/index.php",
    "https://www.newhaven.edu/business/index.php",
    "https://www.newhaven.edu/arts-sciences/index.php",
    "https://www.newhaven.edu/lee-college/index.php",
    "https://www.newhaven.edu/health-sciences/index.php",
    "https://studentsupport.newhaven.edu/",
]

ALLOWED_DOMAINS = {"www.newhaven.edu", "newhaven.edu", "studentsupport.newhaven.edu"}

SKIP_PATTERNS = re.compile(
    r"\.(pdf|jpg|jpeg|png|gif|svg|css|js|ico|zip|doc|docx|xls|xlsx|mp4|mp3|webp)$"
    r"|/give/|/_resources/|/img/|/lib/|/prod/"
    r"|logout|login|portal|mycharger|banner|self-service"
    r"|facebook\.com|twitter|instagram|linkedin|youtube|tiktok",
    re.IGNORECASE
)

MAX_PAGES  = 600
MAX_CHUNKS = 5000

# ─────────────────────────────────────────────────────────────────────────────
# In-memory stores
# ─────────────────────────────────────────────────────────────────────────────
_knowledge:    list = []
_buildings:    list = []
_professors:   list = []
_ready               = False   # True after local data loaded (instant)
_crawl_done          = False   # True after background crawl finishes
_pages_crawled       = 0

# ─────────────────────────────────────────────────────────────────────────────
# HTML helpers
# ─────────────────────────────────────────────────────────────────────────────
def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","noscript","header","footer",
                     "nav","aside","form","iframe","svg","button"]):
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
        full   = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.netloc not in ALLOWED_DOMAINS:
            continue
        if SKIP_PATTERNS.search(full):
            continue
        clean = parsed._replace(fragment="").geturl().rstrip("/")
        links.append(clean)
    return links

def chunk_text(text: str, max_chars: int = 1000) -> list:
    chunks, i = [], 0
    while i < len(text):
        end   = min(len(text), i + max_chars)
        chunk = text[i:end].strip()
        if len(chunk) > 100:
            chunks.append(chunk)
        if end == len(text):
            break
        i = max(0, end - 100)
    return chunks

# ─────────────────────────────────────────────────────────────────────────────
# Background crawler — runs AFTER startup so Railway healthcheck passes
# ─────────────────────────────────────────────────────────────────────────────
async def background_crawl():
    global _pages_crawled, _crawl_done
    await asyncio.sleep(5)  # wait 5 seconds after startup before crawling

    visited = set()
    queue   = deque(SEED_URLS)
    session = requests.Session()
    session.headers["User-Agent"] = "AskCharlieBot/3.0 (UNH student project)"

    print("Background crawler started...")

    while queue and _pages_crawled < MAX_PAGES and len(_knowledge) < MAX_CHUNKS:
        url    = queue.popleft()
        parsed = urlparse(url)
        norm   = parsed._replace(fragment="").geturl().rstrip("/")

        if norm in visited:
            continue
        visited.add(norm)

        try:
            resp = session.get(url, timeout=10, allow_redirects=True)
            if resp.status_code != 200:
                continue
            if "text/html" not in resp.headers.get("content-type",""):
                continue

            html  = resp.text
            text  = clean_html(html)
            label = urlparse(url).path.strip("/").replace("/","-") or "home"

            for chunk in chunk_text(text):
                _knowledge.append({"text": chunk, "type": label, "url": url})

            _pages_crawled += 1
            if _pages_crawled % 100 == 0:
                print(f"Crawled {_pages_crawled} pages, {len(_knowledge)} chunks")

            for link in extract_links(html, url):
                link_norm = urlparse(link)._replace(fragment="").geturl().rstrip("/")
                if link_norm not in visited:
                    queue.append(link)

            await asyncio.sleep(0.1)  # small delay to not block event loop

        except Exception:
            continue

    _crawl_done = True
    print(f"Crawler done: {_pages_crawled} pages, {len(_knowledge)} chunks")

# ─────────────────────────────────────────────────────────────────────────────
# Search
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
        print(f"Loaded {len(_professors)} professor records")
    else:
        _professors = []
        print("No professors.json found")

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
    terms = [w for w in re.sub(r"[^\w\s]","",query).lower().split()
             if w not in STOPWORDS and len(w) > 2]
    if not terms:
        return []
    scored = sorted([(p, _prof_score(p, terms)) for p in _professors],
                    key=lambda x: -x[1])
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
# Startup — loads local data instantly, then kicks off background crawl
# ─────────────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global _ready

    print("=== Ask Charlie starting ===")

    # 1. Buildings CSV
    csv_path = ROOT / "data" / "buildings.csv"
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = row.get("building_name","").strip()
                if name:
                    maps = (f"https://www.google.com/maps/search/?api=1&query="
                            f"{name.replace(' ','+')}+University+of+New+Haven+CT")
                    _buildings.append({"name":name,"category":row.get("category",""),
                                       "notes":row.get("notes",""),"maps":maps})
                    _knowledge.append({"text":f"Building: {name}. {row.get('category','')}. "
                                              f"{row.get('notes','')} Maps: {maps}",
                                       "type":"building","url":""})
        print(f"Loaded {len(_buildings)} buildings")

    # 2. CDC markdown
    cdc_path = ROOT / "data" / "cdc.md"
    if cdc_path.exists():
        for chunk in chunk_text(cdc_path.read_text(encoding="utf-8")):
            _knowledge.append({"text":chunk,"type":"cdc","url":""})
        print("Loaded cdc.md")

    # 3. Professors
    load_professors()

    # 4. Mark ready IMMEDIATELY so Railway healthcheck passes
    _ready = True
    print("=== Ready (background crawl starting) ===")

    # 5. Start crawling in background — doesn't block startup
    asyncio.create_task(background_crawl())

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

    question  = req.message.strip()
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

    messages = [{"role":"system","content":SYSTEM_PROMPT}]
    for h in req.history[-6:]:
        messages.append({"role":h["role"],"content":h["content"]})
    messages.append({
        "role":"user",
        "content": f"{ctx}\n\nQuestion: {question}" if ctx else question
    })

    async def generate():
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST","https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization":f"Bearer {OPENROUTER_KEY}",
                         "Content-Type":"application/json",
                         "HTTP-Referer":"https://newhaven.edu",
                         "X-Title":"Ask Charlie UNH"},
                json={"model":MODEL,"messages":messages,"max_tokens":512,"stream":True},
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
        "crawl_done": _crawl_done,
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
    return {"message":"Ask Charlie — UNH Assistant","ready":_ready,"crawl_done":_crawl_done}
