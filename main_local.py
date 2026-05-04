"""
Ask Charlie — Local Version
  ✅ Name gate — "Before you get to me, what's your name?" screen
  ✅ Personalized greeting — "Hey Anand, ask me anything!"
  ✅ Name remembered per session (data/memory.json)
  ✅ Structured file logging — logs/charlie.log (rotating 10 MB × 5)
  ✅ Query analytics — logs/queries.jsonl (one JSON line per request)
  ✅ Atomic memory writes (tmp → rename, no corruption on crash)
  ✅ Ollama retry with exponential backoff (3 attempts)
  ✅ /ready endpoint — 503 until vector index is built
  ✅ /hello endpoint — returns personalized greeting for frontend
  ✅ Query rewriting (LLM-assisted)
  ✅ Metadata filtering (professor/course/page/building/dining)
  ✅ Dynamic prompt builder (per intent)
  ✅ Intent + query type detection
  ✅ Confidence scoring + hedging
  ✅ Answer post-processing
  ✅ Natural guardrails
  ✅ Vector search (FAISS) + reranking (CrossEncoder)
  ✅ LRU caching + thread safety
  ✅ Weekly auto-update

Install deps:
  pip install sentence-transformers faiss-cpu numpy python-dotenv

Run:
  Terminal 1: ollama serve
  Terminal 2: python3 main_local.py
  Browser:    open index_local.html
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from functools import lru_cache
from threading import Lock
import httpx, requests, re, os, json, csv, time, threading, subprocess, logging
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime
import uuid  
import os
import json


try:
    from sentence_transformers import SentenceTransformer, CrossEncoder
    import numpy as np
    import faiss
    VECTOR_SEARCH = True
except ImportError:
    VECTOR_SEARCH = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT         = Path(__file__).parent
OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = os.environ.get("MODEL", "qwen2.5:14b")
MEMORY_FILE  = ROOT / "data" / "memory.json"
LOG_DIR      = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── Logging setup ─────────────────────────────────────────────────────────────
from logging.handlers import RotatingFileHandler

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

log = logging.getLogger("charlie")
log.setLevel(logging.INFO)

# 1. Console — see output in terminal as usual
_console = logging.StreamHandler()
_console.setFormatter(_fmt)
log.addHandler(_console)

# 2. Rotating file — charlie.log rolls at 10 MB, keeps 5 backups
_file = RotatingFileHandler(LOG_DIR / "charlie.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8")
_file.setFormatter(_fmt)
log.addHandler(_file)

# 3. Query analytics logger — one JSON line per question → queries.jsonl
_qlog = logging.getLogger("charlie.queries")
_qlog.setLevel(logging.INFO)
_qlog.propagate = False  # don't double-log into charlie.log
_qfile = RotatingFileHandler(LOG_DIR / "queries.jsonl", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
_qfile.setFormatter(logging.Formatter("%(message)s"))  # raw JSON lines only
_qlog.addHandler(_qfile)

def log_query(name: str, session_id: str, question: str, intent: str,
              confidence: str, response_ms: int):
    """Write one JSON line to logs/queries.jsonl after every answered question."""
    record = {
        "time":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "name":        name,
        "session_id":  session_id,
        "question":    question,
        "intent":      intent,
        "confidence":  confidence,
        "response_ms": response_ms,
    }
    _qlog.info(json.dumps(record))

app = FastAPI(title="Ask Charlie — Local v5")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Chunk types for metadata filtering ───────────────────────────────────────
CHUNK_PROFESSOR = "professor"
CHUNK_COURSE    = "course"
CHUNK_DINING    = "dining"
CHUNK_BUILDING  = "building"
CHUNK_IT        = "it"
CHUNK_CAREER    = "career"
CHUNK_ADMISSION = "admission"
CHUNK_PAGE      = "page"  # general web page

# ── Intent detection ──────────────────────────────────────────────────────────
INTENT_PATTERNS = {
    "professor":    re.compile(r"\b(professor|prof|dr\.?|faculty|coordinator|director|instructor|who is|who teaches|contact|email|office|phone|staff)\b", re.I),
    "course":       re.compile(r"\b(course|curriculum|class|subject|credit|requirement|elective|capstone|what do i study|dsci|csci|banl)\b", re.I),
    "program":      re.compile(r"\b(program|degree|major|ms |bs |mba|phd|graduate|undergraduate|apply|admission)\b", re.I),
    "dining":       re.compile(r"\b(dining|food|eat|meal|cafe|restaurant|menu|breakfast|lunch|dinner|hungry|bartels|jazzman|moe|wow)\b", re.I),
    "it_support":   re.compile(r"\b(canvas|mfa|wifi|password|login|email|vpn|portal|tech|computer|print|network)\b", re.I),
    "career":       re.compile(r"\b(job|career|internship|cpt|opt|resume|interview|handshake|employer|salary|hire|cdc)\b", re.I),
    "international":re.compile(r"\b(international|visa|f1|cpt|opt|stem|oiss|immigration|i.?20)\b", re.I),
    "financial":    re.compile(r"\b(tuition|fee|financial.?aid|scholarship|fafsa|cost|price|loan|grant)\b", re.I),
    "campus":       re.compile(r"\b(building|location|parking|map|where is|directions|campus|hall|center|maxcy|echlin|bergami)\b", re.I),
    "health":       re.compile(r"\b(health|counseling|therapy|medical|wellness|sick|doctor|appointment|mental)\b", re.I),
}

def classify_intent(question: str) -> str:
    for intent, pattern in INTENT_PATTERNS.items():
        if pattern.search(question):
            return intent
    return "general"

def detect_query_type(question: str) -> str:
    q = question.lower()
    if any(w in q for w in ["recommend","suggest","best","should i","which one","what would you"]):
        return "recommendation"
    if any(w in q for w in ["summary","overview","tell me about","explain","describe"]):
        return "summary"
    if any(w in q for w in ["also","what about","and then","more about","other","else"]):
        return "followup"
    return "fact"

# Intent → chunk type mapping for metadata filtering
INTENT_TO_CHUNK_TYPE = {
    "professor":     CHUNK_PROFESSOR,
    "course":        CHUNK_COURSE,
    "dining":        CHUNK_DINING,
    "it_support":    CHUNK_IT,
    "career":        CHUNK_CAREER,
    "international": CHUNK_CAREER,
    "financial":     CHUNK_ADMISSION,
    "program":       CHUNK_COURSE,
    "campus":        CHUNK_BUILDING,
    "health":        CHUNK_PAGE,
    "general":       None,  # search all
}

# ── Memory system ─────────────────────────────────────────────────────────────
_memory: dict = {}

def load_memory():
    global _memory
    if MEMORY_FILE.exists():
        try:
            _memory = json.loads(MEMORY_FILE.read_text())
            log.info(f"✅ Memory loaded ({len(_memory)} sessions)")
        except:
            _memory = {}

def save_memory():
    """Atomic write — write to .tmp first, then rename so a crash never corrupts the file."""
    MEMORY_FILE.parent.mkdir(exist_ok=True)
    tmp = MEMORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_memory, indent=2), encoding="utf-8")
    tmp.replace(MEMORY_FILE)

def get_user_memory(session_id: str) -> dict:
    return _memory.get(session_id, {
        "name":          None,   # set by name gate on first visit
        "topics_asked":  [],
        "program":       None,
        "first_seen":    None,
        "last_seen":     None,
        "question_count": 0,
    })

def update_memory(session_id: str, question: str, answer: str):
    mem = get_user_memory(session_id)
    now = datetime.now().isoformat()
    mem["last_seen"] = now
    if not mem.get("first_seen"):
        mem["first_seen"] = now
    mem["question_count"] = mem.get("question_count", 0) + 1

    prog = re.search(
        r"\b(data science|computer science|cybersecurity|artificial intelligence|"
        r"business analytics|forensic science|criminal justice|mba)\b",
        question.lower()
    )
    if prog and not mem.get("program"):
        mem["program"] = prog.group(1)

    intent = classify_intent(question)
    if intent not in mem["topics_asked"]:
        mem["topics_asked"].append(intent)
    if len(mem["topics_asked"]) > 15:
        mem["topics_asked"] = mem["topics_asked"][-15:]

    _memory[session_id] = mem
    save_memory()

# ── Dynamic prompt builder ────────────────────────────────────────────────────
BASE_IDENTITY = """You are Charlie, the University of New Haven's AI guide.
You're knowledgeable, friendly, and genuinely helpful — like a smart upperclassman who knows everything about UNH.

