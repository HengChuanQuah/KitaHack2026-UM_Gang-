import os
import shutil
import io
import re
import json
import time
from typing import List, Dict, Tuple, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import google.generativeai as genai
from pypdf import PdfReader
import uvicorn
import PyPDF2


# --- CONFIGURATION ---
GOOGLE_API_KEY = "GOOGLE_API_KEY_PLACEHOLDER"
genai.configure(api_key=GOOGLE_API_KEY)

model = genai.GenerativeModel('models/gemini-2.5-flash')
flash_model = genai.GenerativeModel("models/gemini-2.5-flash")
answer_model = genai.GenerativeModel("models/gemini-2.5-flash")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "study_data")
os.makedirs(DATA_DIR, exist_ok=True)

MAX_FILE_MB = 30
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024


# ---------------------------
# Helpers
# ---------------------------
def ensure_subject_dirs(subj_path: str):
    os.makedirs(subj_path, exist_ok=True)
    os.makedirs(os.path.join(subj_path, "notes"), exist_ok=True)
    os.makedirs(os.path.join(subj_path, "pyq"), exist_ok=True)

def safe_filename(name: str) -> str:
    return os.path.basename(name)

def extract_text(file_bytes: bytes) -> str:
    """Whole-document text extraction (best-effort)."""
    try:
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        return "".join([page.extract_text() or "" for page in reader.pages])
    except:
        return ""


# ---------------------------
# Optional vision support
# ---------------------------
try:
    from pdf2image import convert_from_bytes
    from PIL import Image
    VISION_AVAILABLE = True
except Exception:
    VISION_AVAILABLE = False


# ---------------------------
# RAG-lite helpers
# ---------------------------
STOPWORDS = {
    "the","a","an","and","or","but","if","then","else","when","while",
    "is","are","was","were","be","been","being",
    "to","of","in","on","for","with","as","by","at","from",
    "this","that","these","those","it","its","we","you","they","i",
    "can","could","should","would","may","might","will","shall",
    "yang","dan","atau","jika","maka","ini","itu","dalam","pada","ke","dari","untuk","oleh"
}

def normalize_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize_keywords(s: str) -> List[str]:
    s = normalize_text(s)
    words = re.findall(r"[a-z0-9]+", s)
    words = [w for w in words if w not in STOPWORDS and len(w) > 2]
    return words

def chunk_text_by_words(text: str, chunk_size_words: int = 700, overlap_words: int = 120) -> List[str]:
    words = text.split()
    chunks = []
    start = 0
    n = len(words)

    while start < n:
        end = min(start + chunk_size_words, n)
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        if end == n:
            break
        start = max(0, end - overlap_words)

    return chunks

