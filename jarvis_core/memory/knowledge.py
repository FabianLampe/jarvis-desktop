"""Lokale Dokumentensuche: MarkItDown → Chunks mit Fundstelle → FastEmbed → Chroma.

Jede Passage behält Dateipfad, Seite/Folie/Überschrift und Zeilenbereich, damit
Antworten belegt werden können. Alles läuft lokal; nur der einmalige Download
des Embedding-Modells (Hugging Face) braucht Internet."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

log = logging.getLogger("jarvis.knowledge")

SUPPORTED = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".md", ".txt", ".html", ".htm",
             ".csv", ".json", ".xml", ".epub"}
_PAGE_BREAK = "\x0c"
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_SLIDE = re.compile(r"<!--\s*Slide number:\s*(\d+)\s*-->")


@dataclass
class Chunk:
    text: str
    page: Optional[int]
    section: str
    line_start: int
    line_end: int

    @property
    def location(self) -> str:
        bits = []
        if self.page is not None:
            bits.append(f"Seite/Folie {self.page}")
        if self.section:
            bits.append(f"„{self.section}“")
        bits.append(f"Zeilen {self.line_start}–{self.line_end}")
        return ", ".join(bits)


def chunk_markdown(text: str, size: int = 900, overlap: int = 150, pdf: bool = False) -> list[Chunk]:
    """Teilt Markdown in überlappende Abschnitte und merkt Seite, Überschrift, Zeilen."""
    chunks: list[Chunk] = []
    page: Optional[int] = 1 if pdf else None
    section = ""
    buf: list[tuple[str, int]] = []          # (Zeile, Zeilennr.)
    buf_len = 0
    buf_page, buf_section = page, section

    def flush(keep_overlap: bool = True):
        nonlocal buf, buf_len, buf_page, buf_section
        body = "\n".join(l for l, _ in buf).strip()
        if body:
            chunks.append(Chunk(body, buf_page, buf_section, buf[0][1], buf[-1][1]))
        if keep_overlap and overlap:
            keep, n = [], 0
            for item in reversed(buf):
                n += len(item[0]) + 1
                if n > overlap:
                    break
                keep.insert(0, item)
            buf = keep if body else []
        else:
            buf = []
        buf_len = sum(len(l) + 1 for l, _ in buf)
        buf_page, buf_section = page, section

    for no, raw in enumerate(text.split("\n"), start=1):
        line = raw
        while _PAGE_BREAK in line:
            before, line = line.split(_PAGE_BREAK, 1)
            if before.strip():
                buf.append((before, no)); buf_len += len(before) + 1
            flush(keep_overlap=False)
            page = (page or 1) + 1
            buf_page = page
        if (m := _SLIDE.search(line)):
            flush(keep_overlap=False)
            page = int(m.group(1)); buf_page = page
            continue
        if (m := _HEADING.match(line)):
            if buf_len:
                flush(keep_overlap=False)
            section = m.group(2)
            buf_section = section
        if not line.strip() and not buf:
            continue
        if not buf:
            buf_page, buf_section = page, section
        buf.append((line, no)); buf_len += len(line) + 1
        if buf_len >= size:
            flush()
    flush(keep_overlap=False)
    return [c for c in chunks if len(c.text) > 3]


class Embedder:
    """FastEmbed (ONNX, CPU). e5-Modelle brauchen die Präfixe query:/passage:."""

    def __init__(self, model: str, cache_dir: Path):
        self.model_name = model
        self.cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()
        self._e5 = "e5" in model.lower()

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                log.info("Lade Embedding-Modell %s …", self.model_name)
                self._model = TextEmbedding(model_name=self.model_name,
                                            cache_dir=str(self.cache_dir))
        return self._model

    def docs(self, texts: list[str]) -> list[list[float]]:
        texts = [f"passage: {t}" for t in texts] if self._e5 else texts
        return [v.tolist() for v in self._load().embed(texts)]

    def query(self, text: str) -> list[float]:
        text = f"query: {text}" if self._e5 else text
        return next(iter(self._load().embed([text]))).tolist()


class Knowledge:
    def __init__(self, data_dir: Path, model: str, chunk_chars: int = 900,
                 overlap: int = 150, top_k: int = 4, embedder: Optional[Embedder] = None):
        self.dir = Path(data_dir) / "chroma"
        self.size, self.overlap, self.top_k = chunk_chars, overlap, top_k
        self.embedder = embedder or Embedder(model, Path(data_dir) / "models")
        self._col = None
        self._lock = threading.Lock()

    @property
    def collection(self):
        with self._lock:
            if self._col is None:
                import chromadb
                client = chromadb.PersistentClient(path=str(self.dir))
                self._col = client.get_or_create_collection(
                    "documents", metadata={"hnsw:space": "cosine"})
        return self._col

    # ---------------------------------------------------------- Indexieren

    def convert(self, path: Path) -> str:
        if path.suffix.lower() in (".md", ".txt", ".csv", ".json", ".xml"):
            return path.read_text(encoding="utf-8", errors="replace")
        from markitdown import MarkItDown
        return MarkItDown().convert(str(path)).text_content or ""

    def index_file(self, path: Path) -> int:
        path = Path(path).resolve()
        text = self.convert(path)
        chunks = chunk_markdown(text, self.size, self.overlap, pdf=path.suffix.lower() == ".pdf")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        col = self.collection
        col.delete(where={"path": str(path)})        # Neuindexierung ersetzt alte Passagen
        if not chunks:
            return 0
        embs = self.embedder.docs([c.text for c in chunks])
        col.add(
            ids=[f"{digest}-{i}" for i in range(len(chunks))],
            embeddings=cast(Any, embs), documents=[c.text for c in chunks],
            metadatas=[{"path": str(path), "file": path.name, "chunk": i,
                        "page": c.page if c.page is not None else -1,
                        "section": c.section, "line_start": c.line_start,
                        "line_end": c.line_end, "location": c.location, "sha": digest}
                       for i, c in enumerate(chunks)])
        return len(chunks)

    def index_path(self, path: Path) -> dict:
        path = Path(path)
        files = [path] if path.is_file() else sorted(
            f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in SUPPORTED
            and not any(part.startswith(".") for part in f.relative_to(path).parts))
        done, failed, chunks = 0, [], 0
        for f in files[:500]:
            try:
                chunks += self.index_file(f); done += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("Indexierung von %s fehlgeschlagen: %s", f, exc)
                failed.append(f"{f.name}: {type(exc).__name__}: {exc}"[:160])
        return {"files": done, "chunks": chunks, "failed": failed, "skipped": max(0, len(files) - 500)}

    # --------------------------------------------------------------- Suche

    def search(self, query: str, k: Optional[int] = None) -> list[dict]:
        col = self.collection
        if col.count() == 0:
            return []
        res = col.query(query_embeddings=[self.embedder.query(query)],
                        n_results=min(k or self.top_k, col.count()))
        out = []
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for doc, meta, dist in zip(docs, metas, dists):
            out.append({"text": doc, "path": meta["path"], "file": meta["file"],
                        "location": meta["location"], "score": round(1 - dist, 3)})
        return out

    def stats(self) -> dict:
        col = self.collection
        files = {str(m.get("path")) for m in (col.get(include=cast(Any, ["metadatas"])).get("metadatas") or [])}
        return {"chunks": col.count(), "files": sorted(files)}

    def forget(self, path: Path) -> None:
        self.collection.delete(where={"path": str(Path(path).resolve())})