Core behavior:
- Give direct, natural answers. Don't sound like a bot reading from a manual.
- Vary your response length: short for simple questions, thorough for complex ones.
- Only ask follow-up questions when it genuinely helps the student.
- Never start every response with "Hello!" or end every response with "What else can I help you with?"
- When uncertain: say "I believe...", "As of my last update...", or "You'd want to confirm with [office]..."
- Use the [CONTEXT DATA] to answer — it contains real, verified UNH data.
- NEVER use citation numbers like [1], [2], [3].
- Don't repeat the question back to the student.

Key contacts (use when relevant):
Main: (203) 932-7000 | IT: (203) 932-7371 | CDC: (203) 479-4858
Admissions: admissions@newhaven.edu | Financial Aid: finaid@newhaven.edu
Health: (203) 932-7079 | OISS: oiss@newhaven.edu | (203) 932-7449"""

INTENT_PROMPTS = {
    "professor": """
For faculty questions, format info clearly:
👤 **Name** | 🏫 Dept | 📌 Title | 📞 Phone | 🏢 Office | 📧 Email
🔗 https://www.newhaven.edu/directory/index.php
Missing info → say "not listed in directory" naturally, not as an error.""",

    "course": """
For course/curriculum questions:
- List courses with codes (e.g. DSCI 6003 - Machine Learning)
- Mention coordinator and total credits
- Note STEM designation if applicable
- Point to catalog.newhaven.edu for full details
Be specific — students make real decisions based on this.""",

    "program": """
For program questions:
- Give a genuine sense of what the program is like
- Mention career outcomes if relevant
- Include application process if they seem prospective
- Note online vs on-ground availability
- Mention STEM designation if applicable""",

    "dining": """
For dining questions be specific:
- Hours and locations matter most
- Mention payment methods (dining dollars, meal swipes, credit card, Apple Pay)
- For today's menu: https://newhaven.sodexomyway.com
- Mention the Everyday app for meal planning
Dining: Bartels (main hall), Jazzman's (coffee), FoD (Westside kiosk), Moe's (burritos), ReCharge (C-store), Smooth Haven (smoothies), Wow (wings/burgers)""",

    "it_support": """
For IT questions:
- Give step-by-step help when needed
- Direct links: Canvas (canvas.newhaven.edu), MFA (studentsupport.newhaven.edu/mfa/)
- WiFi: connect to UNH-Secure with UNH credentials
- IT Help: (203) 932-7371 | uis@newhaven.edu
- Be practical and specific.""",

    "career": """