def save_chunks(subject_path: str, chunks: List[str]) -> str:
    chunk_path = os.path.join(subject_path, "notes_chunks.json")
    payload = [{"id": i, "text": c} for i, c in enumerate(chunks)]
    with open(chunk_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return chunk_path

def load_chunks(subject_path: str) -> List[Dict]:
    chunk_path = os.path.join(subject_path, "notes_chunks.json")
    if not os.path.exists(chunk_path):
        return []
    with open(chunk_path, "r", encoding="utf-8") as f:
        return json.load(f)

def retrieve_top_chunks_with_score(question: str, chunks: List[Dict], top_k: int = 5) -> Tuple[List[Dict], int]:
    q_words = set(tokenize_keywords(question))
    if not q_words:
        return ([], 0)

    scored: List[Tuple[int, int]] = []
    for c in chunks:
        c_words = set(tokenize_keywords(c["text"]))
        score = len(q_words.intersection(c_words))
        scored.append((score, c["id"]))

    scored.sort(reverse=True, key=lambda x: x[0])
    top_score = scored[0][0] if scored else 0

    best_ids = [cid for score, cid in scored[:top_k] if score > 0]
    id_to_chunk = {c["id"]: c for c in chunks}
    return ([id_to_chunk[i] for i in best_ids], top_score)


# ---------------------------
# Better vision trigger
# ---------------------------
def should_use_vision(pyq_text: str) -> bool:
    t = (pyq_text or "").strip()
    low = t.lower()

    if len(t) < 300:
        return True
    if t.count("�") > 10:
        return True

    diagram_keywords = [
        "diagram", "rajah", "graf", "graph", "litar", "circuit", "gambar",
        "axis", "paksi", "velocity-time", "displacement-time", "table", "jadual"
    ]
    if any(k in low for k in diagram_keywords):
        return True

    return False


# ---------------------------
# English-only filter (NO TRANSLATION)
# ---------------------------
ENGLISH_MARKERS = [
    "diagram", "graph", "table", "which", "shows", "state", "tick", "choose",
    "answer", "explain", "calculate", "determine", "based on", "time", "displacement",
    "velocity", "temperature", "energy", "force", "mass", "current", "potential"
]
MALAY_MARKERS = [
    "rajah", "graf", "jadual", "nyatakan", "huraikan", "tentukan", "tandakan",
    "suhu", "masa", "sesaran", "halaju", "daya", "tenaga", "haba", "muatan",
    "pendam", "objek", "permukaan", "bulan", "bumi", "kekuatan", "medan"
]

def keep_english_only(text: str) -> str:
    if not text:
        return text

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return text

    kept_lines = []
    for ln in lines:
        low = ln.lower()

        has_eng = any(m in low for m in ENGLISH_MARKERS)
        has_malay = any(m in low for m in MALAY_MARKERS)

        if has_eng and has_malay:
            idxs = []
            for m in ENGLISH_MARKERS:
                i = low.find(m)
                if i != -1:
                    idxs.append(i)
            if idxs:
                cut = min(idxs)
                kept_lines.append(ln[cut:].strip())
                continue

        if has_eng and not has_malay:
            kept_lines.append(ln)
            continue

        if has_malay and not has_eng:
            continue

        if any(w in low for w in [" the ", " which ", " shows ", " state ", " explain ", " calculate "]):
            kept_lines.append(ln)

    return "\n".join(kept_lines) if kept_lines else text


# ---------------------------
# Gemini retry wrapper (ONLY used for extraction + answering)
# ---------------------------
def gemini_generate_with_retry(gen_model, content, max_retries: int = 6):
    delay = 3.0
    last_err = None
    for _ in range(max_retries):
        try:
            return gen_model.generate_content(content)
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if ("429" in msg) or ("quota" in msg) or ("rate" in msg) or ("resource_exhausted" in msg):
                time.sleep(delay)
                delay = min(delay * 1.8, 25.0)
                continue
            raise
    raise last_err


# ---------------------------
# Structured extraction rules (Faithful)
# ---------------------------
def _safe_json_load(text: str) -> Any:
    raw = (text or "").strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except:
        l = raw.find("[")
        r = raw.rfind("]")
        if l != -1 and r != -1 and r > l:
            return json.loads(raw[l:r+1])
        raise

EXTRACT_STRUCTURED_RULES = """
Return ONLY valid JSON (no markdown, no code fences, no extra text).

Output must be a JSON list. Each element is an object with keys:
- "type": "mcq" OR "subjective"
- "stem": string (ENGLISH ONLY)
- "options": object with keys "A","B","C","D" ONLY if type="mcq" (ENGLISH ONLY)
- "has_diagram": true/false
- "diagram_text": string (ENGLISH ONLY)

ENGLISH-ONLY RULES:
- The paper is bilingual. Extract ONLY the ENGLISH version.
- Do NOT include any Malay text.
- Do NOT translate anything.
- If English is not visible for a field, leave it blank "".

FAITHFULNESS RULES (VERY IMPORTANT):
- Extract ONLY what is visible in the provided text/images.
- Do NOT invent or paraphrase questions.
- Keep the exact wording as much as possible.
- If you are unsure, leave stem "" (empty) rather than guessing.
- Preserve the order of questions as they appear (top to bottom).
- For MCQ, keep options exactly as shown and keep the correct A/B/C/D mapping.
- Do NOT merge two questions into one.
"""

def _clean_extracted_questions(arr: Any, source: str) -> List[Dict]:
    cleaned: List[Dict] = []
    if not isinstance(arr, list):
        return cleaned

    order = 0
    for q in arr:
        if not isinstance(q, dict):
            continue
        stem_raw = str(q.get("stem", "") or "").strip()
        if not stem_raw:
            continue

        stem = keep_english_only(stem_raw).strip()
        diagram_text = keep_english_only(str(q.get("diagram_text", "") or "")).strip()
        q_type = str(q.get("type", "subjective")).lower().strip()
        options = q.get("options", None)

        if isinstance(options, dict):
            for k in list(options.keys()):
                if isinstance(options[k], str):
                    options[k] = keep_english_only(options[k].strip())
            options = {k: v for k, v in options.items() if k in ["A","B","C","D"] and str(v).strip()}

        cleaned.append({
            "page": None,
            "order_in_page": order,
            "source": source,
            "type": q_type,
            "stem": stem,
            "options": options,
            "has_diagram": bool(q.get("has_diagram", False)),
            "diagram_text": diagram_text,
        })
        order += 1

    return cleaned

def extract_questions_with_llm(pyq_text: str, limit: int = 10) -> List[Dict]:
    prompt = f"""
You are extracting exam questions from a past-year paper text dump.

{EXTRACT_STRUCTURED_RULES}

Extract up to {limit} questions from the text below.
Return ONLY a JSON list.

[PYQ_TEXT]
{pyq_text[:28000]}
"""
    try:
        resp = gemini_generate_with_retry(flash_model, prompt)
        arr = _safe_json_load(resp.text)
        return _clean_extracted_questions(arr, source="text_onecall")[:limit]
    except:
        return []

def extract_questions_from_pdf_with_vision(pyq_bytes: bytes, limit: int = 10, max_pages: int = 6) -> List[Dict]:
    if not VISION_AVAILABLE:
        return []
    try:
        pages = convert_from_bytes(pyq_bytes, dpi=240)
        pages = pages[:max_pages]

        prompt = f"""
You are extracting exam questions from images of a past-year paper.

{EXTRACT_STRUCTURED_RULES}

Extract up to {limit} questions from these pages.
Return ONLY a JSON list.
"""
        parts = [prompt] + pages
        resp = gemini_generate_with_retry(flash_model, parts)
        arr = _safe_json_load(resp.text)
        return _clean_extracted_questions(arr, source="vision_onecall")[:limit]
    except:
        return []

def extract_all_questions_faithful(pyq_bytes: bytes, limit: int = 10) -> List[Dict]:
    """
    Same function name as your zip code, but now only ONE extraction call.
    This prevents hitting quota too early and gives more complete questions.
    """
    pyq_text = extract_text(pyq_bytes)
    use_vision = should_use_vision(pyq_text)

    if use_vision:
        qs = extract_questions_from_pdf_with_vision(pyq_bytes, limit=limit, max_pages=6)
        if qs:
            return qs
        return extract_questions_with_llm(pyq_text, limit=limit)

    qs = extract_questions_with_llm(pyq_text, limit=limit)
    if qs:
        return qs
    if VISION_AVAILABLE:
        return extract_questions_from_pdf_with_vision(pyq_bytes, limit=limit, max_pages=6)
    return []


# ---------------------------
# Build chunks from saved notes
# ---------------------------
def rebuild_notes_chunks(subj_path: str) -> Tuple[bool, str]:
    notes_dir = os.path.join(subj_path, "notes")
    if not os.path.exists(notes_dir):
        return (False, "Notes folder not found.")

    note_files = [f for f in os.listdir(notes_dir) if f.lower().endswith(".pdf")]
    if not note_files:
        return (False, "No lecture notes found. Upload notes first.")

    combined = ""
    for fn in note_files:
        p = os.path.join(notes_dir, fn)
        try:
            with open(p, "rb") as f:
                b = f.read()
            t = extract_text(b)
            if t.strip():
                combined += "\n\n" + t
        except:
            continue

    if not combined.strip():
        return (False, "Could not extract text from saved lecture notes (they may be scanned images).")

    chunks = chunk_text_by_words(combined, chunk_size_words=700, overlap_words=120)
    save_chunks(subj_path, chunks)
    return (True, f"Notes saved. Built chunk database with {len(chunks)} chunks from {len(note_files)} note PDF(s).")


# =========================================================
# SUBJECTS
# =========================================================
@app.get("/subjects")
async def list_subjects():
    subs = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d))]
    return {"subjects": subs}

