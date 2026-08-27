# 🔐 PaySentinelIQ

<div align="center">

**AI-Powered Payroll & Boleto Fraud Detection Platform**

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-16.2-black.svg)](https://nextjs.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-336791.svg)](https://www.postgresql.org/)
[![CrewAI](https://img.shields.io/badge/CrewAI-Multi--Agent-FF6B6B.svg)](https://crewai.com/)
[![Gemini](https://img.shields.io/badge/LLM-Gemini_2.5_Flash-4285F4.svg)](https://deepmind.google/technologies/gemini/)
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)]()

*Detecting fraud before it happens — deterministic validation, multi-agent AI, and RAG-powered knowledge retrieval*

</div>

---

## 📖 Table of Contents

- [Overview](#-overview)
- [Architecture](#-architecture)
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Getting Started](#-getting-started)
- [Pipeline Flow](#-pipeline-flow)
- [AI Agent System](#-ai-agent-system)
- [Fusion Engine & Explainability](#-fusion-engine--explainability)
- [RAG Knowledge Base](#-rag-knowledge-base)
- [API Reference](#-api-reference)
- [Testing & Benchmarking](#-testing--benchmarking)
- [Deployment](#-deployment)

---

## 🎯 Overview

**PaySentinelIQ** is an enterprise-grade fraud detection platform that analyzes Brazilian financial documents — bank slips (*boletos*) and payroll records (*contracheques*) — using a hybrid architecture of **deterministic rules**, **multi-agent AI (CrewAI + Gemini)**, and **RAG (Retrieval-Augmented Generation)** with official FEBRABAN/BACEN knowledge.

The system achieves **100% accuracy on fraudulent document detection** (verified via 60-document benchmark), producing zero false negatives on blatantly fraudulent documents.

### Why It Exists

Fraudulent boletos cause **billions of reais in annual losses** in Brazil. Traditional solutions rely on human review or simple regex checks. PaySentinelIQ combines:

- **FEBRABAN/BACEN regulatory rules** (Módulo 10/11 checksums, ISPB bank validation)
- **Multi-method PDF extraction** (PyMuPDF → pdfplumber → pypdf → Tesseract OCR)
- **5 specialized AI agents** analyzing forensic, compliance, and fraud patterns
- **Official knowledge base** retrieved via BGE-M3 embeddings + pgvector
- **Weighted Fusion Engine** with full explainability

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND (Next.js 16)                     │
│  analyze-bank-slip  │  analyze-payroll  │  dashboard  │  reports │
└────────────────────────────┬────────────────────────────────────┘
                             │ multipart/form-data (PDF + metadata)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     API GATEWAY (FastAPI)                        │
│  POST /api/documents/analyze  ←  CANONICAL PIPELINE             │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                 CANONICAL DOCUMENT PIPELINE (6 stages)           │
│                                                                  │
│  [1] Ingest  ──▶  [2] Extract  ──▶  [3] Validate                │
│       │                │                  │                      │
│       ▼                ▼                  ▼                      │
│  [4] Enrich  ──▶  [5] Risk  ──▶  [6] CrewAI (5 agents)         │
│       │                │                  │                      │
│       ▼                ▼                  ▼                      │
│  BrasilAPI       FusionEngine      Knowledge Base (RAG)          │
└──────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      OUTPUT LAYER                                │
│  PipelineResult { risk_score, evidence[], explainability, ... }  │
│  → PostgreSQL (analysis_records, fraud_alerts, notifications)    │
│  → WebSocket (real-time toast notifications)                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## ✨ Features

### Document Analysis
- **Boleto (Bank Slip) Analysis**: FEBRABAN layout validation, linha digitável checksum, bank code verification against BACEN ISPB registry, CNPJ/CPF validation, overdue detection, illegal fee detection (multa > 2%, juros > 1%/mês), Pix QR code cross-validation
- **Payroll (Contracheque) Analysis**: INSS/IRRF/FGTS recalculation, salary consistency checks, CBO/CNAE compatibility, ghost employee detection

### AI Intelligence
- **5 Specialized CrewAI Agents**: Fraud Pattern Analyst, Document Forensics Analyst, Entity Compliance Analyst, Lead Investigator, Quality Reviewer
- **Parallel Execution**: Agents A, B, C run simultaneously via `asyncio.gather()`
- **RAG-Powered**: Agents consult official FEBRABAN/BACEN knowledge base before producing conclusions
- **Circuit Breaker**: 3 consecutive LLM failures → open circuit for 60s
- **Retry with Backoff**: 2 retries per agent with exponential backoff (1s, 2s)

### Scoring & Explainability
- **Fusion Engine**: Weighted evidence fusion with source confidence multipliers (deterministic=1.5x, knowledge_base=1.3x, crewai=0.9x)
- **IRON RULES**: 1 CRITICAL evidence → score ≥ 70 (HIGH), 3+ CRITICAL → score ≥ 90
- **Explainability Engine**: Per-evidence contribution breakdown, source attribution, severity analysis
- **ThresholdProvider**: Single source of truth for LOW/MEDIUM/HIGH classification

### Knowledge Base (RAG)
- **21 Official PDFs**: FEBRABAN manual, BACEN regulations, fraud patterns, bank layouts, algorithms
- **BGE-M3 Embeddings**: 1024-dimensional semantic vectors via pgvector
- **Hybrid Search**: Semantic (cosine) + keyword (full-text) + authority-weighted ranking
- **Query Expansion**: Auto-expands queries with related FEBRABAN/BACEN terms

### Production Readiness
- **Observability**: Structured JSON logging, CorrelationContext (request_id, trace_id, pipeline_id), PipelineMetrics (per-stage timing, error rates)
- **Resilience**: Circuit breaker, retry with backoff, graceful degradation (deterministic-only when LLM unavailable)
- **Health Checks**: `/health`, `/ready` (DB + Redis), `/live` (LLM provider)
- **Security**: Magic bytes validation, double-extension blocking, rate limiting, prompt injection sanitization
- **Shadow Mode**: Run legacy + canonical pipelines side-by-side for regression testing
- **Feature Flags**: 12 flags controlling all major features (zero-downtime rollback)

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend Framework** | FastAPI 0.115 (async) |
| **Frontend** | Next.js 16.2 (Turbopack), TypeScript, TailwindCSS, Zustand, Framer Motion |
| **Database** | PostgreSQL 15 + pgvector (Supabase) |
| **Cache** | Redis (Celery broker, WebSocket Pub/Sub) |
| **Messaging** | RabbitMQ 4 (topic exchange, retry + DLQ, publisher confirms) |
| **AI/ML** | CrewAI (multi-agent orchestration), LangChain, Google Gemini 2.5 Flash, BAAI/bge-m3 (embeddings), RecursiveCharacterTextSplitter |
| **PDF/OCR** | PyMuPDF, pdfplumber, pypdf, Tesseract OCR, pdf2image |
| **Infrastructure** | Railway (backend), Vercel (frontend), Supabase (DB), Sentry (error tracking) |
| **Testing** | pytest (257 tests), benchmark suite (60 documents) |

---

## 📁 Project Structure

```
PaySentinelIQ/
├── Back-end/                          # FastAPI backend
│   ├── app/
│   │   ├── api/documents/             # Canonical document analysis endpoint
│   │   ├── ai_agents/                 # CrewAI orchestration
│   │   │   ├── orchestrator.py        # 5-agent CrewAIOrchestrator
│   │   │   ├── agent_prompts.py       # Specialized system prompts
│   │   │   ├── crew.py                # Legacy agent definitions
│   │   │   └── tools/
│   │   │       ├── knowledge_tool.py  # RAG search tool
│   │   │       ├── boleto_tools.py    # FEBRABAN validation tools
│   │   │       ├── brazil_financial_tools.py
│   │   │       └── pdf_forensic_tools.py
│   │   ├── core/contracts/            # Domain contracts (PipelineContext, Evidence, etc.)
│   │   ├── services/
│   │   │   ├── pipeline/              # CanonicalPipeline + 6 stages
│   │   │   │   └── stages/            # Ingest, Extract, Validate, Enrich, Risk, CrewAI
│   │   │   ├── scoring/               # FusionEngine, ExplainabilityEngine, ThresholdProvider
│   │   │   ├── ai/                    # RiskAnalyzer, FraudCopilot, BoletoAnalyzer
│   │   │   └── ocr/                   # Multi-method PDF extraction
│   │   ├── knowledge/                 # RAG module (chunker, embedder, retriever, vector_store)
│   │   ├── observability/             # Logging, metrics, correlation, health checks
│   │   ├── fraud_detection/           # Legacy 7-stage pipeline (deprecated, being migrated)
│   │   ├── analytics/                 # Dashboard KPIs, analysis history, stats
│   │   ├── notifications/             # Notification service
│   │   └── shared/                    # ORM models, settings, domain primitives
│   └── tests/                         # 257 automated tests
│       └── unit/
│           ├── test_benchmark.py      # 60-document benchmark
│           ├── test_pipeline_hardening.py  # 40 regression tests
│           └── ...
├── Front-end/                         # Next.js frontend
│   └── src/
│       ├── app/[locale]/(app)/
│       │   ├── dashboard/             # Main dashboard, bank-slip, payroll, reports
│       │   ├── verification-center/   # Fraud alert review
│       │   └── notifications/         # Notification feed
│       ├── components/
│       │   ├── analysis/              # Upload, pipeline animation, result cards
│       │   └── notifications/         # Toast, notification cards
│       ├── hooks/                     # TanStack Query hooks
│       ├── lib/                       # API client, analysis mapper
│       └── stores/                    # Zustand stores
└── knowledge/                         # RAG knowledge base (21 PDFs)
    ├── regulations/                   # FEBRABAN, BACEN
    ├── attack_patterns/               # Fraud pattern catalog
    ├── reference/                     # Bank layouts
    └── ...
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL 15+ with pgvector extension
- Google Gemini API key
- Google OAuth client ID (for Drive integration)

### Backend Setup

```bash
cd Back-end
poetry install
cp .env.example .env  # Configure DATABASE_URL, GEMINI_API_KEY, etc.
poetry run python -m uvicorn app.main:create_app --factory --reload
```

### Frontend Setup

```bash
cd Front-end
npm install
cp .env.local.example .env.local  # Configure NEXT_PUBLIC_API_URL, GOOGLE_CLIENT_ID
npm run dev
```

### Database

```bash
# Tables are auto-created on startup via Base.metadata.create_all
# Or manually:
cd Back-end
poetry run alembic upgrade head
```

### RabbitMQ (Event-Driven Messaging)

Eventos assíncronos alimentam o Activity History (`/audit-logs`), o Notification
Center e e-mails transacionais. Arquitetura completa em
[`Back-end/docs/event_driven_architecture.md`](Back-end/docs/event_driven_architecture.md).

```bash
cd Back-end
# RabbitMQ 4 + Postgres + Redis
docker compose -f docker/docker-compose.yml up -d postgres redis rabbitmq

# Management UI: http://localhost:15672   (psi / psi_secret por padrão)
# AMQP:          localhost:5672

# API (publishers com confirms)
python -m uvicorn app.main:create_app --factory --reload

# Workers (processos separados, escaláveis horizontalmente)
python -m app.workers.audit_worker          # audit_logs (Activity History)
python -m app.workers.notification_worker   # notificações in-app + WS
python -m app.workers.email_worker          # e-mails transacionais

# Scheduler — detecta contas próximas do vencimento → bill.due_soon
python -m app.workers.scheduler
```

### Knowledge Base Ingestion

```bash
# Generate knowledge PDFs
cd knowledge
python generate_knowledge.py

# Ingest into pgvector (requires PostgreSQL + BGE-M3 model)
cd Back-end
poetry run python -m app.knowledge.ingest_knowledge
```

---

## 🔄 Pipeline Flow

```
POST /api/documents/analyze (multipart PDF + document_type)
  │
  ├── Stage 1: INGEST — validate file, generate document_id
  ├── Stage 2: EXTRACT — PyMuPDF OCR, structured field extraction, metadata
  ├── Stage 3: VALIDATE — FEBRABAN, CNPJ, dates, values → Evidence[]
  ├── Stage 4: ENRICH — BrasilAPI CNPJ lookup, company risk analysis
  ├── Stage 5: RISK — RiskAnalyzer heuristics + FusionEngine → deterministic score
  ├── Stage 6: CREW AI — 5 agents (A,B,C parallel → D → E) + RAG context injection
  │
  └── PipelineResult { risk_score, risk_level, evidence[], explainability, ... }
      ├── Persisted to: analysis_records, fraud_alerts, notifications
      └── WebSocket push → real-time toast notification
```

---

## 🤖 AI Agent System

| Agent | Role | Knowledge Sources |
|-------|------|-------------------|
| **A — Fraud Pattern Analyst** | Detects known fraud patterns, social engineering, behavioral inconsistencies | Fraud patterns, FEBRABAN, historical cases |
| **B — Document Forensics Analyst** | Validates PDF metadata, layers, fonts, OCR quality, AI-generation artifacts | Forensics, reference layouts |
| **C — Entity Compliance Analyst** | CNPJ/CPF validation, bank ISPB, Pix QR code, regulatory compliance | BACEN, entities, algorithms |
| **D — Lead Investigator** | Correlates A+B+C findings, eliminates duplicates, produces unified hypotheses | All agent outputs |
| **E — Quality Reviewer** | Validates coherence, detects contradictions, enforces IRON RULE | All agent outputs |

**Execution:** Agents A, B, C run in parallel. Agent D runs after all three complete. Agent E validates D's output.

---

## ⚖️ Fusion Engine & Explainability

### Scoring Algorithm

```
For each Evidence:
  contribution = severity_weight × source_multiplier × confidence

IRON RULES:
  - Any CRITICAL evidence → score ≥ 70 (HIGH)
  - 3+ CRITICAL evidence → score ≥ 90
  - 2+ CRITICAL → compound multiplier 1.2x
```

### Source Trust Hierarchy

| Source | Multiplier | Example |
|--------|-----------|---------|
| DETERMINISTIC | 1.5x | FEBRABAN Módulo 10 checksum |
| KNOWLEDGE_BASE | 1.3x | Official FEBRABAN manual (RAG) |
| BRASILAPI | 1.3x | Receita Federal CNPJ lookup |
| HEURISTIC | 1.0x | Round amount detection |
| CREWAI | 0.9x | AI agent analysis |

---

## 📚 RAG Knowledge Base

21 official PDFs organized by category:

| Category | Documents | Weight |
|----------|-----------|--------|
| Regulations/FEBRABAN | Boleto manual, field validation rules | 1.00 |
| Regulations/BACEN | PIX norms, boleto regulations, institutions | 0.98 |
| Attack Patterns | Fake boleto, QR overlay, beneficiary swap, ghost bank, payroll fraud | 0.90-0.95 |
| Algorithms | Módulo 10, Módulo 11, due date calculation | 0.95 |
| Reference | BB, Itaú, Caixa, Bradesco, Santander layouts | 0.95 |

**Ingestion Pipeline:** PDF → PyMuPDF → RecursiveCharacterTextSplitter (900/200) → BGE-M3 (1024-dim) → pgvector HNSW index.

---

## 📡 API Reference

### Document Analysis

```http
POST /api/documents/analyze
Content-Type: multipart/form-data

file: <PDF binary>
document_type: "boleto" | "contracheque"
observations: "optional notes"

Response:
{
  "RISK_ASSESSMENT": {
    "fraud_risk_score": 100,
    "risk_classification": "HIGH",
    "recommended_action": "REJECT"
  },
  "ANOMALY_LIST": [...],
  "risk_score": 100,
  "risk_level": "HIGH",
  "evidence": [...],
  "explainability": {...},
  "extracted_metadata": {
    "due_date": "15/03/2021",
    "issuer": "BANCO NACIONAL DIGITAL S/A",
    "cnpj": "00.000.000/0001-00",
    "amount": "15000.0"
  }
}
```

### Health Checks

```http
GET /health          → {"status": "ok"}
GET /ready           → {"status": "ready", "checks": {"database": {...}, "redis": {...}}}
GET /live            → {"status": "alive", "checks": {"llm": {...}}}
```

### Dashboard

```http
GET /api/dashboard/kpis    → {payrolls_processed, fraud_alerts, ai_confidence, ...}
GET /api/analysis/stats     → {total_documents, fraudulent_count, fraud_rate, ...}
GET /api/analysis/history   → [{id, risk_score, risk_level, ...}, ...]
```

---

## 🧪 Testing & Benchmarking

### Test Suite: 257 tests

```
$ pytest tests/unit/ -q
257 passed in 0.66s
```

| Test Category | Count | Description |
|--------------|-------|-------------|
| Contracts | 19 | PipelineContext, Evidence, PipelineResult |
| Architecture | 11 | CanonicalPipeline, FusionEngine invariants |
| CrewAI | 16 | Circuit breaker, JSON parser, agent findings |
| Pipeline Hardening | 40 | Regression (20 fraud + 20 legit) |
| Golden Dataset | 8 | Automated accuracy/precision/recall |
| Production | 17 | Event bus, exceptions, backward compat |
| Benchmark | 63 | 50 fraud + 10 legit + 3 stats |
| RAG | 24 | Query expansion, cache, evidence source |
| Knowledge | 11 | Chunker, registry, metadata |

### Benchmark Results

```
=== PAYSENTINELIQ BENCHMARK ===
Total documents: 60
True Positives:  50
True Negatives:  10
False Positives: 0
False Negatives: 0
Accuracy:  1.000 (100%)
Precision: 1.000 (100%)
Recall:    1.000 (100%)
F1 Score:  1.000
```

---

## 🚢 Deployment

| Component | Platform | URL |
|-----------|----------|-----|
| **Frontend** | Vercel | `https://pay-sentinel-iq.vercel.app` |
| **Backend** | Railway | `https://paysentineliq-production.up.railway.app` |
| **Database** | Supabase | PostgreSQL 15 + pgvector |

### Feature Flags (Zero-Downtime Rollback)

| Flag | Default | Purpose |
|------|---------|---------|
| `USE_CANONICAL_PIPELINE` | true | Canonical pipeline as entry point |
| `ENABLE_AI_AGENTS` | true | CrewAI multi-agent execution |
| `ENABLE_SHADOW_PIPELINE` | false | Legacy vs canonical comparison |
| `ENABLE_PIPELINE_EVENTS` | true | Event bus observability |
| `ENABLE_STRUCTURED_LOGGING` | true | JSON-formatted logs |
| `ENABLE_SECURITY_VALIDATION` | true | Magic bytes + MIME validation |

---

## 📊 Project Stats

```
Total commits:    200+
Lines of code:    35,000+
Python files:     80+
TypeScript files: 50+
Test files:       15+
Test cases:       257
PDF knowledge:    21 documents
AI agents:        5 (parallel A,B,C)
Pipeline stages:  6
Benchmark score:  100% accuracy
```

---

<div align="center">

**Built with ❤️ by the PaySentinelIQ Engineering Team**

*"Deterministic before LLM. Evidence before score. Explainability before trust."*

</div>