For career/CDC questions:
- Handshake is the main platform: newhaven.joinhandshake.com
- CDC: Charger Plaza Room 106 | M-F 8:30am-4:30pm | Walk-in: M-Th 1-3pm
- International: CPT/OPT through OISS (oiss@newhaven.edu | 203-932-7449)""",

    "international": """
For international student questions:
- CPT = work auth during studies | OPT = after graduation (12 months) | STEM OPT = +24 months
- OISS: oiss@newhaven.edu | (203) 932-7449
- Be clear — these are important visa/immigration matters
- Acknowledge if rules may have changed: "you'll want to confirm with OISS"
- F-1 students need I-20 maintained by DSO""",

    "financial": """
For financial/cost questions:
- Direct to finaid@newhaven.edu | (203) 932-7315 for personalized info
- Be honest that tuition changes — suggest checking current rates
- FAFSA early submission matters
- Mention merit scholarships exist but vary""",

    "health": """
For health/wellness questions:
- Counseling & Psychological Services: (203) 932-7079 | Schwartz Hall | M-F 8:30-4:30
- If ANY sign of distress, lead with support resources before information
- Emergency: Campus Police (203) 932-7014 or 911
- Health Center is separate from counseling""",

    "campus": """
For campus/location questions:
- Include Google Maps link when available
- Key buildings: Maxcy Hall (CS/ECECS), Echlin Hall (CS/Cybersecurity), Bergami (STEM), Sheffield, Bartels Campus Center
- Mention parking if relevant""",

    "general": "Be helpful, knowledgeable, and conversational. Point to the right resource if you don't have specific info.",
}

def build_dynamic_prompt(question: str, session_id: str) -> str:
    intent = classify_intent(question)
    query_type = detect_query_type(question)
    mem = get_user_memory(session_id)

    prompt = BASE_IDENTITY
    prompt += "\n" + INTENT_PROMPTS.get(intent, INTENT_PROMPTS["general"])

    if mem.get("program"):
        prompt += f"\n\nContext: This student has previously asked about the {mem['program']} program."
    if mem.get("question_count", 0) > 3:
        prompt += f"\n They've asked {mem['question_count']} questions — they're engaged, keep being helpful."

    if query_type == "recommendation":
        prompt += "\n\nThey want a recommendation — give a clear opinion or suggestion, not just facts."
    elif query_type == "followup":
        prompt += "\n\nThis is a follow-up — reference the conversation naturally."
    elif query_type == "summary":
        prompt += "\n\nThey want an overview — be organized but concise."

    prompt += """

Confidence guide (use naturally):
- Verified data in context → state directly
- May have changed → "As of last update..." or "I believe..."  
- Not in context → "I don't have that specific info, but [office/url] can help"

Dining hours (use when relevant):
🍽️ Bartels & Stoud: Mon-Fri 7am-8pm | Sat-Sun 10am-7pm | All payment methods
🍽️ Jazzman's: Mon-Fri 7am-6pm | Coffee & pastries
🍽️ Food on Demand: Westside Hall | Kiosk | Text notification
🍽️ Moe's: Bergami | Meal equiv $8 Mon-Fri 11am-3pm
🍽️ ReCharge: 8am-2:30pm | Sheffield Hall
🍽️ Smooth Haven: 8am-5:30pm | Smoothies
🍽️ Wow Cafe: Bergami | Wings/burgers | Meal equiv dinner 4:30-9pm
Allergen-free Simple Zone at Bartels (no milk, eggs, wheat, soy, sesame)
Real-time menus: https://newhaven.sodexomyway.com"""

    return prompt

# ── Confidence scoring ─────────────────────────────────────────────────────────
def score_confidence(context: str) -> str:
    if len(context) > 600: return "high"
    elif len(context) > 150: return "medium"
    return "low"

# ── Answer post-processing ────────────────────────────────────────────────────
REMOVE_PHRASES = [
    r"^Hello!?\s+I('d| would)?\s+(be happy to|certainly|gladly)\s+\w+[^.]*\.\s*",
    r"Is there anything else (I can help you with|you'd like to know)\??\s*$",
    r"What else can I (help|assist) you with\??\s*$",
    r"Feel free to ask (if you have|any) (more )?questions\.?\s*$",
    r"I hope (this|that) (helps|was helpful)!?\s*$",
    r"Let me know if you need (more|any) (information|help)\.?\s*$",
]

def clean_response(text: str) -> str:
    for p in REMOVE_PHRASES:
        text = re.sub(p, "", text, flags=re.I | re.MULTILINE)
    text = re.sub(r'\[\d+\]', '', text)
    return text.strip()

# ── Guardrails ────────────────────────────────────────────────────────────────
MENTAL_RE = re.compile(
    r"\b(suicide|kill myself|end my life|hurt myself|self.harm|want to die|hopeless|worthless|no reason to live)\b", re.I
)
HARMFUL_PATTERNS = [
    re.compile(r"\bhow to (make|build|create) (a )?bomb\b", re.I),
    re.compile(r"\bhow to (get|buy|sell|make) (cocaine|heroin|meth|fentanyl)\b", re.I),
    re.compile(r"\b(porn|nude|naked|xxx)\b", re.I),
    re.compile(r"\bhack (into|the) (canvas|unh system|database)\b", re.I),
]
OFFTOPIC_RE = re.compile(
    r"\b("
    r"netflix|hulu|disney plus|spotify|tiktok trend|"
    r"movie review|song lyrics|music video|"
    r"minecraft|fortnite|roblox|steam games|"
    r"bitcoin|crypto|stock tips|forex|lottery|"
    r"liquor store|bar near|bars near|club near|"
    r"casino|gambling|betting"
    r")\b", re.I
)