@app.post("/subjects/create")
async def create_subject(name: str = Form(...)):
    subj_path = os.path.join(DATA_DIR, name)
    ensure_subject_dirs(subj_path)
    return {"status": "success"}


# =========================================================
# NOTES VIEWING
# =========================================================
@app.get("/subjects/{subject}/notes")
async def list_notes(subject: str):
    subj_path = os.path.join(DATA_DIR, subject)
    notes_dir = os.path.join(subj_path, "notes")
    if not os.path.exists(notes_dir):
        return {"notes": []}
    notes = [f for f in os.listdir(notes_dir) if f.lower().endswith(".pdf")]
    notes.sort()
    return {"notes": notes}

@app.get("/subjects/{subject}/notes/{filename}")
async def download_note(subject: str, filename: str):
    subj_path = os.path.join(DATA_DIR, subject)
    notes_dir = os.path.join(subj_path, "notes")
    filename = safe_filename(filename)
    path = os.path.join(notes_dir, filename)

    if not os.path.exists(path):
        return {"error": "Note file not found."}

    return FileResponse(path, media_type="application/pdf", filename=filename)


# =========================================================
# SAVED PAPERS LIST / READ
# =========================================================
@app.get("/subjects/{subject}/files")
async def list_files(subject: str):
    subj_path = os.path.join(DATA_DIR, subject)
    if not os.path.exists(subj_path):
        return {"files": []}
    files = [f for f in os.listdir(subj_path) if f.endswith("_analysis.txt")]
    return {"files": files}

