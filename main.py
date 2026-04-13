"""
Ask Charlie — University of New Haven AI Chatbot
Production-ready | OpenRouter (Gemini 2.5 Flash Lite) | Credit-efficient
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx, requests, re, os, json, csv, threading, time as time_module
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime

app = FastAPI(title="Ask Charlie — UNH AI Assistant")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ROOT           = Path(__file__).parent
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL          = os.environ.get("MODEL", "openrouter/auto")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are Charlie, the official AI assistant for the University of New Haven (UNH).
You help students, faculty, staff, and visitors 24/7 with accurate, friendly answers.

RESPONSE RULES:
- NEVER use citation numbers [1][2][3] anywhere — not in text, not next to URLs
- Keep URLs clean — no brackets or numbers after them
- Use bullet points and bold for lists
- Keep answers under 300 words
- Be warm, friendly, and helpful
- Only use provided context — never invent facts
- Always include Google Maps link for buildings
- End professor/program responses with one helpful follow-up question
- If unsure: say so clearly and give the right contact info

KEY UNH CONTACTS:
- Main office: (203) 932-7000 | newhaven.edu
- IT/UIS Help: (203) 932-7371 | uis@newhaven.edu
- Career Development (CDC): (203) 479-4858 | careerdevelopmentcenter@newhaven.edu
- Admissions: admissions@newhaven.edu | (203) 932-7319
- Financial Aid: finaid@newhaven.edu | (203) 932-7315
- Health Center: (203) 932-7079
- Registrar: registrar@newhaven.edu
- Campus Police: (203) 932-7014 (emergency: 911)
- Library: (203) 932-7170
- Housing: (203) 932-7042

PROFESSOR CARD FORMAT (always use this when showing a person):
👤 **[Full Name]**
🏫 **Department:** [Department]
📌 **Title:** [Title]
📞 **Phone:** [Phone or "Not listed"]
🏢 **Office:** [Office or "Not listed"]
📧 **Email:** [email]
🔗 **Directory:** https://www.newhaven.edu/directory/index.php

UNH DINING (always available — use this when asked about food):
🍽️ **Bartels & Stoud Dining Hall (The Marketplace)**
• Hours: Mon-Fri 7am-8pm | Sat-Sun 10am-7pm
• Location: Bartels Campus Center
• 11 stations: breakfast, deli, pizza/pasta, grill, vegan & allergy-free options

🍽️ **Jazzman's Cafe and Bakery**
• Hours: Mon-Fri 7am-6pm | Location: Bartels Campus Center
• Coffee, pastries, sandwiches

🍽️ **Food on Demand (FoD)**
• Hours: Vary seasonally | Location: Westside Hall
• Fixed menu, unlimited salad/dessert/beverages

🍽️ **Moe's Southwest Grill**
• Hours: Vary | Location: Bergami Hall
• Burritos, tacos, quesadillas

🍽️ **ReCharge Convenience Market (C Store)**
• Hours: 8am-2:30pm | Location: Sheffield Hall
• Snacks, groceries, dining dollars accepted

🍽️ **Smooth Haven**
• Hours: 8am-5:30pm | Smoothies and healthy options

🍽️ **Wow (Wings Over West Haven)**
• Hours: Vary | Location: Bergami Hall
• Wings, burgers, quesadillas, salads

For real-time menus and hours: https://newhaven.sodexomyway.com

UNH IT SUPPORT (always available):
- Canvas LMS: canvas.newhaven.edu
- MFA Setup: studentsupport.newhaven.edu/mfa/
- WiFi: Connect to "UNH-Secure" with your UNH credentials
- Password reset: studentsupport.newhaven.edu/login-trouble/
- Printing: studentsupport.newhaven.edu/printing-on-campus/
- IT Help Desk: (203) 932-7371 | uis@newhaven.edu
"""

# ── Data stores ───────────────────────────────────────────────────────────────
_knowledge:      list = []
_buildings:      list = []
_professors:     list = []
_events:         list = []
_ready                = False
_crawl_done           = False
_pages_crawled        = 0
_last_prof_update     = None