# Tier 2 — student-relevant off-campus questions
STUDENT_HELPFUL_RE = re.compile(
    r"\b(weather|temperature|raining|snow|"
    r"nearest|near campus|near unh|near me|close to campus|"
    r"starbucks|dunkin|coffee shop|pharmacy|cvs|walgreens|"
    r"urgent care|hospital near|doctor near|"
    r"parking near|gas station|grocery|supermarket|"
    r"uber|lyft|taxi|bus route|train|"
    r"restaurant near|food near|pizza near)\b", re.I
)

def check_student_helpful(msg: str) -> str | None:
    if STUDENT_HELPFUL_RE.search(msg.lower()):
        return (
            "That\'s a bit outside my UNH knowledge base, but here are some "
            "quick resources that can help:\n\n"
            "🌤️ **Weather:** [weather.com](https://weather.com) or just Google \'West Haven CT weather\'\n"
            "☕ **Nearby places:** [Google Maps](https://maps.google.com) — search near University of New Haven\n"
            "🏥 **Urgent care near UNH:** Yale New Haven Urgent Care, 150 Sargent Dr\n"
            "💊 **Pharmacy:** CVS at 490 Campbell Ave, West Haven (5 min from campus)\n"
            "🚗 **Rides:** Uber/Lyft work well around campus\n\n"
            "For anything UNH-specific — professors, programs, dining, IT — just ask! 😊"
        )
    return None

def check_guardrails(msg: str) -> str | None:
    m = msg.lower()
    if MENTAL_RE.search(m):
        return ("It sounds like you might be going through something difficult. "
                "Please reach out to UNH Counseling — they're genuinely there to help:\n\n"
                "📞 **(203) 932-7079** | 📍 Schwartz Hall | Mon-Fri 8:30am-4:30pm\n"
                "For immediate help: Campus Police **(203) 932-7014** or **911**\n\n"
                "You don't have to handle this alone. 💙")
    helpful = check_student_helpful(msg)
    if helpful:
        return helpful

    for pattern in HARMFUL_PATTERNS:
        if pattern.search(m):
            return ("That's not something I can help with. "
                    "If you have a UNH-related question — academics, campus life, programs, dining — I'm here for that.")
    if OFFTOPIC_RE.search(m):
        return ("That's a bit outside my wheelhouse! I'm built for UNH questions — "
                "programs, professors, dining, campus life, IT support. What can I help you with?")
    return None

# ── Thread-safe data stores ───────────────────────────────────────────────────
_knowledge:  list = []  # each item: {"text": str, "type": str, "url": str}
_buildings:  list = []
_professors: list = []
_courses:    dict = {}
_ready:      bool = False
_lock = Lock()

# ── Vector search with metadata filtering ────────────────────────────────────
_embed_model  = None
_reranker     = None
_faiss_index  = None
_faiss_chunks = []   # list of text strings
_faiss_types  = []   # parallel list of chunk types

def setup_vector_search():
    global _embed_model, _reranker
    if not VECTOR_SEARCH:
        log.warning("Install: pip install sentence-transformers faiss-cpu numpy")
        return
    try:
        log.info("Loading embedding model (all-MiniLM-L6-v2)...")
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        log.info("✅ Embedding model ready")
        try:
            _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            log.info("✅ Reranker ready")
        except:
            log.info("ℹ️ Reranker skipped (optional)")
    except Exception as e:
        log.warning(f"Vector setup failed: {e}")

def build_vector_index():
    global _faiss_index, _faiss_chunks, _faiss_types
    if not VECTOR_SEARCH or _embed_model is None: return
    with _lock:
        chunks = [(doc["text"], doc.get("type", CHUNK_PAGE)) for doc in _knowledge if "text" in doc]
    if not chunks: return
    texts = [c[0] for c in chunks]
    types = [c[1] for c in chunks]
    try:
        log.info(f"Building FAISS index ({len(texts)} chunks)...")
        embeddings = _embed_model.encode(texts, show_progress_bar=False, batch_size=32)
        index = faiss.IndexFlatL2(embeddings.shape[1])
        index.add(np.array(embeddings).astype("float32"))
        _faiss_index = index
        _faiss_chunks = texts
        _faiss_types  = types

        # Log type breakdown
        from collections import Counter
        counts = Counter(types)
        log.info(f"✅ FAISS ready: {len(texts)} chunks | " + " | ".join(f"{t}:{n}" for t,n in counts.items()))
    except Exception as e:
        log.warning(f"FAISS failed: {e}")

def vector_search(query: str, k: int = 8, filter_type: str = None) -> list:
    """
    Semantic search with optional metadata filtering.
    filter_type: if set, only searches chunks of that type.
    Falls back to keyword search if FAISS not ready.
    """
    if _faiss_index is None or _embed_model is None:
        return keyword_search(query, k)
    try:
        q_emb = _embed_model.encode([query]).astype("float32")

        if filter_type:
            # ── Metadata filtering ──
            # Get indices of chunks matching the filter type
            allowed = [i for i, t in enumerate(_faiss_types) if t == filter_type]
            if not allowed:
                # Fallback to unfiltered if no chunks of that type
                log.debug(f"No chunks of type '{filter_type}', searching all")
                _, indices = _faiss_index.search(q_emb, k)
                return [_faiss_chunks[i] for i in indices[0] if i < len(_faiss_chunks)]

            # Search full index first, then filter
            search_k = min(k * 4, len(_faiss_chunks))  # oversample for filtering
            _, indices = _faiss_index.search(q_emb, search_k)
            allowed_set = set(allowed)
            filtered = [_faiss_chunks[i] for i in indices[0]
                       if i < len(_faiss_chunks) and i in allowed_set]

            if len(filtered) < k:
                # Not enough filtered results — add general results
                others = [_faiss_chunks[i] for i in indices[0]
                         if i < len(_faiss_chunks) and i not in allowed_set]
                filtered = filtered + others[:k - len(filtered)]

            log.debug(f"Filtered search ({filter_type}): {len(filtered)} results")
            return filtered[:k]
        else:
            # Unfiltered search
            _, indices = _faiss_index.search(q_emb, k)
            return [_faiss_chunks[i] for i in indices[0] if i < len(_faiss_chunks)]

    except Exception as e:
        log.warning(f"Vector search failed: {e}")
        return keyword_search(query, k)