@app.get("/subjects/{subject}/files/{filename}")
async def get_file_content(subject: str, filename: str):
    path = os.path.join(DATA_DIR, subject, safe_filename(filename))
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return {"content": f.read()}
    return {"content": "Error: File not found."}


# =========================================================
# ✅ Option 1: read analysis JSON per paper
# =========================================================
@app.get("/subjects/{subject}/analysis_json/{filename}")
async def get_analysis_json(subject: str, filename: str):
    path = os.path.join(DATA_DIR, subject, safe_filename(filename))
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    raise HTTPException(status_code=404, detail="Analysis JSON not found.")


# =========================================================
# UPLOAD NOTES ONLY (multiple)
# =========================================================
@app.post("/subjects/upload_notes")
async def upload_notes(
    subject: str = Form(...),
    notes_files: List[UploadFile] = File(...),
):
    subj_path = os.path.join(DATA_DIR, subject)
    ensure_subject_dirs(subj_path)
    notes_dir = os.path.join(subj_path, "notes")

    saved = 0
    errors = []

    for nf in notes_files:
        nb = await nf.read()
        if len(nb) > MAX_FILE_BYTES:
            errors.append(f"{nf.filename}: too large (> {MAX_FILE_MB}MB)")
            continue
        try:
            with open(os.path.join(notes_dir, safe_filename(nf.filename)), "wb") as f:
                f.write(nb)
            saved += 1
        except Exception as e:
            errors.append(f"{nf.filename}: {str(e)}")

    ok, msg = rebuild_notes_chunks(subj_path)

    out = f"Saved {saved} lecture note file(s). {msg}"
    if errors:
        out += f" | Skipped/Errors: {', '.join(errors)}"
    if not ok:
        return {"result": f"Error: {out}"}
    return {"result": out}


