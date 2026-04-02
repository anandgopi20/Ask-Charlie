"""
Ask Charlie — University of New Haven Chatbot Backend
Free deployment on Railway. Uses Claude API + in-memory ChromaDB.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
import chromadb
import requests
import yaml
import re
import os
import json
import pandas as pd
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer
from pathlib import Path

# ── App setup ────────────────────────────────────────────────────────────────
app = FastAPI(title="Ask Charlie API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to newhaven.edu in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ───────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).parent
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL          = "google/gemini-2.0-flash-exp:free"   # free model via OpenRouter
EMBED_MODEL   = "sentence-transformers/all-MiniLM-L6-v2"
COLLECTION    = "charlie_docs"

SYSTEM_PROMPT = """You are Charlie, the friendly AI assistant for the University of New Haven (UNH).
You help students, faculty, and visitors with questions about:
- Academic calendars, schedules, and deadlines
- Campus buildings and locations
- UIS (IT support): WiFi, Canvas, MFA, printing, software
- Career Development Center (CDC): internships, Handshake, resume help, CPT/OPT

Rules:
- Be warm, helpful, and concise. Use bullet points for lists.
- If you don't know something, say so and suggest they contact the relevant office.
- Always use the provided context from UNH sources. Don't make up facts.
- For building locations, include a Google Maps link when possible.
- Keep answers under 200 words unless the question genuinely needs more detail.
- Never answer questions unrelated to UNH."""

DATA_SOURCES = [
    "https://studentsupport.newhaven.edu/canvas/",
    "https://studentsupport.newhaven.edu/campus-card/",
    "https://studentsupport.newhaven.edu/support-we-offer/",
    "https://studentsupport.newhaven.edu/network-connectivity/",
    "https://studentsupport.newhaven.edu/mfa/",
    "https://studentsupport.newhaven.edu/login-trouble/",
    "https://studentsupport.newhaven.edu/printing-on-campus/",
    "https://studentsupport.newhaven.edu/computer-labs/",
    "https://studentsupport.newhaven.edu/software/microsoft-365/",
    "https://studentsupport.newhaven.edu/computer-specs/",
    "https://www.newhaven.edu/academics/calendar/index.php",
]

CDC_CONTENT = """
# UNH Career Development Center (CDC)
## Contact
Email: careerdevelopmentcenter@newhaven.edu | Phone: 203-479-4858
## Handshake
Used for job/internship search, scheduling appointments, career fairs, resume review.
## Resources
Resume writing guides, cover letter guide, LinkedIn guide, Big Interview (mock interviews).
## International Students
CPT / OPT / STEM OPT resources, international-friendly employer listings.
"""

# ── Globals (loaded once on startup) ─────────────────────────────────────────
_chroma_client  = None
_chroma_col     = None
_embed_model    = None
_buildings_df   = None
_index_built    = False

# ── Text helpers ─────────────────────────────────────────────────────────────
def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text("\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def chunk_text(text: str, max_chars=1500, overlap=150):
    chunks, i = [], 0
    while i < len(text):
        end = min(len(text), i + max_chars)
        chunks.append(text[i:end].strip())
        if end == len(text):
            break
        i = max(0, end - overlap)
    return [c for c in chunks if c]

# ── Index building ────────────────────────────────────────────────────────────
def build_index():
    global _chroma_client, _chroma_col, _embed_model, _index_built

    print("⚙️  Building knowledge index…")
    _embed_model   = SentenceTransformer(EMBED_MODEL)
    _chroma_client = chromadb.Client()          # in-memory; persists for server lifetime
    _chroma_col    = _chroma_client.create_collection(COLLECTION)

    all_texts, all_metas = [], []

    # 1. UIS web pages
    for url in DATA_SOURCES:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "AskCharlieBot/2.0"})
            r.raise_for_status()
            text = clean_html(r.text)
            for chunk in chunk_text(text):
                all_texts.append(chunk)
                all_metas.append({"source": url, "type": "uis"})
            print(f"  ✓ {url}")
        except Exception as e:
            print(f"  ✗ {url}: {e}")

    # 2. CDC content
    for chunk in chunk_text(CDC_CONTENT):
        all_texts.append(chunk)
        all_metas.append({"source": "Career Development Center", "type": "cdc"})

    # 3. Buildings CSV
    csv_path = ROOT / "data" / "buildings.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path).fillna("")
        for _, row in df.iterrows():
            text = f"Building: {row.get('building_name','')}. Category: {row.get('category','')}."
            all_texts.append(text)
            all_metas.append({"source": "Campus Buildings", "type": "building",
                               "building_name": str(row.get("building_name", ""))})

    if not all_texts:
        print("⚠️  No documents indexed.")
        return

    print(f"  Embedding {len(all_texts)} chunks…")
    embeddings = _embed_model.encode(all_texts, batch_size=64, show_progress_bar=False).tolist()
    ids = [f"doc_{i}" for i in range(len(all_texts))]
    _chroma_col.add(ids=ids, documents=all_texts, metadatas=all_metas, embeddings=embeddings)
    _index_built = True
    print(f"✅ Index ready — {len(all_texts)} chunks")

# ── Buildings lookup ──────────────────────────────────────────────────────────
def load_buildings():
    global _buildings_df
    csv_path = ROOT / "data" / "buildings.csv"
    if csv_path.exists() and _buildings_df is None:
        _buildings_df = pd.read_csv(csv_path).fillna("")
        _buildings_df["name_lower"] = _buildings_df["building_name"].str.lower()

def find_buildings(query: str):
    load_buildings()
    if _buildings_df is None:
        return []
    q = query.lower()
    hits = _buildings_df[_buildings_df["name_lower"].apply(lambda n: n in q or q in n)]
    out = []
    for _, row in hits.head(3).iterrows():
        name = row["building_name"]
        out.append({
            "name": name,
            "category": row.get("category", ""),
            "maps": f"https://www.google.com/maps/search/?api=1&query={name.replace(' ', '+')}+University+of+New+Haven"
        })
    return out

# ── RAG retrieval ─────────────────────────────────────────────────────────────
def retrieve(question: str, k=5):
    if not _index_built:
        return []
    emb = _embed_model.encode([question]).tolist()[0]
    res = _chroma_col.query(query_embeddings=[emb], n_results=k)
    docs  = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    return list(zip(docs, metas))

# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    build_index()

# ── Request model ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list = []   # [{"role": "user"|"assistant", "content": "..."}]

# ── Chat endpoint (streaming) ─────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    if not OPENROUTER_KEY:
        raise HTTPException(500, "OPENROUTER_API_KEY not set")

    question = req.message.strip()

    # Building lookup shortcut
    buildings = find_buildings(question)
    building_ctx = ""
    if buildings:
        lines = [f"- {b['name']} ({b['category']}) → {b['maps']}" for b in buildings]
        building_ctx = "Relevant campus buildings:\n" + "\n".join(lines) + "\n\n"

    # RAG context
    hits = retrieve(question, k=5)
    rag_ctx = ""
    if hits:
        snippets = [doc[:800] for doc, _ in hits[:4]]
        rag_ctx = "Context from UNH sources:\n" + "\n---\n".join(snippets)

    context_block = building_ctx + rag_ctx

    # Build messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in req.history[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})

    user_content = question
    if context_block:
        user_content = f"{context_block}\n\nStudent question: {question}"
    messages.append({"role": "user", "content": user_content})

    # Stream from OpenRouter (free tier)
    async def generate():
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "HTTP-Referer": "https://charlie.newhaven.edu",
                    "X-Title": "Ask Charlie - UNH",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": messages,
                    "max_tokens": 512,
                    "stream": True,
                },
            ) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        parsed = json.loads(data)
                        text = parsed["choices"][0]["delta"].get("content", "")
                        if text:
                            yield f"data: {json.dumps({'text': text})}\n\n"
                    except Exception:
                        continue
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "index_built": _index_built}

@app.get("/")
def root():
    return {"message": "Ask Charlie API — University of New Haven"}