def rerank(query: str, docs: list, top_k: int = 4) -> list:
    if _reranker is None or not docs: return docs[:top_k]
    try:
        scores = _reranker.predict([[query, d] for d in docs])
        return [doc for doc, _ in sorted(zip(docs, scores), key=lambda x: -x[1])[:top_k]]
    except:
        return docs[:top_k]

# ── Query rewriting (LLM-assisted) ───────────────────────────────────────────
async def rewrite_query(question: str, intent: str) -> str:
    """
    Uses the LLM to expand/clarify vague queries before search.
    Examples:
      "who runs ds" → "Who is the coordinator for the Data Science MS program at UNH?"
      "mfa help" → "How do I set up Multi-Factor Authentication MFA at UNH?"
      "when food" → "What are the dining hall hours and food options at UNH?"
    """
    # Only rewrite short or vague queries (saves time on clear questions)
    words = question.strip().split()
    if len(words) >= 8:
        return question  # Already detailed enough

    # Check if it looks vague (no university-specific terms)
    specific_terms = re.compile(
        r"\b(unh|newhaven|professor|data science|computer science|cybersecurity|"
        r"canvas|mfa|pithadia|behzadan|financial aid|admissions|dining|bartels)\b", re.I
    )
    if specific_terms.search(question) and len(words) >= 4:
        return question  # Already specific enough

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"You are helping rewrite student questions for a University of New Haven (UNH) chatbot search engine.\n"
                            f"Rewrite this question to be more specific and searchable. Add relevant UNH context.\n"
                            f"Intent category: {intent}\n"
                            f"Original question: {question}\n"
                            f"Return ONLY the rewritten question, nothing else. Keep it under 20 words."
                        )
                    }],
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.1, "num_predict": 40}
                }
            )
            rewritten = resp.json()["message"]["content"].strip()
            # Clean up any quotes or prefixes
            rewritten = re.sub(r'^["\'](.*)["\']$', r'\1', rewritten)
            rewritten = re.sub(r'^(Rewritten:|Question:)\s*', '', rewritten, flags=re.I)
            log.info(f"Query rewrite: '{question}' → '{rewritten}'")
            return rewritten if rewritten else question
    except Exception as e:
        log.debug(f"Query rewrite failed (using original): {e}")
        return question

# ── Keyword search (fallback) ─────────────────────────────────────────────────
STOPWORDS = {"what","how","the","is","are","a","an","i","do","does","tell","me",
             "about","give","show","want","need","can","you","at","unh","new","haven",
             "please","help","find","get","university"}

def keyword_search(query: str, k: int = 4, filter_type: str = None) -> list:
    words = set(re.findall(r'\w+', query.lower())) - STOPWORDS
    if not words: return []
    with _lock:
        docs = [d for d in _knowledge if not filter_type or d.get("type") == filter_type] or list(_knowledge)
    scored = [(len(words & set(re.findall(r'\w+', d["text"].lower()))), d["text"]) for d in docs]
    return [t for s,t in sorted([(s,t) for s,t in scored if s>0], reverse=True)[:k]]

# ── HTML helpers ──────────────────────────────────────────────────────────────
def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","noscript","header","footer","nav","aside","form","iframe"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()

def chunk_text(text: str, max_chars: int = 800) -> list:
    chunks, i = [], 0
    while i < len(text):
        end = min(len(text), i + max_chars)
        chunk = text[i:end].strip()
        if len(chunk) > 80: chunks.append(chunk)
        if end == len(text): break
        i = max(0, end - 80)
    return chunks

def fetch_page(url: str, chunk_type: str = CHUNK_PAGE) -> bool:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent":"AskCharlieBot/4.0"})
        if r.status_code == 200:
            text = clean_html(r.text)
            with _lock:
                for chunk in chunk_text(text):
                    _knowledge.append({"text": chunk, "type": chunk_type, "url": url})
            return True
    except: pass
    return False

# ── Course search ─────────────────────────────────────────────────────────────
COURSE_RE = re.compile(
    r"\b(course|curriculum|class|credit|requirement|elective|capstone|"
    r"data science|computer science|cybersecurity|artificial intelligence|"
    r"machine learning|deep learning|nlp|mba|business analytics|forensic)\b", re.I
)

def search_courses(query: str) -> str:
    if not _courses or not COURSE_RE.search(query): return ""
    q = query.lower()
    results = []
    for key, prog in _courses.get("programs", {}).items():
        keywords = key.replace("_"," ").split() + prog.get("name","").lower().split()
        if any(kw in q for kw in keywords if len(kw)>3):
            lines = [f"PROGRAM: {prog.get('name')} | Credits: {prog.get('credits')} | STEM: {prog.get('stem',False)}"]
            if prog.get("coordinator"): lines.append(f"Coordinator: {prog['coordinator']}")
            if prog.get("description"): lines.append(f"About: {prog['description']}")
            for section, label in [("required_courses","Required"),("capstone","Capstone"),
                                    ("special_topics","Special Topics"),("electives","Electives")]:
                if prog.get(section):
                    lines.append(f"{label}:")
                    for c in prog[section][:6]: lines.append(f"  • {c}")
            if prog.get("catalog"): lines.append(f"Catalog: {prog['catalog']}")
            results.append("\n".join(lines))
    return "\n\n".join(results[:2]) if results else ""