# =========================================================
# UPLOAD PYQ ONLY + ANALYZE USING SAVED NOTES (multiple PYQs)
# Saves BOTH TXT + JSON
# =========================================================
@app.post("/upload_and_analyze")
async def upload_and_analyze(
    subject: str = Form(...),
    pyq_files: List[UploadFile] = File(...),
):
    subj_path = os.path.join(DATA_DIR, subject)
    ensure_subject_dirs(subj_path)

    chunk_objs = load_chunks(subj_path)
    if not chunk_objs:
        ok, msg = rebuild_notes_chunks(subj_path)
        if not ok:
            return {"result": f"Error: {msg}"}
        chunk_objs = load_chunks(subj_path)
        if not chunk_objs:
            return {"result": "Error: No notes chunk database found. Upload notes first."}

    pyq_dir = os.path.join(subj_path, "pyq")
    processed = []
    errors = []

    MCQ_INSTRUCTIONS = """
You are a study assistant for exam questions.

LANGUAGE RULE:
- Respond in ENGLISH ONLY.
- Do NOT include Malay text.
- Do NOT translate.

OUTPUT FORMAT (strict):
Answer: <A/B/C/D>  OR  Answer: Cannot determine
Reason (1–2 lines): <short explanation>

Rules:
- Use lecture notes chunks if provided; if not enough, use general knowledge.
- If the question depends on a diagram/graph but diagram info is missing or unclear, do NOT guess. Output "Cannot determine".
- Do NOT invent coordinates/values/points.
- Do NOT use LaTeX.
"""

    SUBJ_INSTRUCTIONS = """
You are a study assistant for exam questions.

LANGUAGE RULE:
- Respond in ENGLISH ONLY.
- Do NOT include Malay text.
- Do NOT translate.

OUTPUT FORMAT:
Final answer (1 line):
<...>

Explanation:
- step 1...
- step 2...
- ...

Rules:
- Prefer lecture notes chunks if provided; if not enough, use general knowledge.
- If the question depends on a diagram/graph but required diagram values are missing/unclear, say what is missing instead of guessing.
- Do NOT invent coordinates/values/points.
- Do NOT use LaTeX.
"""

    for pyq in pyq_files:
        pyq_bytes = await pyq.read()
        if len(pyq_bytes) > MAX_FILE_BYTES:
            errors.append(f"{pyq.filename}: too large (> {MAX_FILE_MB}MB)")
            continue

        try:
            with open(os.path.join(pyq_dir, safe_filename(pyq.filename)), "wb") as f:
                f.write(pyq_bytes)
        except Exception as e:
            errors.append(f"{pyq.filename}: {str(e)}")
            continue

        # ✅ Now uses one-call extraction (fix quota + better completeness)
        questions = extract_all_questions_faithful(pyq_bytes, limit=10)

        if not questions:
            fallback_text = keep_english_only((extract_text(pyq_bytes)[:1500] or "").strip())
            questions = [{
                "page": 1,
                "order_in_page": 0,
                "source": "fallback",
                "type": "subjective",
                "stem": fallback_text if fallback_text else f"(Could not extract questions from {pyq.filename})",
                "options": None,
                "has_diagram": True,
                "diagram_text": ""
            }]

        answers = []
        for qi, qobj in enumerate(questions, start=1):
            q_type = str(qobj.get("type", "subjective")).lower().strip()
            stem = keep_english_only(str(qobj.get("stem", "")).strip())
            options = qobj.get("options", None)
            has_diagram = bool(qobj.get("has_diagram", False))
            diagram_text = keep_english_only(str(qobj.get("diagram_text", "")).strip())

            retrieval_query = stem
            if isinstance(options, dict):
                opt_text = " ".join([f"{k} {v}" for k, v in options.items() if isinstance(v, str)])
                retrieval_query = f"{stem} {opt_text}".strip()

            top_chunks, top_score = retrieve_top_chunks_with_score(retrieval_query, chunk_objs, top_k=5)
            EVIDENCE_THRESHOLD = 2
            has_evidence = (len(top_chunks) > 0 and top_score >= EVIDENCE_THRESHOLD)
            ref_block = "\n\n".join([f"[CHUNK {c['id']}]\n{c['text']}" for c in top_chunks]) if has_evidence else ""

            if q_type == "mcq" and isinstance(options, dict):
                opts_lines = "\n".join([
                    f"{k}. {keep_english_only(str(options.get(k,''))).strip()}"
                    for k in ["A","B","C","D"] if k in options
                ])
                question_for_model = f"{stem}\n\nOptions:\n{opts_lines}"
                prompt_header = MCQ_INSTRUCTIONS
            else:
                question_for_model = stem
                prompt_header = SUBJ_INSTRUCTIONS

            diagram_section = ""
            if has_diagram:
                diagram_section = f"\nDiagram/Graph info extracted:\n{diagram_text if diagram_text else '(No diagram details extracted.)'}\n"

            if has_evidence:
                prompt = f"""{prompt_header}

Question:
{question_for_model}
{diagram_section}

Lecture note chunks (references):
{ref_block}
"""
            else:
                prompt = f"""{prompt_header}

Question:
{question_for_model}
{diagram_section}

Note: Relevant lecture-note evidence not found. You may use general knowledge, but do not invent missing diagram values.
"""

            try:
                # ✅ retry/backoff ONLY here (stops dying at Q6)
                resp = gemini_generate_with_retry(answer_model, prompt)
                ans = keep_english_only((resp.text or "").strip())
            except Exception as e:
                ans = f"Error during answering: {str(e)}"

            answers.append({
                "question_no": qi,
                "page": qobj.get("page", None),
                "source": qobj.get("source", None),
                "question": stem,
                "type": q_type,
                "options": options,
                "answer": ans,
            })

        report_lines = []
        report_lines.append(f"# PYQ Analysis — Subject: {subject}\n")
        report_lines.append(f"PYQ Filename: {pyq.filename}\n")
        report_lines.append("> This report uses saved lecture notes for retrieval (RAG).\n")
        report_lines.append("---\n")

        for item in answers:
            report_lines.append(f"## Q{item['question_no']} (Page {item.get('page','?')})\n")
            if item.get("type") == "mcq" and isinstance(item.get("options"), dict):
                report_lines.append(item["question"])
                report_lines.append("\n")
                opts = item["options"]
                for k in ["A","B","C","D"]:
                    if k in opts:
                        report_lines.append(f"{k}. {keep_english_only(str(opts[k])).strip()}")
                report_lines.append("\n")
            else:
                report_lines.append(item["question"])
                report_lines.append("\n")
            report_lines.append(item["answer"])
            report_lines.append("\n---\n")

        analysis_text = "\n".join(report_lines)
        save_txt = f"{pyq.filename}_analysis.txt"
        with open(os.path.join(subj_path, safe_filename(save_txt)), "w", encoding="utf-8") as f:
            f.write(analysis_text)

        save_json = f"{pyq.filename}_analysis.json"
        json_payload = {
            "subject": subject,
            "pyq_filename": pyq.filename,
            "questions": answers
        }
        with open(os.path.join(subj_path, safe_filename(save_json)), "w", encoding="utf-8") as f:
            json.dump(json_payload, f, ensure_ascii=False, indent=2)

        processed.append(save_txt)

    msg = f"Done. Processed {len(processed)} PYQ file(s). Saved: {', '.join(processed) if processed else '(none)'}"
    if errors:
        msg += f" | Skipped/Errors: {', '.join(errors)}"
    return {"result": msg}


