"""Policy PDFs -> clause-sized chunks -> keyword search (BM25).

Each clause (LP-3, RG-1, KYC-2 ...) becomes one chunk, so a search result is one complete rule with its id.
BM25 needs no embedding model, which keeps the Docker image small; a vector store would also work.
"""
import re

from pypdf import PdfReader
from rank_bm25 import BM25Okapi

from app.config import POLICY_DIR

CLAUSE = re.compile(r"(?=\b(?:LP|RG|KYC)-\d+ [A-Z])")   # a clause id followed by a capital = start of a clause


def _load_chunks():
    chunks = []
    for pdf in sorted(POLICY_DIR.glob("*.pdf")):
        text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
        text = re.sub(r"\s+", " ", text)
        for part in CLAUSE.split(text):
            m = re.match(r"((?:LP|RG|KYC)-\d+)", part)
            if m:
                # stop at the next section heading such as "4. Process"
                body = re.split(r" \d\. [A-Z][a-z]+", part)[0].strip()
                chunks.append({"clause": m.group(1), "source": pdf.name, "text": body})
    return chunks


CHUNKS = _load_chunks()
_bm25 = BM25Okapi([re.findall(r"\w+", c["text"].lower()) for c in CHUNKS])
BY_CLAUSE = {c["clause"]: c for c in CHUNKS}


def search(query: str, k: int = 4) -> list[dict]:
    scores = _bm25.get_scores(re.findall(r"\w+", query.lower()))
    best = sorted(range(len(CHUNKS)), key=lambda i: -scores[i])[:k]
    return [CHUNKS[i] for i in best]


def clauses(ids: list[str]) -> list[dict]:
    return [BY_CLAUSE[i] for i in ids if i in BY_CLAUSE]


def as_context(chunks: list[dict]) -> str:
    return "\n".join(f"[{c['clause']}, {c['source']}] {c['text']}" for c in chunks)