# ── Professor search ──────────────────────────────────────────────────────────
PERSON_RE = re.compile(
    r"\b(professor|prof|dr\.?|faculty|coordinator|director|instructor|"
    r"lecturer|advisor|chair|dean|who is|contact|email|phone|office|staff|teach)\b", re.I
)
PROF_STOP = STOPWORDS | {"data","science","computer","course","program","ai","unh"}

def _score(p: dict, terms: list) -> int:
    s = 0
    name  = (p.get("name") or "").lower()
    dept  = (p.get("department") or "").lower()
    title = (p.get("title") or "").lower()
    email = (p.get("email") or "").lower()
    for t in terms:
        if t in name:  s += 10
        if t in dept:  s += 6
        if t in title: s += 4
        if t in email: s += 2
    if any(r in title for r in ["coordinator","director","chair","dean"]):
        if any(t in dept or t in title for t in terms): s += 5
    return s

def search_professors(query: str) -> list:
    with _lock: profs = list(_professors)
    if not profs: return []
    terms = [w for w in re.sub(r"[^\w\s]","",query).lower().split()
             if w not in PROF_STOP and len(w) > 2]
    if not terms: return []
    return [p for p,s in sorted([(p,_score(p,terms)) for p in profs],
                                  key=lambda x:-x[1]) if s>0][:5]

# ── Context builder ───────────────────────────────────────────────────────────
async def build_context(question: str, intent: str) -> str:
    """
    Builds context using:
    1. Query rewriting (LLM-assisted)
    2. Metadata filtering (type-specific search)
    3. Reranking
    """
    parts = []

    # ── Step 1: Query rewriting ──
    search_query = await rewrite_query(question, intent)

    # ── Step 2: Course catalog (structured lookup) ──
    cc = search_courses(question)  # Use original for structured lookup
    if cc: parts.append("COURSE CATALOG:\n" + cc)

    # ── Step 3: Professor lookup (structured) ──
    if PERSON_RE.search(question):
        results = search_professors(question)
        if results:
            lines = ["PROFESSOR DATA:"]
            for p in results:
                lines.append(
                    f"Name: {p.get('name','?')} | Dept: {p.get('department','N/A')} | "
                    f"Title: {p.get('title','N/A')} | Phone: {p.get('phone','Not listed')} | "
                    f"Office: {p.get('office','Not listed')} | Email: {p.get('email','Not listed')}"
                )
            parts.append("\n".join(lines))

    # ── Step 4: Building lookup ──
    q = question.lower()
    with _lock: buildings = list(_buildings)
    bldgs = [b for b in buildings if any(w in b["name"].lower() for w in q.split() if len(w)>3)][:2]
    if bldgs:
        parts.append("BUILDINGS:\n" + "\n".join(
            f"- {b['name']} ({b['category']}): {b['notes']} | Maps: {b['maps']}" for b in bldgs
        ))

    # ── Step 5: Semantic search with metadata filtering ──
    chunk_type = INTENT_TO_CHUNK_TYPE.get(intent)
    raw = vector_search(search_query, k=8, filter_type=chunk_type)

    if raw:
        hits = rerank(search_query, raw, top_k=4)
        parts.append("UNH CONTENT:\n" + "\n---\n".join(hits))

    ctx = "\n\n".join(parts)
    log.info(f"Context built: {len(ctx)} chars | type_filter={chunk_type} | rewritten='{search_query[:60]}'")
    return ctx[:4000]

# ── UNH pages with chunk types ────────────────────────────────────────────────
UNH_PAGES = [
    # Academics
    ("https://www.newhaven.edu/academics/index.php",           CHUNK_PAGE),
    ("https://www.newhaven.edu/academics/programs/index.php",  CHUNK_COURSE),
    # Data Science & AI
    ("https://www.newhaven.edu/engineering/graduate-programs/data-science/index.php",     CHUNK_COURSE),
    ("https://www.newhaven.edu/engineering/graduate-programs/data-science/faculty.php",   CHUNK_PROFESSOR),
    ("https://www.newhaven.edu/engineering/graduate-programs/artificial-intelligence/index.php",  CHUNK_COURSE),
    ("https://www.newhaven.edu/engineering/graduate-programs/artificial-intelligence/faculty.php", CHUNK_PROFESSOR),
    # CS & Engineering
    ("https://www.newhaven.edu/engineering/undergraduate-programs/computer-science/index.php", CHUNK_COURSE),
    ("https://www.newhaven.edu/engineering/academic-departments/electrical-computer-engineering-computer-science-faculty.php", CHUNK_PROFESSOR),
    # Business
    ("https://www.newhaven.edu/business/index.php",                         CHUNK_COURSE),
    # Admissions
    ("https://www.newhaven.edu/admissions/graduate/index.php",              CHUNK_ADMISSION),
    ("https://www.newhaven.edu/admissions/undergraduate/index.php",         CHUNK_ADMISSION),
    ("https://www.newhaven.edu/admissions/financial-aid/index.php",         CHUNK_ADMISSION),
    ("https://www.newhaven.edu/admissions/international/index.php",         CHUNK_CAREER),
    # Student Life
    ("https://www.newhaven.edu/student-life/career-development-center/index.php", CHUNK_CAREER),
    ("https://www.newhaven.edu/student-life/health-wellness/index.php",     CHUNK_PAGE),
    ("https://www.newhaven.edu/student-life/living-on-campus/index.php",    CHUNK_PAGE),
    # IT Support
    ("https://studentsupport.newhaven.edu/mfa/",                            CHUNK_IT),
    ("https://studentsupport.newhaven.edu/canvas/",                         CHUNK_IT),
    # Campus
    ("https://www.newhaven.edu/about/campus-locations/index.php",           CHUNK_BUILDING),
    ("https://www.newhaven.edu/academics/calendar/index.php",               CHUNK_PAGE),
    # Dining
    ("https://newhaven.sodexomyway.com/en-us/locations/the-marketplace-(dining-hall)-", CHUNK_DINING),
    ("https://newhaven.sodexomyway.com/en-us/meal-plan/meal-plan-options",  CHUNK_DINING),
]