# =========================================================
# ✅ Scope analysis endpoint (REVERTED)
# =========================================================
@app.post("/subjects/analyze_scope")
async def analyze_scope(subject: str = Form(...)):
    subj_path = os.path.join(DATA_DIR, subject)
    if not os.path.exists(subj_path):
        return {"topics": []}

    files = [f for f in os.listdir(subj_path) if f.endswith("_analysis.txt")]
    if not files:
        return {"topics": ["No past year papers found. Upload some first!"]}

    combined_text = ""
    for filename in files:
        path = os.path.join(subj_path, filename)
        with open(path, "r", encoding="utf-8") as f:
            combined_text += f"--- Paper: {filename} ---\n" + f.read() + "\n"

    prompt = f"""
I have provided analyses from multiple Past Year Exam papers for the subject '{subject}'.

TASK: Identify EVERY unique technical topic or chapter that has appeared in these exams.

OUTPUT FORMAT:
Return ONLY a simple comma-separated list.
Example: "Topic A, Topic B, Topic C"

[DATA]:
{combined_text[:60000]}
"""
    try:
        response = flash_model.generate_content(prompt)  # ✅ reverted
        raw_text = (response.text or "").replace("\n", "").replace("*", "")
        topics = [t.strip() for t in raw_text.split(",") if t.strip()]
        return {"topics": topics}
    except Exception as e:
        return {"topics": [f"Error: {str(e)}"]}


