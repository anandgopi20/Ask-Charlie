<div align="center">

<img src="https://img.shields.io/badge/version-5.0-002855?style=for-the-badge"/>
<img src="https://img.shields.io/badge/model-qwen2.5:14b-B8982E?style=for-the-badge"/>
<img src="https://img.shields.io/badge/rating-4.5%2F5-48bb78?style=for-the-badge"/>
<img src="https://img.shields.io/badge/built%20with-FastAPI-009688?style=for-the-badge"/>

# 🎓 Ask Charlie
### University of New Haven — AI Campus Assistant

> RAG-based AI assistant that answers real questions about UNH —  
> professors, courses, dining, campus, IT, visas, and more.  
> Runs fully locally with no cloud dependency.

**[🌐 Live Demo](https://askcharlie.netlify.app)** · **[📖 Wiki](https://github.com/anandgopi20/Ask-Charlie/wiki)** · **[📋 Feedback](https://github.com/anandgopi20/Ask-Charlie/wiki/Feedback)**

</div>

---

## What is Ask Charlie?

Ask Charlie is a RAG-based AI assistant built for UNH students at the
ECECS department. Unlike a general AI, Charlie only answers from
verified UNH data — real faculty records, the official course catalog,
live web pages, and campus resources.

If it doesn't know something, it says so and points you to the
right office — instead of guessing.

**Tested by 15 real UNH students, faculty, and staff — avg rating 4.5 / 5.**

---

## How it works
Student question
↓
Intent detected  (professor / course / dining / IT / career / campus)
↓
Query rewritten  (if short or vague)
↓
Vector search — FAISS finds 8 similar chunks
↓
Reranking — CrossEncoder picks best 4
↓
Language model generates answer
↓
Answer streams to student

→ Full explanation: [Architecture Wiki](https://github.com/anandgopi20/Ask-Charlie/wiki/Architecture)

---

## Features

| Feature | Status |
|---|---|
| RAG pipeline with FAISS vector search | ✅ |
| CrossEncoder reranking | ✅ |
| Intent detection (10 categories) | ✅ |
| Query rewriting via LLM | ✅ |
| Metadata filtering by chunk type | ✅ |
| Name gate — personalized greeting | ✅ |
| Structured logging (charlie.log + queries.jsonl) | ✅ |
| Ollama retry with exponential backoff | ✅ |
| Export conversation as .txt | ✅ |
| Dark mode toggle | ✅ |
| Thumbs up/down feedback logging | ✅ |
| Response time display | ✅ |

---

## Knowledge base

| Source | Contents |
|---|---|
| `professors.json` | 2,302 faculty & staff records |
| `buildings.csv` | 34 campus buildings |
| `cdc.md` | Career Development Center info |
| `courses.json` | 24 program curricula |
| Live web scraping | 22 UNH pages — refreshed on startup |
| Auto-refresh | Background thread re-scrapes every 7 days |

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/anandgopi20/Ask-Charlie.git
cd Ask-Charlie

# 2. Install dependencies
pip install -r requirements.txt

# 3. Pull the model
ollama pull qwen2.5:14b

# 4. Terminal 1 — start Ollama
ollama serve

# 5. Terminal 2 — start Charlie
python3 main_local.py

# 6. Open browser
open index_local.html
```

→ Full instructions: [Setup Guide](https://github.com/anandgopi20/Ask-Charlie/wiki/Setup-Guide)

---

## Tech stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI |
| Vector search | FAISS |
| Embeddings | all-MiniLM-L6-v2 |
| Reranker | CrossEncoder ms-marco-MiniLM-L-6-v2 |
| Local LLM | Qwen2.5 14B via Ollama |
| Production LLM | Gemini 2.5 Flash Lite |
| Frontend | Vanilla HTML / CSS / JS |
| Logging | Python RotatingFileHandler + JSONL |

---

## Project structure
Ask-Charlie/
├── data/
│   ├── professors.json
│   ├── buildings.csv
│   ├── cdc.md
│   └── courses.json
├── logs/                  ← auto-created on first run
│   ├── charlie.log
│   └── queries.jsonl
├── scraper1/
├── main_local.py          ← local backend (Ollama)
├── main.py                ← production backend (Railway)
├── index_local.html       ← local frontend
├── index.html             ← production frontend
├── requirements.txt
├── railway.toml
└── Procfile

---

## Wiki

| Page | Link |
|---|---|
| Home | [Wiki Home](https://github.com/anandgopi20/Ask-Charlie/wiki) |
| Changelog | [All versions](https://github.com/anandgopi20/Ask-Charlie/wiki/Changelog) |
| Architecture | [How RAG works](https://github.com/anandgopi20/Ask-Charlie/wiki/Architecture) |
| API Reference | [All endpoints](https://github.com/anandgopi20/Ask-Charlie/wiki/API-Reference) |
| Setup Guide | [Install & run](https://github.com/anandgopi20/Ask-Charlie/wiki/Setup-Guide) |
| Roadmap | [Planned features](https://github.com/anandgopi20/Ask-Charlie/wiki/Roadmap) |
| Feedback | [15 real responses](https://github.com/anandgopi20/Ask-Charlie/wiki/Feedback) |

---

## Feedback highlights

> *"Response was clear and direct."* — Robert Patrick, Fire Protection Engineering

> *"Complete answer with phone number for further questions."* — Joseph Dibiamo, Chemistry

> *"He gave good detailed answers and multiple options."* — Liz Trentmann, Business Management

> *"Gave names of who to contact for info."* — Pier Cirillo, Professor / CCBE

---

<div align="center">

Advisor **Prof. Div Pithadia** 
Built by **Anand Gopi** · University of New Haven  
Testing by **Spandhana** · University of New Haven


[askcharlie.netlify.app](https://askcharlie.netlify.app) · [Wiki](https://github.com/anandgopi20/Ask-Charlie/wiki)

</div>