# ── UNH pages ─────────────────────────────────────────────────────────────────
UNH_PAGES = [
    ("academics",       "https://www.newhaven.edu/academics/index.php"),
    ("programs",        "https://www.newhaven.edu/academics/programs/index.php"),
    ("admissions",      "https://www.newhaven.edu/admissions/index.php"),
    ("undergrad",       "https://www.newhaven.edu/admissions/undergraduate/index.php"),
    ("grad",            "https://www.newhaven.edu/admissions/graduate/index.php"),
    ("financial-aid",   "https://www.newhaven.edu/admissions/financial-aid/index.php"),
    ("international",   "https://www.newhaven.edu/admissions/international/index.php"),
    ("student-life",    "https://www.newhaven.edu/student-life/index.php"),
    ("housing",         "https://www.newhaven.edu/student-life/living-on-campus/index.php"),
    ("health",          "https://www.newhaven.edu/student-life/health-wellness/index.php"),
    ("safety",          "https://www.newhaven.edu/student-life/public-safety/index.php"),
    ("cdc",             "https://www.newhaven.edu/student-life/career-development-center/index.php"),
    ("calendar",        "https://www.newhaven.edu/academics/calendar/index.php"),
    ("events",          "https://www.newhaven.edu/events/index.php"),
    ("about",           "https://www.newhaven.edu/about/index.php"),
    ("campus-maps",     "https://www.newhaven.edu/about/campus-locations/index.php"),
    ("parking",         "https://www.newhaven.edu/about/visitors/parking.php"),
    ("engineering",     "https://www.newhaven.edu/engineering/index.php"),
    ("business",        "https://www.newhaven.edu/business/index.php"),
    ("arts-sciences",   "https://www.newhaven.edu/arts-sciences/index.php"),
    ("lee-college",     "https://www.newhaven.edu/lee-college/index.php"),
    ("health-sciences", "https://www.newhaven.edu/health-sciences/index.php"),
    ("research",        "https://www.newhaven.edu/research/index.php"),
    ("veterans",        "https://www.newhaven.edu/veterans/index.php"),
    ("orientation",     "https://www.newhaven.edu/student-life/orientation/index.php"),
    ("commencement",    "https://www.newhaven.edu/commencement/index.php"),
    ("accessibility",   "https://www.newhaven.edu/student-life/accessibility-resources-center/index.php"),
    ("uis-mfa",         "https://studentsupport.newhaven.edu/mfa/"),
    ("uis-canvas",      "https://studentsupport.newhaven.edu/canvas/"),
    ("uis-wifi",        "https://studentsupport.newhaven.edu/network-connectivity/"),
    ("uis-printing",    "https://studentsupport.newhaven.edu/printing-on-campus/"),
    ("uis-login",       "https://studentsupport.newhaven.edu/login-trouble/"),
    ("ececs-faculty",   "https://www.newhaven.edu/engineering/academic-departments/electrical-computer-engineering-computer-science-faculty.php"),
    ("ds-faculty",      "https://www.newhaven.edu/engineering/graduate-programs/data-science/faculty.php"),
    ("ai-faculty",      "https://www.newhaven.edu/engineering/graduate-programs/artificial-intelligence/faculty.php"),
    ("cs-faculty",      "https://www.newhaven.edu/engineering/undergraduate-programs/computer-science/faculty.php"),
    ("business-faculty","https://www.newhaven.edu/business/graduate-programs/information-science/faculty.php"),
    ("ds-program",      "https://www.newhaven.edu/engineering/graduate-programs/data-science/index.php"),
    ("ai-program",      "https://www.newhaven.edu/engineering/graduate-programs/artificial-intelligence/index.php"),
]

# ── HTML helpers ──────────────────────────────────────────────────────────────
def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","noscript","header","footer",
                     "nav","aside","form","iframe","svg","button"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()

def chunk_text(text: str, max_chars: int = 800) -> list:
    chunks, i = [], 0
    while i < len(text):
        end = min(len(text), i + max_chars)
        chunk = text[i:end].strip()
        if len(chunk) > 80:
            chunks.append(chunk)
        if end == len(text):
            break
        i = max(0, end - 80)
    return chunks

def fetch_page(label: str, url: str):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent":"AskCharlieBot/3.0"})
        if r.status_code == 200:
            for chunk in chunk_text(clean_html(r.text)):
                _knowledge.append({"text": chunk, "type": label, "url": url})
    except:
        pass

# ── Live events ────────────────────────────────────────────────────────────────
def fetch_live_events():
    global _events
    try:
        r = requests.get("https://www.newhaven.edu/events/index.php",
                         timeout=10, headers={"User-Agent":"AskCharlieBot/3.0"})
        soup = BeautifulSoup(r.text, "lxml")
        events = []
        for item in soup.select(".event-item,.event,article.event,.events-list li")[:10]:
            title = item.select_one("h2,h3,h4,.event-title,.title")
            date  = item.select_one(".date,.event-date,time")
            loc   = item.select_one(".location,.venue,.event-location")
            link  = item.select_one("a")
            if title:
                events.append({
                    "title":    title.get_text(strip=True),
                    "date":     date.get_text(strip=True) if date else "See website",
                    "location": loc.get_text(strip=True) if loc else "UNH Campus",
                    "url":      ("https://www.newhaven.edu" + link["href"]
                                 if link and link.get("href","").startswith("/")
                                 else link["href"] if link else "https://www.newhaven.edu/events/")
                })
        _events = events
        print(f"Fetched {len(_events)} events")
    except Exception as e:
        print(f"Events fetch failed: {e}")