# =========================================================
# SCOPE / MOCK / NOTES BANK (unchanged)
# =========================================================

def get_scope_file(subject_name):
    return f"{subject_name}_scope.json"

@app.post("/subjects/get_scope")
async def get_scope(subject_name: str = Form(...)):
    filepath = get_scope_file(subject_name)
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return {"scope": json.load(f)}
    return {"scope": []}

def get_master_notes_file(subject_name):
    return f"{subject_name}_master_notes.txt"

@app.post("/subjects/generate_scope")
async def generate_scope(subject_name: str = Form(...)):
    filepath = get_scope_file(subject_name)
    master_notes_path = get_master_notes_file(subject_name)

    study_context = "No specific lecture notes provided."
    if os.path.exists(master_notes_path):
        with open(master_notes_path, "r", encoding="utf-8") as f:
            study_context = f.read()[:50000]

    gemini_prompt = f"""
    Create a syllabus scope checklist for the subject: {subject_name}.

    CRITICAL INSTRUCTION: Base your checklist on the following lecture notes provided by the student.
    Ensure every major topic in these notes is represented in the checklist.

    [STUDENT LECTURE NOTES START]
    {study_context}
    [STUDENT LECTURE NOTES END]

    Return ONLY a valid JSON array of objects. Do not use markdown blocks.
    Format: [{{"topic": "Topic Name", "completed": false}}, ...]
    """

    try:
        response = model.generate_content(gemini_prompt)  # ✅ reverted
        raw_text = response.text.strip().replace('```json', '').replace('```', '')
        scope_data = json.loads(raw_text)

        with open(filepath, "w") as f:
            json.dump(scope_data, f)

        return {"scope": scope_data}
    except Exception as e:
        return {"error": str(e)}

@app.post("/subjects/toggle_scope_item")
async def toggle_scope_item(subject_name: str = Form(...), topic_index: int = Form(...), completed: bool = Form(...)):
    filepath = get_scope_file(subject_name)
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            scope_data = json.load(f)

        scope_data[topic_index]['completed'] = completed

        with open(filepath, "w") as f:
            json.dump(scope_data, f)

        return {"success": True}
    return {"error": "File not found"}