# ── Data loading ──────────────────────────────────────────────────────────────
def load_data():
    global _professors, _courses

    with _lock:
        _knowledge.clear()
        _buildings.clear()
    log.info("Knowledge base cleared for fresh load")

    # Buildings
    csv_path = ROOT / "data" / "buildings.csv"
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = row.get("building_name","").strip()
                if name:
                    maps = f"https://www.google.com/maps/search/?api=1&query={name.replace(' ','+')}+University+of+New+Haven+CT"
                    with _lock:
                        _buildings.append({"name":name,"category":row.get("category",""),
                                           "notes":row.get("notes",""),"maps":maps})
                        _knowledge.append({"text":f"Building: {name}. {row.get('category','')}. {row.get('notes','')} Maps: {maps}",
                                           "type": CHUNK_BUILDING})
        log.info(f"✅ {len(_buildings)} buildings")

    # CDC
    cdc_path = ROOT / "data" / "cdc.md"
    if cdc_path.exists():
        with _lock:
            for chunk in chunk_text(cdc_path.read_text(encoding="utf-8")):
                _knowledge.append({"text": chunk, "type": CHUNK_CAREER})
        log.info("✅ CDC loaded")

    # Professors
    prof_path = ROOT / "data" / "professors.json"
    if prof_path.exists():
        _professors = json.loads(prof_path.read_text(encoding="utf-8"))
        log.info(f"✅ {len(_professors)} professors")

    # Courses
    course_path = ROOT / "data" / "courses.json"
    if course_path.exists():
        _courses = json.loads(course_path.read_text(encoding="utf-8"))
        with _lock:
            for key, prog in _courses.get("programs", {}).items():
                text = (f"Program: {prog.get('name')} | Credits: {prog.get('credits')} | "
                        f"Coordinator: {prog.get('coordinator','')} | {prog.get('description','')}")
                if prog.get("required_courses"):
                    text += " | Required: " + ", ".join(prog["required_courses"])
                _knowledge.append({"text": text, "type": CHUNK_COURSE})
            if _courses.get("all_programs"):
                _knowledge.append({"text": "UNH programs: " + " | ".join(_courses["all_programs"]),
                                   "type": CHUNK_COURSE})
        log.info(f"✅ {len(_courses.get('programs',{}))} programs")

# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global _ready
    setup_vector_search()
    load_data()
    load_memory()
    _ready = True
    log.info(f"=== Ask Charlie v4 READY | Model: {OLLAMA_MODEL} | Vector: {VECTOR_SEARCH} ===")

    def background():
        log.info(f"Fetching {len(UNH_PAGES)} UNH pages with type tags...")
        success = 0
        for url, chunk_type in UNH_PAGES:
            if fetch_page(url, chunk_type):
                success += 1
                log.info(f"  ✅ [{chunk_type:10}] {url.split('/')[-2] or url.split('/')[-1]}")
            time.sleep(0.5)

        from collections import Counter
        with _lock:
            type_counts = Counter(d.get("type", "unknown") for d in _knowledge)
        log.info(f"✅ {success}/{len(UNH_PAGES)} pages | {len(_knowledge)} total chunks")
        log.info("Type breakdown: " + " | ".join(f"{t}:{n}" for t,n in type_counts.items()))
        build_vector_index()

        while True:
            time.sleep(7 * 24 * 60 * 60)
            log.info("🔄 Weekly update...")
            try:
                subprocess.run(["python3", str(ROOT / "update_data.py")], timeout=3600)
                load_data()
                build_vector_index()
                log.info("✅ Weekly update done!")
            except Exception as e:
                log.error(f"Update failed: {e}")

    threading.Thread(target=background, daemon=True).start()

# ── Chat ──────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list = []
    session_id: str = "default"

