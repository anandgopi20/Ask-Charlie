"""
Ask Charlie — University of New Haven Chatbot Backend
Lightweight version: no heavy ML libraries, fits Railway free tier.
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

app = FastAPI(title="Ask Charlie API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ROOT           = Path(__file__).parent
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL          = "google/gemini-2.0-flash-exp:free"

SYSTEM_PROMPT = """You are Charlie, the friendly AI assistant for the University of New Haven (UNH).
Help students with: academic calendars, campus buildings, IT support (UIS), and Career Development Center (CDC).
Rules:
- Be warm, concise, helpful. Use bullet points for lists.
- Use the provided context. Don't make up facts.
- For buildings always include the Google Maps link.
- Keep answers under 200 words.
- UIS phone: 203-932-7371 | CDC phone: 203-479-4858 | CDC email: careerdevelopmentcenter@newhaven.edu"""

_knowledge: list = []
_buildings: list = []
_ready = False

def clean_html(html):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","noscript","header","footer","nav"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()

def chunk_text(text, max_chars=1200):
    chunks, i = [], 0
    while i < len(text):
        end = min(len(text), i + max_chars)
        chunk = text[i:end].strip()
        if chunk: chunks.append(chunk)
        if end == len(text): break
        i = max(0, end - 100)
    return chunks

def keyword_search(query, k=5):
    words = set(re.findall(r'\w+', query.lower()))
    scored = []
    for doc in _knowledge:
        score = len(words & set(re.findall(r'\w+', doc["text"].lower())))
        if score > 0: scored.append((score, doc["text"]))
    scored.sort(reverse=True)
    return [t for _, t in scored[:k]]

def find_buildings(query):
    q = query.lower()
    hits = []
    for b in _buildings:
        nl = b["name"].lower()
        if any(w in nl for w in q.split() if len(w)>3) or any(w in q for w in nl.split() if len(w)>3):
            hits.append(b)
    return hits[:3]

@app.on_event("startup")
async def startup():
    global _ready
    # Load buildings CSV
    csv_path = ROOT / "data" / "buildings.csv"
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = row.get("building_name","").strip()
                if name:
                    maps = f"https://www.google.com/maps/search/?api=1&query={name.replace(' ','+')}+University+of+New+Haven+CT"
                    b = {"name":name,"category":row.get("category",""),"notes":row.get("notes",""),"maps":maps}
                    _buildings.append(b)
                    _knowledge.append({"text":f"Building: {name}. {row.get('category','')}. {row.get('notes','')} Maps: {maps}","type":"building"})
    # Load CDC
    cdc_path = ROOT / "data" / "cdc.md"
    if cdc_path.exists():
        for chunk in chunk_text(cdc_path.read_text(encoding="utf-8")):
            _knowledge.append({"text":chunk,"type":"cdc"})
    # Fetch UIS pages
    for url in [
        "https://studentsupport.newhaven.edu/mfa/",
        "https://studentsupport.newhaven.edu/canvas/",
        "https://studentsupport.newhaven.edu/network-connectivity/",
        "https://studentsupport.newhaven.edu/printing-on-campus/",
        "https://studentsupport.newhaven.edu/login-trouble/",
        "https://studentsupport.newhaven.edu/campus-card/",
        "https://www.newhaven.edu/academics/calendar/index.php",
    ]:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent":"AskCharlieBot/2.0"})
            r.raise_for_status()
            for chunk in chunk_text(clean_html(r.text)):
                _knowledge.append({"text":chunk,"type":"uis"})
            print(f"  OK {url}")
        except Exception as e:
            print(f"  FAIL {url}: {e}")
    _ready = True
    print(f"Ready — {len(_knowledge)} chunks")

class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.post("/chat")
async def chat(req: ChatRequest):
    if not OPENROUTER_KEY:
        raise HTTPException(500, "OPENROUTER_API_KEY not set")
    question = req.message.strip()
    buildings = find_buildings(question)
    bctx = ""
    if buildings:
        bctx = "Campus buildings:\n" + "\n".join(f"- {b['name']} ({b['category']}): {b['notes']} → {b['maps']}" for b in buildings) + "\n\n"
    hits = keyword_search(question, k=5)
    rctx = ("Context:\n" + "\n---\n".join(hits[:4])) if hits else ""
    ctx = bctx + rctx
    messages = [{"role":"system","content":SYSTEM_PROMPT}]
    for h in req.history[-6:]:
        messages.append({"role":h["role"],"content":h["content"]})
    messages.append({"role":"user","content":(f"{ctx}\n\nQuestion: {question}" if ctx else question)})

    async def generate():
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST","https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization":f"Bearer {OPENROUTER_KEY}","Content-Type":"application/json","HTTP-Referer":"https://newhaven.edu","X-Title":"Ask Charlie UNH"},
                json={"model":MODEL,"messages":messages,"max_tokens":512,"stream":True}) as resp:
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

@app.get("/health")
def health():
    return {"status":"ok","ready":_ready,"chunks":len(_knowledge)}

@app.get("/")
def root():
    return {"message":"Ask Charlie — UNH Assistant"}
