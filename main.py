"""
Ask Charlie — University of New Haven Chatbot Backend
Starts instantly. Loads data fast. Crawls in background.
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
import threading
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urljoin, urlparse
from collections import deque

app = FastAPI(title="Ask Charlie API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

ROOT           = Path(__file__).parent
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL          = os.environ.get("MODEL", "openrouter/auto")

SYSTEM_PROMPT = """You are Charlie, the friendly AI assistant for the University of New Haven (UNH).
Help students, faculty, staff, and visitors with ANY question about UNH.

You know about: academic programs, admissions, tuition, financial aid, campus buildings,
student life, housing, dining, health & wellness, safety, IT support (Canvas/MFA/WiFi),
Career Development Center, library, graduate programs, international students,
research, athletics, alumni, faculty and staff contacts, and more.

Rules:
- Be warm, concise, helpful. Use bullet points for lists.
- Use provided context only. Never make up facts.
- For buildings include Google Maps links.
- Keep answers under 250 words.
- If unsure: newhaven.edu or (203) 932-7000

Key contacts:
- Main: (203) 932-7000 | UIS: 203-932-7371
- CDC: 203-479-4858 | careerdevelopmentcenter@newhaven.edu
- Admissions: admissions@newhaven.edu | Health: (203) 932-7079

When you see DIRECTORY RESULTS format each person as:
👤 **[Name]** | 🏫 **Dept:** [dept] | 📌 **Title:** [title] | 📞 **Phone:** [phone] | 🏢 **Office:** [office] | 📧 **Email:** [email]