@app.post("/subjects/generate_mock")
async def generate_mock(subject: str = Form(...)):
    subj_path = os.path.join(DATA_DIR, subject)

    if not os.path.exists(subj_path):
        return {"questions": []}

    files = [f for f in os.listdir(subj_path) if f.endswith("_analysis.txt")]
    if not files:
        return {"questions": [{"question": "No past year analyses found for this subject.", "answer": "Upload and analyze at least one PYQ first."}]}

    combined_text = ""
    for filename in files:
        with open(os.path.join(subj_path, filename), "r", encoding="utf-8") as f:
            combined_text += f"\n\n--- {filename} ---\n" + f.read()

    context = combined_text[:35000]

    prompt = f"""
You are a study assistant.

TASK:
Generate 5 challenging mock exam questions WITH answer keys based ONLY on the context below.

OUTPUT FORMAT (STRICT):
Return ONLY valid JSON (no markdown, no code fences, no extra text), exactly like:
[
  {{"question":"...","answer":"..."}},
  ...
]

RULES:
- ENGLISH ONLY.
- Do NOT use LaTeX.
- Questions should mimic the style of the past-year questions.
- If context is insufficient, still output JSON but make questions more general.

[CONTEXT]
{context}
"""

    try:
        response = answer_model.generate_content(prompt)  # ✅ reverted
        raw = (response.text or "").strip()

        raw = raw.replace("```json", "").replace("```", "").strip()

        try:
            data = json.loads(raw)
        except:
            l = raw.find("[")
            r = raw.rfind("]")
            if l != -1 and r != -1 and r > l:
                data = json.loads(raw[l:r+1])
            else:
                raise

        if not isinstance(data, list):
            raise ValueError("Model did not return a JSON list.")

        cleaned = []
        for item in data:
            if isinstance(item, dict) and "question" in item and "answer" in item:
                cleaned.append({"question": str(item["question"]), "answer": str(item["answer"])})
        if not cleaned:
            raise ValueError("Parsed JSON but no valid question/answer pairs found.")

        return {"questions": cleaned[:5]}

    except Exception as e:
        return {"questions": [{"question": "Error generating quiz. Try again.", "answer": str(e)}]}

@app.post("/subjects/generate_sample_question")
async def generate_sample_question(topic: str = Form(...), subject_name: str = Form(...)):
    gemini_prompt = f"""
    You are an expert exam setter for students ranging from SPM to university level.
    The student is looking at their scope checklist for the subject: {subject_name}.
    The specific topic is: "{topic}".

    TASK: Generate ONE sample exam question that mimics the style and difficulty of typical past year papers for this topic.
    RULE 1: Provide ONLY the question text.
    RULE 2: Do NOT provide the answer or explanation yet.

    IMPORTANT FORMATTING RULE:
    - Do NOT use LaTeX format (like \\frac, \\int, \\hat).
    - Use standard Unicode symbols instead (e.g., use '∫' for integral, 'θ' for theta, 'x²' for squared).
    - Write equations in a way that is readable in a plain text file (e.g., "x = (-b ± √(b² - 4ac)) / 2a").
    """

    try:
        response = model.generate_content(gemini_prompt)
        sample_question = response.text.strip()
        return {"question": sample_question}
    except Exception as e:
        return {"error": str(e)}

@app.post("/subjects/delete_scope")
async def delete_scope(subject_name: str = Form(...)):
    filepath = get_scope_file(subject_name)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            return {"success": True, "message": "Checklist reset successfully"}
        else:
            return {"error": "No checklist found to delete"}
    except Exception as e:
        return {"error": f"Failed to delete: {str(e)}"}

def get_notes_list_file(subject_name):
    return f"{subject_name}_notes_list.json"

@app.post("/subjects/upload_note")
async def upload_note(subject: str = Form(...), note: UploadFile = File(...)):
    try:
        pdf_bytes = await note.read()

        text_content = ""
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        for page in pdf_reader.pages:
            extracted = page.extract_text()
            if extracted:
                text_content += extracted + "\n"

        if not text_content.strip():
            return {"error": "Could not extract text. Ensure the PDF contains actual text, not just scanned images."}

        master_path = get_master_notes_file(subject)
        with open(master_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n--- Source: {note.filename} ---\n\n")
            f.write(text_content)

        list_path = get_notes_list_file(subject)
        notes_list = []
        if os.path.exists(list_path):
            with open(list_path, "r") as f:
                notes_list = json.load(f)

        notes_list.append(note.filename)
        with open(list_path, "w") as f:
            json.dump(notes_list, f)

        return {"success": True, "notes": notes_list}
    except Exception as e:
        return {"error": str(e)}

@app.post("/subjects/get_notes")
async def get_notes(subject: str = Form(...)):
    list_path = get_notes_list_file(subject)
    if os.path.exists(list_path):
        with open(list_path, "r") as f:
            return {"notes": json.load(f)}
    return {"notes": []}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)