# ── Professor management ──────────────────────────────────────────────────────
def load_professors():
    global _professors, _last_prof_update
    path = ROOT / "data" / "professors.json"
    if path.exists():
        _professors = json.loads(path.read_text(encoding="utf-8"))
        _last_prof_update = datetime.now().strftime("%Y-%m-%d %H:%M")
        print(f"Loaded {len(_professors)} professors")
    else:
        _professors = []

def run_professor_scraper():
    scraper = ROOT / "scraper" / "scrape_unh_directory.py"
    if scraper.exists():
        import subprocess
        subprocess.run(["python3", str(scraper)], timeout=1800)
        load_professors()

def weekly_professor_update():
    while True:
        time_module.sleep(7 * 24 * 60 * 60)
        run_professor_scraper()

# ── Background fetch ──────────────────────────────────────────────────────────
def background_fetch():
    global _crawl_done, _pages_crawled
    fetch_live_events()
    for label, url in UNH_PAGES:
        fetch_page(label, url)
        _pages_crawled += 1
    _crawl_done = True
    print(f"Done: {_pages_crawled} pages, {len(_knowledge)} chunks")

# ── Smart keyword search ──────────────────────────────────────────────────────
def keyword_search(query: str, k: int = 5) -> list:
    words = set(re.findall(r'\w+', query.lower())) - {"what","how","does","the","is","are","a","an"}
    scored = [(len(words & set(re.findall(r'\w+', doc["text"].lower()))), doc["text"])
              for doc in _knowledge]
    scored = [(s, t) for s, t in scored if s > 0]
    scored.sort(reverse=True)
    return [t for _, t in scored[:k]]

def find_buildings(query: str) -> list:
    q = query.lower()
    return [b for b in _buildings
            if any(w in b["name"].lower() for w in q.split() if len(w) > 3)][:2]

# ── Professor search ──────────────────────────────────────────────────────────
PERSON_RE = re.compile(
    r"\b(professor|prof|dr\.?|doctor|faculty|coordinator|director|"
    r"instructor|lecturer|advisor|chair|dean|who is|contact|email|"
    r"phone|office|staff|teach|teaches|find)\b", re.IGNORECASE
)
STOPWORDS = {"who","is","the","a","an","for","of","in","at","me","tell","find",
             "get","what","are","does","do","his","her","their","this","that",
             "can","you","i","my","how","about","give","show","need","want","unh"}

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
    scored = sorted([(p, _score(p, terms)) for p in _professors], key=lambda x: -x[1])
    return [p for p, s in scored if s > 0][:5]

def professor_context(msg: str) -> str:
    if not PERSON_RE.search(msg):
        return ""
    results = search_professors(msg)
    if not results:
        return ""
    lines = ["[DIRECTORY RESULTS — show each as a formatted card]"]
    for p in results:
        lines.append(
            f"Name: {p.get('name','?')} | "
            f"Dept: {p.get('department','N/A')} | "
            f"Title: {p.get('title','N/A')} | "
            f"Phone: {p.get('phone','Not listed')} | "
            f"Office: {p.get('office','Not listed')} | "
            f"Email: {p.get('email','Not listed')} | "
            f"Profile: https://www.newhaven.edu/directory/index.php"
        )
    return "\n".join(lines)

EVENTS_RE = re.compile(
    r"\b(event|events|happening|schedule|calendar|activities|things to do|this week)\b",
    re.IGNORECASE
)

def events_context(msg: str) -> str:
    if not EVENTS_RE.search(msg) or not _events:
        return ""
    lines = ["[UPCOMING UNH EVENTS]"]
    for e in _events[:5]:
        lines.append(f"• {e['title']} | {e['date']} | {e['location']} | {e['url']}")
    return "\n".join(lines)

# ── Build efficient context (saves credits) ───────────────────────────────────
def build_context(question: str) -> str:
    parts = []

    # Professor search
    prof = professor_context(question)
    if prof:
        parts.append(prof)

    # Events
    evt = events_context(question)
    if evt:
        parts.append(evt)

    # Buildings
    buildings = find_buildings(question)
    if buildings:
        blines = ["Campus buildings:"]
        for b in buildings:
            blines.append(f"- {b['name']} ({b['category']}): {b['notes']} → {b['maps']}")
        parts.append("\n".join(blines))

    # Knowledge base (limit to save credits)
    hits = keyword_search(question, k=4)
    if hits:
        parts.append("Context:\n" + "\n---\n".join(hits[:3]))

    return "\n\n".join(parts)

# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global _ready

    # Buildings
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
        print(f"Loaded {len(_buildings)} buildings")

    # CDC
    cdc_path = ROOT / "data" / "cdc.md"
    if cdc_path.exists():
        for chunk in chunk_text(cdc_path.read_text(encoding="utf-8")):
            _knowledge.append({"text":chunk,"type":"cdc","url":""})

    # Professors
    load_professors()

    _ready = True
    print(f"=== Ask Charlie READY | {MODEL} via OpenRouter ===")

    threading.Thread(target=background_fetch, daemon=True).start()
    threading.Thread(target=weekly_professor_update, daemon=True).start()

# ── Chat ───────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.post("/chat")
async def chat(req: ChatRequest):
    question = req.message.strip()
    ctx      = build_context(question)

    messages = [{"role":"system","content":SYSTEM_PROMPT}]
    for h in req.history[-4:]:  # limit history to save credits
        messages.append({"role":h["role"],"content":h["content"]})
    messages.append({
        "role":    "user",
        "content": f"{ctx}\n\nQuestion: {question}" if ctx else question
    })

    async def generate():
        if not OPENROUTER_KEY:
            yield f"data: {json.dumps({'text': 'API key not configured. Please contact support.'})}\n\n"
            yield "data: [DONE]\n\n"
            return
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream(
                    "POST", OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_KEY}",
                        "Content-Type":  "application/json",
                        "HTTP-Referer":  "https://askcharlie.netlify.app",
                        "X-Title":       "Ask Charlie UNH",
                    },
                    json={
                        "model":       MODEL,
                        "messages":    messages,
                        "max_tokens":  500,   # limit to save credits
                        "stream":      True,
                        "temperature": 0.7,
                    },
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        err  = json.loads(body).get("error",{}).get("message","Unknown error")
                        print(f"OpenRouter error {resp.status_code}: {err}")
                        yield f"data: {json.dumps({'text': f'I am having trouble right now. Please try again or call (203) 932-7000.'})}\n\n"
                    else:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "): continue
                            d = line[6:].strip()
                            if d == "[DONE]": break
                            try:
                                t = json.loads(d)["choices"][0]["delta"].get("content","")
                                if t: yield f"data: {json.dumps({'text':t})}\n\n"
                            except: continue
        except Exception as e:
            print(f"Chat error: {e}")
            yield f"data: {json.dumps({'text': 'Sorry, I had trouble connecting. Please try again!'})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status":           "ok",
        "ready":            _ready,
        "model":            MODEL,
        "provider":         "OpenRouter",
        "crawl_done":       _crawl_done,
        "pages_crawled":    _pages_crawled,
        "knowledge_chunks": len(_knowledge),
        "professors":       len(_professors),
        "buildings":        len(_buildings),
        "events":           len(_events),
        "last_prof_update": _last_prof_update,
    }

@app.get("/debug")
async def debug():
    if not OPENROUTER_KEY:
        return {"error": "OPENROUTER_API_KEY not set"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}",
                         "Content-Type": "application/json"},
                json={"model": MODEL,
                      "messages": [{"role":"user","content":"say hi"}],
                      "max_tokens": 10, "stream": False}
            )
            data = resp.json()
            ok   = resp.status_code == 200
            return {
                "model":    MODEL,
                "provider": "OpenRouter",
                "status":   resp.status_code,
                "working":  ok,
                "response": data.get("choices",[{}])[0].get("message",{}).get("content","") if ok else data.get("error",{}).get("message","")
            }
    except Exception as e:
        return {"error": str(e)}

@app.get("/professors/search")
def prof_search(q: str):
    return {"query": q, "results": search_professors(q)}

@app.get("/events")
def get_events():
    return {"events": _events}

@app.post("/professors/reload")
def prof_reload():
    load_professors()
    return {"message": f"Reloaded {len(_professors)} records"}

@app.post("/professors/refresh")
def prof_refresh():
    threading.Thread(target=run_professor_scraper, daemon=True).start()
    return {"message": "Scraper started — check /health in 15 mins"}

@app.get("/")
def root():
    return {
        "message":  "Ask Charlie — UNH AI Assistant",
        "ready":    _ready,
        "model":    MODEL,
        "provider": "OpenRouter",
        "docs":     "https://askcharlie.netlify.app"
    }