If no match: "Try https://www.newhaven.edu/directory or call (203) 932-7000"
"""

# ── Data stores ──────────────────────────────────────────────────────────────
_knowledge:  list = []
_buildings:  list = []
_professors: list = []
_ready            = False
_crawl_done       = False
_pages_crawled    = 0

# ── UNH pages to fetch ───────────────────────────────────────────────────────
UNH_PAGES = [
    ("academics",        "https://www.newhaven.edu/academics/index.php"),
    ("programs",         "https://www.newhaven.edu/academics/programs/index.php"),
    ("admissions",       "https://www.newhaven.edu/admissions/index.php"),
    ("undergrad",        "https://www.newhaven.edu/admissions/undergraduate/index.php"),
    ("grad-admissions",  "https://www.newhaven.edu/admissions/graduate/index.php"),
    ("financial-aid",    "https://www.newhaven.edu/admissions/financial-aid/index.php"),
    ("international",    "https://www.newhaven.edu/admissions/international/index.php"),
    ("student-life",     "https://www.newhaven.edu/student-life/index.php"),
    ("housing",          "https://www.newhaven.edu/student-life/living-on-campus/index.php"),
    ("dining",           "https://www.newhaven.edu/dining"),
    ("health",           "https://www.newhaven.edu/student-life/health-wellness/index.php"),
    ("safety",           "https://www.newhaven.edu/student-life/public-safety/index.php"),
    ("cdc",              "https://www.newhaven.edu/student-life/career-development-center/index.php"),
    ("calendar",         "https://www.newhaven.edu/academics/calendar/index.php"),
    ("about",            "https://www.newhaven.edu/about/index.php"),
    ("campus-maps",      "https://www.newhaven.edu/about/campus-locations/index.php"),
    ("parking",          "https://www.newhaven.edu/about/visitors/parking.php"),
    ("engineering",      "https://www.newhaven.edu/engineering/index.php"),
    ("business",         "https://www.newhaven.edu/business/index.php"),
    ("arts-sciences",    "https://www.newhaven.edu/arts-sciences/index.php"),
    ("lee-college",      "https://www.newhaven.edu/lee-college/index.php"),
    ("health-sciences",  "https://www.newhaven.edu/health-sciences/index.php"),
    ("research",         "https://www.newhaven.edu/research/index.php"),
    ("library",          "https://www.newhaven.edu/student-life/index.php"),
    ("inclusion",        "https://www.newhaven.edu/inclusion/index.php"),
    ("veterans",         "https://www.newhaven.edu/veterans/index.php"),
    ("orientation",      "https://www.newhaven.edu/student-life/orientation/index.php"),
    ("uis-mfa",          "https://studentsupport.newhaven.edu/mfa/"),
    ("uis-canvas",       "https://studentsupport.newhaven.edu/canvas/"),
    ("uis-wifi",         "https://studentsupport.newhaven.edu/network-connectivity/"),
    ("uis-printing",     "https://studentsupport.newhaven.edu/printing-on-campus/"),
    ("uis-login",        "https://studentsupport.newhaven.edu/login-trouble/"),
]

# ── HTML helpers ─────────────────────────────────────────────────────────────
def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","noscript","header","footer",
                     "nav","aside","form","iframe","svg","button"]):
        tag.decompose()
    text = soup.get_text("\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def chunk_text(text: str, max_chars: int = 1000) -> list:
    chunks, i = [], 0
    while i < len(text):
        end = min(len(text), i + max_chars)
        chunk = text[i:end].strip()
        if len(chunk) > 100:
            chunks.append(chunk)
        if end == len(text):
            break
        i = max(0, end - 100)
    return chunks

def fetch_page(label: str, url: str):
    try:
        r = requests.get(url, timeout=10,
                         headers={"User-Agent": "AskCharlieBot/3.0"})
        if r.status_code == 200:
            for chunk in chunk_text(clean_html(r.text)):
                _knowledge.append({"text": chunk, "type": label, "url": url})
    except:
        pass

# ── Background thread for fetching pages ─────────────────────────────────────
def background_fetch():
    global _crawl_done, _pages_crawled
    for label, url in UNH_PAGES:
        fetch_page(label, url)
        _pages_crawled += 1
    _crawl_done = True
    print(f"Pages fetched: {_pages_crawled}, chunks: {len(_knowledge)}")

# ── Search ───────────────────────────────────────────────────────────────────
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

# ── Professor search ──────────────────────────────────────────────────────────
PERSON_RE = re.compile(
    r"\b(professor|prof|dr\.?|doctor|faculty|coordinator|director|"
    r"instructor|lecturer|advisor|chair|dean|who is|contact|email|"
    r"phone|office|staff|teach|teaches)\b", re.IGNORECASE
)
STOPWORDS = {"who","is","the","a","an","for","of","in","at","me","tell",
             "find","get","what","are","does","do","his","her","their",
             "this","that","can","you","i","my","how","about","give","show"}

def load_professors():
    global _professors
    path = ROOT / "data" / "professors.json"
    if path.exists():
        _professors = json.loads(path.read_text(encoding="utf-8"))
        print(f"Loaded {len(_professors)} professors")
    else:
        _professors = []

def _score(p: dict, terms: list) -> int:
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

def search_professors(query: str) -> list:
    if not _professors:
        return []
    terms = [w for w in re.sub(r"[^\w\s]","",query).lower().split()
             if w not in STOPWORDS and len(w) > 2]
    if not terms:
        return []
    scored = sorted([(p, _score(p, terms)) for p in _professors],
                    key=lambda x: -x[1])
    return [p for p, s in scored if s > 0][:5]

def professor_context(msg: str) -> str:
    if not PERSON_RE.search(msg):
        return ""
    results = search_professors(msg)
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

# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global _ready

    # Load buildings
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
                    _knowledge.append({"text":f"Building: {name}. "
                                              f"{row.get('category','')}. "
                                              f"{row.get('notes','')} Maps: {maps}",
                                       "type":"building","url":""})

    # Load CDC
    cdc_path = ROOT / "data" / "cdc.md"
    if cdc_path.exists():
        for chunk in chunk_text(cdc_path.read_text(encoding="utf-8")):
            _knowledge.append({"text":chunk,"type":"cdc","url":""})

    # Load professors
    load_professors()

    # Mark ready IMMEDIATELY — Railway healthcheck passes right away
    _ready = True
    print("=== Ask Charlie READY ===")

    # Fetch UNH pages in background thread (doesn't block)
    t = threading.Thread(target=background_fetch, daemon=True)
    t.start()

# ── Chat ───────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.post("/chat")
async def chat(req: ChatRequest):
    if not OPENROUTER_KEY:
        raise HTTPException(500, "OPENROUTER_API_KEY not set")

    question = req.message.strip()
    prof_ctx = professor_context(question)
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
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream(
                    "POST",
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://askcharlie.netlify.app",
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
                        if not line.startswith("data: "):
                            continue
                        d = line[6:].strip()
                        if d == "[DONE]":
                            break
                        try:
                            t = json.loads(d)["choices"][0]["delta"].get("content","")
                            if t:
                                yield f"data: {json.dumps({'text':t})}\n\n"
                        except:
                            continue
        except Exception as e:
            yield f"data: {json.dumps({'text': f'Error: {str(e)}'})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

# ── Endpoints ─────────────────────────────────────────────────────────────────
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
    return {"message": "Ask Charlie — UNH Assistant", "ready": _ready}
