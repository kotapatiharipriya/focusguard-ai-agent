"""Local retrieval over your Markdown notes (RAG for the coach and MCP).

Sources, by folder:
  knowledge/techniques   proven ways out of the scrolling loop (shipped with FocusGuard AI)
  knowledge/me           your own goals and notes (goals.md is a template to fill in)
  data/history           the coach's past weekly summaries, written automatically
  + any extra folders listed under "knowledge_paths" in data/config.json (e.g. "docs")

Retrieval is BM25 (stdlib, instant, no setup). If Ollama is enabled and
"rag_embeddings" is true, results are re-ranked with local embeddings.
Usage numbers are never put in here: those are structured and come from SQL.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import urllib.request
from pathlib import Path

STOP = set("""a an and are as at be but by for from has have i if in into is it its me my of on or our so
than that the their them then there these they this to too was we were what when where which while who
why will with you your yours not no do does did can could would should just very more most much""".split())

_COMMENT = re.compile(r"<!--.*?-->", re.S)


def tokenize(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOP or len(w) < 2:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


def parse_markdown(text: str) -> tuple[str, str, dict[str, str]]:
    """-> (title, tags, {section heading: body}) with HTML comments removed."""
    text = _COMMENT.sub("", text)
    title, tags, sections, cur, buf = "", "", {}, None, []
    for line in text.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.lower().startswith("tags:"):
            tags = line.split(":", 1)[1].strip()
        elif line.startswith("## "):
            if cur is not None:
                sections[cur] = "\n".join(buf).strip()
            cur, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf).strip()
    elif "\n".join(buf).strip():
        sections[""] = "\n".join(buf).strip()
    return title, tags, sections


class Knowledge:
    def __init__(self, root: Path, extra_paths: list[str] | None = None, history_dir: Path | None = None,
                 ollama: dict | None = None, embeddings: bool = False, profile_dir: Path | None = None):
        self.root = Path(root)
        self.dirs = [self.root / "knowledge"] + [self.root / p for p in (extra_paths or [])]
        if history_dir:
            self.dirs.append(Path(history_dir))
        self.profile_dir = Path(profile_dir) if profile_dir else None
        if self.profile_dir:
            self.dirs.append(self.profile_dir)
        self.ollama = ollama or {}
        self.use_embeddings = embeddings and self.ollama.get("enabled")
        self._sig = None
        self._lock = threading.Lock()
        self.docs: list[dict] = []
        self._vec: dict[str, list[float]] = {}

    # -- index -------------------------------------------------------------
    def _files(self) -> list[Path]:
        files = []
        for d in self.dirs:
            if d.exists():
                files += sorted(p for p in d.rglob("*.md") if p.is_file())
        return files

    def _kind(self, path: Path) -> str:
        for d in self.dirs:
            try:
                rel = path.relative_to(d)
            except ValueError:
                continue
            if d.name == "knowledge":
                return rel.parts[0] if len(rel.parts) > 1 else "notes"
            if self.profile_dir and d == self.profile_dir:
                return "me"  # your onboarding answers count as your own notes
            return "history" if d.name == "history" else d.name
        return "notes"

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def refresh(self):
        files = self._files()
        sig = tuple((str(p), p.stat().st_mtime_ns) for p in files)
        if sig == self._sig:
            return
        docs = []
        for p in files:
            try:
                title, tags, sections = parse_markdown(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            kind = self._kind(p)
            title = title or p.stem.replace("-", " ").title()
            if kind == "techniques":
                # One document per technique so a match returns the whole method.
                body = "\n\n".join(f"{h}\n{t}" for h, t in sections.items())
                docs.append({"kind": kind, "path": self._rel(p), "title": title, "section": "",
                             "text": body, "sections": sections, "tags": tags})
            else:
                for h, t in sections.items():
                    if len(t) < 3:
                        continue  # empty template prompt
                    docs.append({"kind": kind, "path": self._rel(p), "title": title, "section": h,
                                 "text": t, "sections": {h: t}, "tags": tags})
        for d in docs:
            weighted = f"{d['title']} {d['section']} " + (" " + d["tags"]) * 3 + " " + d["text"]
            d["tokens"] = tokenize(weighted)
        self.docs = docs
        n = max(len(docs), 1)
        df: dict[str, int] = {}
        for d in docs:
            for t in set(d["tokens"]):
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.avgdl = sum(len(d["tokens"]) for d in docs) / n
        self._sig = sig

    # -- search ------------------------------------------------------------
    def _bm25(self, q: list[str], d: dict, k1=1.5, b=0.75) -> float:
        tf: dict[str, int] = {}
        for t in d["tokens"]:
            tf[t] = tf.get(t, 0) + 1
        dl = len(d["tokens"])
        s = 0.0
        for t in q:
            if t in tf:
                f = tf[t]
                s += self.idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / self.avgdl))
        return s

    def _embed(self, text: str) -> list[float] | None:
        key = hashlib.sha1(text.encode()).hexdigest()
        if key in self._vec:
            return self._vec[key]
        try:
            req = urllib.request.Request(
                self.ollama["url"].rstrip("/") + "/api/embeddings",
                data=json.dumps({"model": self.ollama.get("embed_model", "nomic-embed-text"), "prompt": text[:2000]}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.ollama.get("timeout_s", 4)) as r:
                vec = json.loads(r.read()).get("embedding")
            if vec:
                self._vec[key] = vec
            return vec
        except Exception:
            return None

    @staticmethod
    def _cos(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1
        nb = math.sqrt(sum(y * y for y in b)) or 1
        return dot / (na * nb)

    def search(self, query: str, k: int = 3, kinds: list[str] | None = None, min_score: float = 0.0) -> list[dict]:
        with self._lock:
            self.refresh()
            q = tokenize(query)
            pool = [d for d in self.docs if not kinds or d["kind"] in kinds]
            scored = [(self._bm25(q, d), d) for d in pool]
            scored = [x for x in scored if x[0] > min_score]
            scored.sort(key=lambda x: -x[0])
            if self.use_embeddings and scored:
                qv = self._embed(query)
                if qv:
                    top = max(s for s, _ in scored) or 1
                    rer = []
                    for s, d in scored[: k * 4]:
                        dv = self._embed(d["text"])
                        rer.append((0.5 * s / top + 0.5 * (self._cos(qv, dv) if dv else 0), d))
                    scored = sorted(rer, key=lambda x: -x[0])
            return [{"kind": d["kind"], "path": d["path"], "title": d["title"], "section": d["section"],
                     "text": d["text"], "sections": d["sections"], "score": round(s, 3)} for s, d in scored[:k]]


def first_sentences(text: str, n: int = 2, limit: int = 320) -> str:
    parts = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    out = " ".join(parts[:n])
    return out if len(out) <= limit else out[: limit - 1].rsplit(" ", 1)[0] + "…"