@app.post("/chat")
async def chat(req: ChatRequest):
    question    = req.message.strip()
    intent      = classify_intent(question)
    request_id  = uuid.uuid4().hex[:8]
    start_time  = time.time()
    user_name   = get_user_memory(req.session_id).get("name") or "unknown"
    log.info(f"event=req_start id={request_id} user={user_name} session={req.session_id[:12]} intent={intent}")

    guard = check_guardrails(question)
    if guard:
        async def safe():
            yield f"data: {json.dumps({'text': guard, 'intent': 'safety'})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(safe(), media_type="text/event-stream")

    # Build context with query rewriting + metadata filtering
    ctx = await build_context(question, intent)
    confidence = score_confidence(ctx)
    system_prompt = build_dynamic_prompt(question, req.session_id)

    log.info(f"event=context id={request_id} chars={len(ctx)} filter={INTENT_TO_CHUNK_TYPE.get(intent)} confidence={confidence}")

    user_content = (
        f"[CONTEXT DATA]\n{ctx}\n[END CONTEXT]\n\n"
        f"Student question: {question}"
    ) if ctx else question

    if confidence == "low":
        user_content += "\n\n[Note: Limited data available — acknowledge uncertainty naturally]"

    messages = [{"role":"system","content":system_prompt}]
    for h in req.history[-6:]:
        messages.append({"role":h["role"],"content":h["content"]})
    messages.append({"role":"user","content":user_content})

    full_response = []
    t_start = time.time()

    async def generate():
        import asyncio
        last_error = None
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                log.warning(f"Ollama retry {attempt}/2...")
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    async with client.stream("POST", OLLAMA_URL,
                        json={
                            "model": OLLAMA_MODEL, "messages": messages,
                            "stream": True, "think": False,
                            "options": {"temperature":0.4,"num_predict":700,
                                        "repeat_penalty":1.1,
                                        "stop":["[CONTEXT DATA","Student question:"]}
                        }
                    ) as resp:
                        if resp.status_code != 200:
                            last_error = f"HTTP {resp.status_code}"
                            continue
                        async for line in resp.aiter_lines():
                            if not line: continue
                            try:
                                data = json.loads(line)
                                t = data.get("message",{}).get("content","")
                                if t:
                                    full_response.append(t)
                                    yield f"data: {json.dumps({'text': t, 'intent': intent})}\n\n"
                                if data.get("done"): break
                            except: continue
                        last_error = None
                        break
            except Exception as e:
                last_error = str(e)
                log.error(f"Ollama error (attempt {attempt+1}): {e}")

        if last_error:
            yield f"data: {json.dumps({'text':'Cannot reach Ollama. Is ollama serve running?'})}\n\n"
        yield "data: [DONE]\n\n"

        # Log analytics + update memory after streaming completes
        full_text  = "".join(full_response)
        elapsed_ms = int((time.time() - t_start) * 1000)
        latency    = time.time() - start_time
        mem        = get_user_memory(req.session_id)
        user_name  = mem.get("name") or "unknown"
        log.info(f"event=req_end id={request_id} user={user_name} latency={latency:.2f}s intent={intent} confidence={confidence}")
        if latency > 30:
            log.warning(f"event=slow_query id={request_id} user={user_name} latency={latency:.2f}s question=\"{question[:60]}\"")
        log_query(
            name=user_name,
            session_id=req.session_id,
            question=question,
            intent=intent,
            confidence=confidence,
            response_ms=elapsed_ms,
        )
        if full_text:
            threading.Thread(target=update_memory,
                           args=(req.session_id, question, full_text), daemon=True).start()

    return StreamingResponse(generate(), media_type="text/event-stream")

# ── Endpoints ─────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    session_id: str
    name: str

@app.post("/register")
def register(req: RegisterRequest):
    """Name gate — called once when user types their name for the first time."""
    name = req.name.strip().title()
    if not name:
        return {"error": "Name cannot be empty"}
    mem = get_user_memory(req.session_id)
    mem["name"] = name
    if not mem.get("first_seen"):
        mem["first_seen"] = datetime.now().isoformat()
    mem["last_seen"] = datetime.now().isoformat()
    _memory[req.session_id] = mem
    save_memory()
    log.info(f"New user registered: {name} (session={req.session_id})")
    return {"name": name, "message": f"Hey {name}, ask me anything!"}

@app.get("/hello")
def hello(session_id: str = "default"):
    """Returns personalized greeting — or signals frontend to show name gate."""
    mem = get_user_memory(session_id)
    name = mem.get("name")
    if name:
        count = mem.get("question_count", 0)
        if count == 0:
            greeting = f"Hey {name} — ask me anything about UNH. I'm Charlie."
        else:
            greeting = f"Hey {name}, welcome back! You've asked me {count} question{'s' if count != 1 else ''} so far."
    else:
        greeting = None  # frontend shows name gate
    return {"name": name, "greeting": greeting, "needs_name": name is None}

@app.get("/health")
def health():
    from collections import Counter
    with _lock:
        type_counts = dict(Counter(d.get("type","?") for d in _knowledge))
    return {
        "status": "ok", "version": "5.0",
        "model": OLLAMA_MODEL,
        "vector_search": VECTOR_SEARCH,
        "reranker": _reranker is not None,
        "query_rewriting": True,
        "metadata_filtering": True,
        "professors": len(_professors),
        "buildings": len(_buildings),
        "total_chunks": len(_knowledge),
        "chunks_by_type": type_counts,
        "programs": len(_courses.get("programs",{})),
        "memory_sessions": len(_memory),
        "ready": _ready,
    }

@app.get("/ready")
def ready():
    """503 until vector index is built — lets frontend show a loading state."""
    if _ready:
        return {"status": "ready", "model": OLLAMA_MODEL}
    from fastapi import HTTPException
    raise HTTPException(status_code=503, detail="Still loading knowledge base, please wait...")

@app.get("/memory/{session_id}")
def get_memory_endpoint(session_id: str):
    return get_user_memory(session_id)

@app.delete("/memory/{session_id}")
def clear_memory(session_id: str):
    if session_id in _memory:
        del _memory[session_id]
        save_memory()
    return {"cleared": session_id}

@app.get("/")
def root():
    return {"message": "Ask Charlie v5 — Name gate + Logging + Retry", "model": OLLAMA_MODEL, "ready": _ready}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
