#!/usr/bin/env python3
"""
fast_context.py - LLM-free "Fast Context" code retrieval, with confidence scoring.

Shelldeck addon for semantic code search. Can be used standalone or via `sd search`.

    python -m shelldeck.addons.fast_context "lazy load"
    python -m shelldeck.addons.fast_context "where is the auth token validated" --root ~/code/app --top 5
    python -m shelldeck.addons.fast_context "retry with backoff" --ext py,ts --max-tokens 3000 --format json

How it works (same shape as the SWE-grep subagent in the docs: grep/read/glob tools,
parallel calls, at most 4 turns) but with heuristics instead of a model:

  INDEX   A persistent SQLite inverted index (token -> files, with term frequencies) plus a
          symbol table (functions/classes). It is built once (multi-process) and afterwards
          only files whose mtime/size changed are re-indexed, so repeat queries are ~instant.
  TURN 1  Query -> weighted terms: identifiers, quoted phrases, stemmed words, adjacent-word
          n-grams in every identifier style ("lazy load" -> lazyload / lazy_load / lazy-load).
          Terms are looked up in the index (no full-repo scan), ranked with BM25 + path +
          definition bonuses, then the top candidates are read in parallel threads to compute
          real line-level evidence (term proximity, definitions, coverage).
  TURN 2+ Only if confidence is not already high: synonyms + identifiers harvested from the
          best hits + import neighbours are added as new terms and everything is re-ranked.
          Stops early on high confidence or when the ranking stops changing.
  OUTPUT  Files, line ranges, enclosing symbol, numbered code, and a 0-100 confidence per file
          and for the whole answer, with human-readable reasons.
"""

from __future__ import annotations

import argparse
import bisect
import fnmatch
import hashlib
import heapq
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import accumulate

VERSION = 4                    # bump to force a re-index when the schema changes
MAX_TURNS = 4
MAX_TERMS_PER_TURN = 8         # new expansion terms per later turn
MAX_FILE_BYTES = 1_000_000
MAX_TOKENS_PER_TERM = 1500     # vocabulary matches considered per term
EARLY_EXIT_CONFIDENCE = 85.0
K1, B = 1.2, 0.75              # BM25

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", "dist",
    "build", "target", ".next", ".nuxt", ".idea", ".vscode", ".tox", ".mypy_cache",
    ".pytest_cache", ".gradle", "vendor", "coverage", ".cache", "site-packages", "dist-packages",
}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
              "composer.lock", "Pipfile.lock", "go.sum"}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf", ".zip", ".gz", ".tar",
    ".tgz", ".bz2", ".xz", ".7z", ".jar", ".class", ".exe", ".dll", ".so", ".dylib", ".o",
    ".a", ".pyc", ".pyo", ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4", ".mov", ".avi",
    ".wasm", ".lock", ".map", ".svg", ".sqlite", ".db", ".bin", ".pkl", ".npy", ".parquet",
}
STOPWORDS = set("""
a an the and or but if then else of to in on at by for with from into onto as is are was were be been
being do does did done doing have has had having it its this that these those there here where when
what which who whom how why can could should would will shall may might must not no yes we our us you
your they them their i me my find show get set use used using make made makes code function functions
class classes method methods file files implement implementation implemented handle handles handling
logic please about all any some each every
""".split())
CODE_KEYWORDS = set("""
def class return import from self this true false none null undefined var let const function async
await public private protected static void int string bool boolean float double char long short new
else elif while for try catch except finally raise throw throws lambda yield with pass break continue
package interface extends implements struct enum type impl trait export default module require print
println args kwargs cls
""".split())

# tiny rule-based stand-in for an LLM's semantic knowledge
SYNONYMS = {
    "lazy": ["defer", "deferred", "ondemand", "dynamic"],
    "load": ["import", "fetch", "require", "init"],
    "auth": ["authenticate", "login", "token", "session", "credential"],
    "authentication": ["auth", "login", "token", "session"],
    "authorization": ["permission", "role", "policy", "acl"],
    "config": ["configuration", "settings", "options", "env"],
    "db": ["database", "sql", "query", "orm"],
    "database": ["db", "sql", "query", "orm"],
    "error": ["exception", "raise", "throw", "fail"],
    "cache": ["memoize", "ttl", "lru", "store"],
    "log": ["logger", "logging", "trace"],
    "test": ["spec", "mock", "assert", "fixture"],
    "delete": ["remove", "destroy", "drop"],
    "create": ["build", "make", "init", "construct"],
    "parse": ["decode", "deserialize", "tokenize"],
    "render": ["draw", "paint", "display"],
    "route": ["router", "endpoint", "handler", "url"],
    "async": ["await", "promise", "future", "coroutine"],
    "send": ["emit", "dispatch", "publish"],
    "retry": ["backoff", "attempt", "reconnect"],
    "validate": ["verify", "check", "sanitize", "schema"],
    "queue": ["worker", "job", "task"],
    "http": ["request", "response", "client"],
    "thread": ["worker", "pool", "lock", "concurrent"],
    "serialize": ["encode", "dump", "marshal"],
}

TEST_RX = re.compile(
    r"(^|/)(tests?|__tests__|specs?|e2e|fixtures?|mocks?|__mocks__|testdata)(/|$)"
    r"|(^|/)test_[^/]*$|_test\.[a-z0-9]+$|[._-](test|spec)\.[a-z0-9]+$")
GENERATED_RX = re.compile(r"\.min\.|\.generated\.|_pb2\.py$|\.pb\.go$|(^|/)vendor/")

LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx",
    ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php", ".c": "c",
    ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".cs": "csharp", ".kt": "kotlin",
    ".swift": "swift", ".scala": "scala", ".sh": "bash", ".sql": "sql", ".md": "markdown",
    ".json": "json", ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".html": "html",
    ".css": "css", ".vue": "vue", ".lua": "lua", ".dart": "dart",
}

IDENT_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
HARVEST_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
CAMEL_RX = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")

_MODS = (r"(?:(?:export|default|pub(?:\([\w:]+\))?|public|private|protected|internal|static|async|"
         r"abstract|final|override|unsafe|extern|const|declare)[ \t]+)*")
SYMBOL_RX = re.compile(
    r"^[ \t]*" + _MODS +
    r"(?P<kind>def|class|function\*?|func|fn|interface|struct|enum|trait|type|object|module)"
    r"[ \t]+(?:\([^)\n]*\)[ \t]*)?(?P<name>[A-Za-z_$][\w$]*)"
    r"|^[ \t]*(?:export[ \t]+)?(?:const|let|var)[ \t]+(?P<cname>[A-Za-z_$][\w$]*)[ \t]*"
    r"(?::[^=\n]+)?=[ \t]*(?:async[ \t]*)?(?:\([^)\n]*\)|\w+)[ \t]*=>",
    re.M)
CLASS_KINDS = {"class", "interface", "struct", "enum", "trait", "object"}

PY_IMPORT_RX = re.compile(r"^\s*(?:from\s+([.\w]+)\s+import|import\s+([\w.]+))")
JS_IMPORT_RX = re.compile(r"""(?:from|require\(|import\()\s*['"](\.{1,2}/[^'"]+)['"]""")


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def split_identifier(s: str) -> list[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [p.lower() for p in re.split(r"[^A-Za-z0-9]+", s) if p]


def stem(w: str) -> str:
    w = w.lower()
    for suf in ("ations", "ation", "ings", "ing", "ers", "er", "ies", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    if w.endswith("e") and len(w) >= 5:
        return w[:-1]
    return w


def looks_like_identifier(tok: str) -> bool:
    return ("_" in tok or re.search(r"[a-z][A-Z]", tok) is not None
            or re.search(r"\w\.\w", tok) is not None or "/" in tok)


def language_of(path: str) -> str:
    return LANG_BY_EXT.get(os.path.splitext(path)[1].lower(), "")


# --------------------------------------------------------------------------- #
# Indexing (runs in worker processes)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=300_000)
def _subwords(ident: str) -> tuple[str, ...]:
    return tuple(p.lower() for p in CAMEL_RX.findall(ident) if len(p) >= 2 and not p.isdigit())


def index_text(text: str) -> tuple[dict[str, int], int]:
    """Token -> term frequency. Whole identifiers AND their camel/snake sub-words are indexed."""
    raw = Counter(IDENT_RX.findall(text))     # C-speed pass; then work on unique identifiers only
    tf: Counter = Counter()
    total = 0
    for ident, n in raw.items():
        total += n
        if len(ident) > 64:
            continue
        low = ident.lower()
        if len(low) >= 2:
            tf[low] += n
        subs = _subwords(ident)
        if len(subs) > 1 or (subs and subs[0] != low):
            for s in subs:
                tf[s] += n
    return dict(tf), total


_NOT_SYMBOL = STOPWORDS | CODE_KEYWORDS | {"in", "is", "of", "on", "at", "by", "as", "or", "to"}


def sym_match(line: str):
    """(name, kind) if the line declares a function/class-like symbol, else None."""
    m = SYMBOL_RX.match(line)
    if not m:
        return None
    name = m.group("name") or m.group("cname")
    if not name or name.lower() in _NOT_SYMBOL:
        return None
    return name, (m.group("kind") or "function").rstrip("*")


@lru_cache(maxsize=100_000)
def name_parts(name: str) -> tuple[str, ...]:
    return tuple(split_identifier(name))


def extract_symbols(text: str) -> list[tuple[str, str, int]]:
    out, last_pos, line = [], 0, 1
    for m in SYMBOL_RX.finditer(text):
        line += text.count("\n", last_pos, m.start())
        last_pos = m.start()
        name = m.group("name") or m.group("cname")
        if name and name.lower() not in _NOT_SYMBOL:
            out.append((name, (m.group("kind") or "function").rstrip("*"), line))
            if len(out) >= 3000:
                break
    return out


def _index_worker(args):
    root, rel = args
    full = os.path.join(root, rel)
    try:
        st = os.stat(full)
        if st.st_size == 0 or st.st_size > MAX_FILE_BYTES:
            return rel, st.st_mtime_ns, st.st_size, None
        with open(full, "rb") as fh:
            data = fh.read()
    except OSError:
        return rel, 0, 0, None
    if b"\0" in data[:4096] or len(data) / (data.count(b"\n") + 1) > 400:   # binary / minified
        return rel, st.st_mtime_ns, st.st_size, None
    text = data.decode("utf-8", "replace")
    tf, ntok = index_text(text)
    return rel, st.st_mtime_ns, st.st_size, (tf, ntok, extract_symbols(text))


def list_files(root: str) -> list[str]:
    files = None
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z", "-co", "--exclude-standard"],
                             capture_output=True, timeout=60)
        if out.returncode == 0 and out.stdout:
            files = out.stdout.decode("utf-8", "replace").split("\0")
    except (OSError, subprocess.SubprocessError):
        pass
    if files is None:
        files, stack = [], [root]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            if e.name not in IGNORE_DIRS and not e.name.startswith("."):
                                stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            files.append(os.path.relpath(e.path, root))
            except OSError:
                continue
    keep = []
    for f in files:
        if not f:
            continue
        f = f.replace("\\", "/")
        parts = f.split("/")
        name = parts[-1]
        if any(p in IGNORE_DIRS or p.endswith((".dist-info", ".egg-info")) for p in parts[:-1]) \
                or name in SKIP_NAMES:
            continue
        if os.path.splitext(name)[1].lower() in BINARY_EXT or name.endswith(".min.js"):
            continue
        keep.append(f)
    return keep


def cache_path(root: str) -> str:
    base = os.environ.get("FC_CACHE_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"), "fastcontext")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        base = os.path.join(tempfile.gettempdir(), "fastcontext-cache")
        os.makedirs(base, exist_ok=True)
    return os.path.join(base, hashlib.sha1(root.encode()).hexdigest()[:16] + ".sqlite")


class Blob:
    """Substring search across many short strings using ONE big str (C-speed str.find)."""
    __slots__ = ("blob", "starts")

    def __init__(self, items: list[str]):
        self.blob = "\n" + "\n".join(items) + "\n"
        self.starts = list(accumulate((len(s) + 1 for s in items), initial=1))

    def find(self, sub: str, limit: int = 20000) -> list[int]:
        blob, starts, out = self.blob, self.starts, []
        pos = blob.find(sub)
        while pos != -1:
            i = bisect.bisect_right(starts, pos) - 1
            out.append(i)
            if len(out) >= limit:
                break
            pos = blob.find(sub, starts[i + 1])
        return out


class Index:
    """Persistent, incrementally-updated inverted index (SQLite)."""

    def __init__(self, root: str, use_cache: bool = True, reindex: bool = False, log=lambda *a: None):
        self.root, self.log = root, log
        self.path = cache_path(root) if use_cache else ":memory:"
        if reindex and use_cache:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(self.path + suffix)
                except OSError:
                    pass
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("PRAGMA cache_size=-131072")
        if use_cache:
            self.db.execute("PRAGMA journal_mode=WAL")
        self._schema()

    def _schema(self):
        db = self.db
        db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
        row = db.execute("SELECT v FROM meta WHERE k='version'").fetchone()
        if row and row[0] != str(VERSION):
            db.executescript("DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS vocab; "
                             "DROP TABLE IF EXISTS postings; DROP TABLE IF EXISTS symbols; DELETE FROM meta;")
            row = None
        db.executescript("""
            CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, path TEXT UNIQUE, mtime INTEGER, size INTEGER, ntok INTEGER);
            CREATE TABLE IF NOT EXISTS postings(tid INTEGER, fid INTEGER, tf INTEGER, PRIMARY KEY(tid, fid)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS postings_fid ON postings(fid);
            CREATE TABLE IF NOT EXISTS symbols(fid INTEGER, name TEXT, lname TEXT, kind TEXT, line INTEGER);
            CREATE INDEX IF NOT EXISTS symbols_fid ON symbols(fid);
            CREATE INDEX IF NOT EXISTS symbols_lname ON symbols(lname);
        """)
        if not row:
            db.execute("INSERT OR REPLACE INTO meta VALUES('version', ?)", (str(VERSION),))
        db.commit()

    def _load_vocab(self) -> list[str]:
        """Vocabulary is one newline-joined string in meta; token i has id i+1 (append-only)."""
        row = self.db.execute("SELECT v FROM meta WHERE k='vocab'").fetchone()
        return row[0].split("\n") if row and row[0] else []

    # ---- incremental sync ------------------------------------------------- #
    def sync(self) -> dict:
        db = self.db
        now = {}
        for rel in list_files(self.root):
            try:
                st = os.stat(os.path.join(self.root, rel))
            except OSError:
                continue
            now[rel] = (st.st_mtime_ns, st.st_size)
        known = {p: (i, m, s) for i, p, m, s in db.execute("SELECT id, path, mtime, size FROM files")}
        changed = [p for p, ms in now.items() if p not in known or known[p][1:] != ms]
        removed = [known[p][0] for p in known if p not in now]
        stats = {"files": len(now), "changed": len(changed), "removed": len(removed)}

        for i in range(0, len(removed), 500):
            chunk = removed[i:i + 500]
            q = ",".join("?" * len(chunk))
            for table, col in (("postings", "fid"), ("symbols", "fid"), ("files", "id")):
                db.execute(f"DELETE FROM {table} WHERE {col} IN ({q})", chunk)

        if changed:
            if len(changed) >= 500:
                print(f"[FastContext] indexing {len(changed):,} files (cached for next time)...", file=sys.stderr)
            args = [(self.root, p) for p in changed]
            if len(changed) > 300 and (os.cpu_count() or 1) > 1:
                with ProcessPoolExecutor(max_workers=min(os.cpu_count() or 2, 8)) as ex:
                    results = list(ex.map(_index_worker, args, chunksize=64))
            else:
                results = [_index_worker(a) for a in args]

            bulk = len(changed) >= 1500
            if bulk:      # cold build: sorted bulk insert is far faster than random B-tree inserts
                old = [known[p][0] for p in changed if p in known]
                for i in range(0, len(old), 500):
                    chunk = old[i:i + 500]
                    q = ",".join("?" * len(chunk))
                    db.execute(f"DELETE FROM postings WHERE fid IN ({q})", chunk)
                    db.execute(f"DELETE FROM symbols WHERE fid IN ({q})", chunk)
                db.execute("DROP INDEX IF EXISTS postings_fid")
                db.execute("CREATE TEMP TABLE post_stage(tid INTEGER, fid INTEGER, tf INTEGER)")
            target = "post_stage" if bulk else "postings"
            vtoks = self._load_vocab()
            tid_map = {tok: i + 1 for i, tok in enumerate(vtoks)}
            next_tid = len(vtoks) + 1
            vocab_grew = False
            for rel, mtime, size, payload in results:
                if rel in known:
                    fid = known[rel][0]
                    if not bulk:
                        db.execute("DELETE FROM postings WHERE fid=?", (fid,))
                        db.execute("DELETE FROM symbols WHERE fid=?", (fid,))
                    db.execute("UPDATE files SET mtime=?, size=?, ntok=? WHERE id=?",
                               (mtime, size, payload[1] if payload else 0, fid))
                else:
                    fid = db.execute("INSERT INTO files(path, mtime, size, ntok) VALUES (?,?,?,?)",
                                     (rel, mtime, size, payload[1] if payload else 0)).lastrowid
                if not payload:
                    continue
                tf, _, syms = payload
                rows = []
                for tok, n in tf.items():
                    tid = tid_map.get(tok)
                    if tid is None:
                        tid = tid_map[tok] = next_tid
                        next_tid += 1
                        vtoks.append(tok)
                        vocab_grew = True
                    rows.append((tid, fid, n))
                db.executemany(f"INSERT INTO {target} VALUES (?,?,?)", rows)
                if syms:
                    db.executemany("INSERT INTO symbols VALUES (?,?,?,?,?)",
                                   [(fid, n, n.lower(), k, ln) for n, k, ln in syms])
            if vocab_grew:
                db.execute("INSERT OR REPLACE INTO meta VALUES('vocab', ?)", ("\n".join(vtoks),))
            if bulk:
                db.execute("INSERT INTO postings SELECT tid, fid, tf FROM post_stage ORDER BY tid, fid")
                db.execute("DROP TABLE post_stage")
                db.execute("CREATE INDEX IF NOT EXISTS postings_fid ON postings(fid)")
        db.commit()
        return stats

    # ---- in-memory views used at query time -------------------------------- #
    def load(self):
        rows = self.db.execute("SELECT id, path, ntok FROM files").fetchall()
        self.paths = {i: p for i, p, _ in rows}
        self.ntok = {i: n for i, _, n in rows}
        indexed = [n for _, _, n in rows if n > 0]
        self.N = max(1, len(indexed))
        self.avgdl = (sum(indexed) / len(indexed)) if indexed else 1.0
        self.fids = [i for i, _, _ in rows]
        self.path_blob = Blob([p.lower() for _, p, _ in rows])
        self.vtoks = self._load_vocab()
        self.vocab = Blob(self.vtoks)

    def postings(self, tids: list[int]):
        for i in range(0, len(tids), 500):
            chunk = tids[i:i + 500]
            q = ",".join("?" * len(chunk))
            yield from self.db.execute(f"SELECT fid, tid, tf FROM postings WHERE tid IN ({q})", chunk)

    def symbols_by_lname(self, names: list[str]):
        for i in range(0, len(names), 500):
            chunk = names[i:i + 500]
            q = ",".join("?" * len(chunk))
            yield from self.db.execute(
                f"SELECT fid, name, kind, line FROM symbols WHERE lname IN ({q})", chunk)

    def stats(self) -> dict:
        c = self.db.execute
        return {
            "db": self.path,
            "files": c("SELECT COUNT(*) FROM files").fetchone()[0],
            "vocab": len(self._load_vocab()),
            "postings": c("SELECT COUNT(*) FROM postings").fetchone()[0],
            "symbols": c("SELECT COUNT(*) FROM symbols").fetchone()[0],
            "db_mb": round(os.path.getsize(self.path) / 1e6, 1) if os.path.exists(self.path) else 0,
        }


# --------------------------------------------------------------------------- #
# Query terms
# --------------------------------------------------------------------------- #
@dataclass
class Term:
    key: str
    raw: str
    weight: float
    kind: str                                   # word | ident | ngram | syn | expand
    index_variants: list[str]
    text_variants: list[str]
    covers: list[str] = field(default_factory=list)   # ngram: keys of the word terms it spans
    tf: dict = field(default_factory=dict)            # fid -> quality-weighted term frequency
    syms: list = field(default_factory=list)          # (fid, name, kind, line)
    path_fids: set = field(default_factory=set)
    df: int = 0
    idf: float = 0.0
    looked_up: bool = False
    short: bool = False
    rx: re.Pattern | None = None

    def __post_init__(self):
        self.short = len(self.key) <= 3 and self.kind in ("word", "syn", "expand")
        if self.short:
            self.rx = re.compile(r"(?<![a-z0-9])" + re.escape(self.key) + r"(?![a-z0-9])")

    @property
    def in_query(self) -> bool:
        return self.kind in ("word", "ident")


def make_word(raw: str, weight: float | None = None, kind: str = "word") -> Term:
    k = stem(raw) if kind == "word" else raw
    w = weight if weight is not None else min(1.5, 0.8 + len(k) / 12)
    return Term(k, raw, w, kind, [k], [k])


def make_ngram(words: list[str], weight: float) -> Term:
    stems = [stem(w) for w in words]
    variants = set()
    for seq in (words, stems):
        variants |= {"".join(seq), "_".join(seq), "-".join(seq), " ".join(seq)}
    idx = [v for v in variants if re.fullmatch(r"[a-z0-9_]+", v)]
    return Term("~" + "_".join(words), " ".join(words), weight, "ngram", idx, sorted(variants),
                covers=stems)


def make_ident(text: str, weight: float = 3.0) -> Term:
    low = text.lower()
    if re.fullmatch(r"[a-z0-9_$]+", low):
        idx = [low]
    else:
        parts = sorted((p for p in re.split(r"[^a-z0-9_$]+", low) if len(p) >= 3), key=len, reverse=True)
        idx = parts[:1]
    return Term(low, text, weight, "ident", idx, [low])


# --------------------------------------------------------------------------- #
# File data + line-level scanning
# --------------------------------------------------------------------------- #
class FileData:
    __slots__ = ("lines", "low", "offs")

    def __init__(self, text: str):
        self.lines = text.split("\n")
        low = text.lower()
        if len(low) != len(text):   # rare unicode case-mapping length changes: keep offsets valid
            low = "".join(c.lower() if len(c.lower()) == 1 else c for c in text)
        self.low = low
        self.offs = list(accumulate(len(l) + 1 for l in self.lines))   # offs[i] = start of line i+1


class FileAnalysis:
    __slots__ = ("fd", "term_lines", "line_w")

    def __init__(self):
        self.fd: FileData | None = None
        self.term_lines: dict[str, list[int]] = {}
        self.line_w: dict[int, float] = {}


def find_lines(term: Term, fd: FileData, cap: int = 1500) -> list[int]:
    low, offs, out = fd.low, fd.offs, set()
    if term.rx is not None:
        for m in term.rx.finditer(low):
            out.add(bisect.bisect_right(offs, m.start()))
            if len(out) >= cap:
                break
    else:
        for v in term.text_variants:
            pos = low.find(v)
            while pos != -1 and len(out) < cap:
                ln = bisect.bisect_right(offs, pos)
                out.add(ln)
                if ln >= len(offs):
                    break
                pos = low.find(v, offs[ln])
    return sorted(out)


def best_window(hits: dict[int, set], wmap: dict[str, float], span: int = 8) -> tuple[float, int]:
    lines = sorted(hits)
    if len(lines) > 400:
        lines = sorted(sorted(lines, key=lambda l: -len(hits[l]))[:400])
    best, best_line = 0.0, -1
    for i, li in enumerate(lines):
        acc, k = set(), i
        while k < len(lines) and lines[k] - li <= span:
            acc |= hits[lines[k]]
            k += 1
        w = sum(wmap.get(t, 0.0) for t in acc)
        if w > best:
            best, best_line = w, li
    return best, best_line


def enclosing_symbol(lines: list[str], idx: int, max_back: int = 300) -> str:
    lo = max(0, idx - max_back)
    for i in range(idx, lo - 1, -1):
        m = sym_match(lines[i])
        if m:
            name = m[0]
            ind = len(lines[i]) - len(lines[i].lstrip(" \t"))
            if ind > 0:
                for j in range(i - 1, lo - 1, -1):
                    mj = sym_match(lines[j])
                    if mj and mj[1] in CLASS_KINDS and \
                            (len(lines[j]) - len(lines[j].lstrip(" \t"))) < ind:
                        return f"{mj[0]}.{name}"
            return name
    return ""


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class Snippet:
    start: int
    end: int
    hit_lines: list[int]
    text: list[str]
    symbol: str = ""
    terms: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class Scored:
    fid: int
    path: str
    base: float
    final: float = 0.0
    cov: float = 0.0
    prox: float = 0.0
    prox_cov: float = 0.0
    prox_line: int = -1
    defsig: float = 0.0
    defsym: tuple | None = None
    pathsig: float = 0.0
    spec: float = 0.0
    strength: float = 0.0
    confidence: float = 0.0
    present: list[str] = field(default_factory=list)
    snippets: list[Snippet] = field(default_factory=list)


def label_of(c: float) -> str:
    return "high" if c >= 75 else "medium" if c >= 50 else "low" if c >= 30 else "very_low"


def file_confidence(s: Scored) -> float:
    raw = 0.32 * s.cov + 0.26 * s.prox + 0.18 * s.defsig + 0.08 * s.pathsig + 0.16 * s.strength
    return round(100 * min(1.0, raw * (0.8 + 0.2 * s.spec)), 1)


# --------------------------------------------------------------------------- #
# The retriever
# --------------------------------------------------------------------------- #
class Retriever:
    def __init__(self, root, *, use_cache=True, reindex=False, max_turns=MAX_TURNS, context=3,
                 per_file=3, include_tests=False, exts=None, globs=None, thorough=False, verbose=False):
        self.root = os.path.abspath(root)
        self.verbose = verbose
        self.ctx, self.per_file, self.thorough = context, per_file, thorough
        self.max_turns = min(max_turns, MAX_TURNS)
        self.include_tests = include_tests
        self.exts = {e.strip().lower().lstrip(".") for e in (exts or []) if e.strip()}
        self.globs = [g.strip() for g in (globs or []) if g.strip()]
        self.workers = min(16, (os.cpu_count() or 4) * 2)
        self.idx = Index(self.root, use_cache, reindex)
        self.terms: dict[str, Term] = {}
        self.analysis: dict[int, FileAnalysis] = {}
        self.import_boost: dict[str, float] = {}
        self._allowed: dict[int, bool] = {}
        self._suffix_map: dict[str, list[str]] | None = None
        self.file_syms: dict = {}
        self.base: dict[int, float] = {}
        self.timings: dict[str, float] = defaultdict(float)
        self.turns_used = 0
        self.converged = True

    def log(self, *a):
        if self.verbose:
            print(*a, file=sys.stderr)

    # ---- query parsing --------------------------------------------------- #
    def build_terms(self, query: str) -> None:
        T = self.terms

        def add(t: Term):
            if t.key not in T:
                T[t.key] = t

        for a, b in re.findall(r'"([^"]+)"|`([^`]+)`', query):
            phrase = (a or b).strip().lower()
            words = [w for w in re.split(r"[^a-z0-9]+", phrase) if len(w) >= 2]
            if len(words) >= 2:
                add(make_ngram(words[:3], 3.0))
            for w in words:
                if w not in STOPWORDS:
                    add(make_word(w))
        q = re.sub(r'"[^"]+"|`[^`]+`', " ", query)

        run: list[str] = []
        ngrams: list[Term] = []

        def flush():
            nonlocal run
            if len(run) >= 2:
                for i in range(len(run) - 1):
                    ngrams.append(make_ngram(run[i:i + 2], 2.0))
                if len(run) >= 3:
                    ngrams.append(make_ngram(run[:3], 2.5))
            run = []

        for tok in q.split():
            tok = tok.strip(",;:!?[]{}<>\"'").rstrip(".")
            cleaned = tok[:-2] if tok.endswith("()") else tok
            if not cleaned:
                continue
            if len(cleaned) >= 3 and looks_like_identifier(cleaned):
                flush()
                add(make_ident(cleaned))
                for part in split_identifier(cleaned):
                    if len(part) >= 3 and part not in STOPWORDS:
                        add(make_word(part, 0.7))
                continue
            for w in re.split(r"[^A-Za-z0-9]+", cleaned):
                wl = w.lower()
                if len(wl) < 2 or wl.isdigit() or wl in STOPWORDS or stem(wl) in STOPWORDS:
                    flush()
                    continue
                add(make_word(wl))
                run.append(wl)
        flush()
        for ng in ngrams[:5]:
            add(ng)

    @property
    def qterms(self) -> list[Term]:
        return [t for t in self.terms.values() if t.in_query and t.weight > 0]

    # ---- index lookups (the "grep/glob" step) ---------------------------- #
    def lookup(self, t: Term) -> None:
        idx = self.idx
        quality: dict[int, float] = {}
        for v in t.index_variants:
            for i in idx.vocab.find(v):
                tok = idx.vtoks[i]
                if t.short and tok != v:
                    continue
                q = 1.0 if tok == v else 0.8 if tok.startswith(v) else 0.65 if tok.endswith(v) else 0.5
                if q > quality.get(i, 0.0):
                    quality[i] = q
        items = sorted(quality.items(), key=lambda kv: (-kv[1], len(idx.vtoks[kv[0]])))[:MAX_TOKENS_PER_TERM]
        tid_q = {i + 1: q for i, q in items}
        tf: dict[int, float] = defaultdict(float)
        for fid, tid, n in idx.postings(list(tid_q)):
            tf[fid] += tid_q[tid] * n
        t.tf, t.df = tf, len(tf)
        t.idf = math.log(1 + (idx.N - t.df + 0.5) / (t.df + 0.5))
        t.syms = list(idx.symbols_by_lname([idx.vtoks[i] for i, _ in items]))
        if not t.short:
            fids = set()
            for v in t.text_variants:
                fids.update(idx.fids[i] for i in idx.path_blob.find(v, limit=5000))
            t.path_fids = fids
        if t.kind in ("syn", "expand") and t.df > 0.15 * idx.N:
            t.weight = 0.0                               # too common to be informative
        t.looked_up = True

    # ---- filters / priors ------------------------------------------------ #
    def allowed(self, fid: int) -> bool:
        ok = self._allowed.get(fid)
        if ok is None:
            p = self.idx.paths[fid]
            ok = True
            if self.exts and os.path.splitext(p)[1].lower().lstrip(".") not in self.exts:
                ok = False
            if ok and self.globs and not any(fnmatch.fnmatch(p, g) for g in self.globs):
                ok = False
            self._allowed[fid] = ok
        return ok

    def prior(self, pl: str) -> float:
        p = 1.0
        if not self.include_tests and TEST_RX.search(pl):
            p *= 0.6
        if pl.endswith((".md", ".rst", ".txt")):
            p *= 0.8
        if GENERATED_RX.search(pl):
            p *= 0.5
        return p

    # ---- reading + scanning (the "read" step, threaded) ------------------- #
    def read_fd(self, rel: str) -> FileData | None:
        try:
            with open(os.path.join(self.root, rel), "rb") as fh:
                data = fh.read(MAX_FILE_BYTES + 1)
        except OSError:
            return None
        if len(data) > MAX_FILE_BYTES or b"\0" in data[:4096]:
            return None
        return FileData(data.decode("utf-8", "replace"))

    def prepare(self, fid: int) -> None:
        fa = self.analysis.get(fid)
        if fa is None:
            fa = self.analysis[fid] = FileAnalysis()
            fa.fd = self.read_fd(self.idx.paths[fid])
        if fa.fd is None:
            return
        for t in self.terms.values():
            if t.looked_up and t.key not in fa.term_lines:
                fa.term_lines[t.key] = find_lines(t, fa.fd)

    # ---- ranking ---------------------------------------------------------- #
    def rank(self, top_k: int) -> list[Scored]:
        idx = self.idx
        t0 = time.perf_counter()
        base: dict[int, float] = defaultdict(float)
        for t in self.terms.values():
            if not t.looked_up or t.weight <= 0:
                continue
            wi = t.weight * t.idf
            for fid, tf in t.tf.items():
                base[fid] += wi * tf * (K1 + 1) / (tf + K1 * (1 - B + B * idx.ntok[fid] / idx.avgdl))
            for fid in t.path_fids:
                base[fid] += wi * 1.5
            seen: Counter = Counter()
            for fid, _, _, _ in t.syms:
                if seen[fid] < 3:
                    seen[fid] += 1
                    base[fid] += wi * 1.2
        pool = [(b * self.prior(idx.paths[f].lower()), f) for f, b in base.items() if self.allowed(f)]
        pool = heapq.nlargest(max(40, top_k * 5), pool)
        self.base = {f: b for b, f in pool}
        self.timings["rank_index"] += time.perf_counter() - t0

        t1 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            list(ex.map(self.prepare, [f for _, f in pool]))
        self.timings["read_scan"] += time.perf_counter() - t1

        self.file_syms = defaultdict(lambda: defaultdict(set))
        for t in self.terms.values():
            keys = {t.key} if t.in_query else set(t.covers) if t.kind == "ngram" else set()
            if keys:
                for fid, name, kind, line in t.syms:
                    if t.kind == "word" and not any(w.startswith(t.key) for w in name_parts(name)):
                        continue
                    if t.kind == "ident" and name.lower() != t.key:
                        continue
                    self.file_syms[fid][(name, line, kind)] |= keys

        scored = [self.score_file(f, b) for b, f in pool]
        scored.sort(key=lambda s: s.final, reverse=True)
        scored = scored[:top_k]
        top = scored[0].final if scored else 1.0
        for s in scored:
            s.strength = s.final / top if top else 0.0
            s.confidence = file_confidence(s)
        return scored

    def score_file(self, fid: int, b: float) -> Scored:
        idx = self.idx
        fa = self.analysis[fid]
        path = idx.paths[fid]
        qterms = self.qterms
        total_w = sum(t.weight for t in qterms) or 1.0
        wmap = {t.key: t.weight for t in qterms}

        present = [t for t in qterms
                   if fa.term_lines.get(t.key) or fid in t.path_fids or (fa.fd is None and fid in t.tf)]
        cov = sum(t.weight for t in present) / total_w
        qhits: dict[int, set] = defaultdict(set)
        for t in qterms:
            for ln in fa.term_lines.get(t.key, ()):
                qhits[ln].add(t.key)
        bw, bline = best_window(qhits, wmap) if qhits else (0.0, -1)
        prox_cov = bw / total_w
        prox = prox_cov * (0.6 + 0.4 * min(1.0, len(qhits) / 6))

        lw: dict[int, float] = defaultdict(float)
        for t in self.terms.values():
            if t.weight <= 0:
                continue
            for ln in fa.term_lines.get(t.key, ()):
                lw[ln] += t.weight * t.idf
        if fa.fd is not None:
            for ln in list(lw):
                if sym_match(fa.fd.lines[ln]):
                    lw[ln] *= 2.5
        fa.line_w = lw

        defsig, defsym = 0.0, None
        for sym, keys in self.file_syms.get(fid, {}).items():
            f = sum(wmap.get(k, 0.0) for k in keys) / total_w
            if f > defsig:
                defsig, defsym = f, sym
        pathsig = sum(t.weight for t in qterms if fid in t.path_fids) / total_w
        n_idf = math.log(1 + idx.N)
        norm = [min(1.0, t.idf / n_idf) for t in present]
        spec = (0.5 * sum(norm) / len(norm) + 0.5 * max(norm)) if norm else 0.0

        final = b * (0.5 + cov) * (1 + 1.5 * prox) * self.import_boost.get(path, 1.0)
        return Scored(fid, path, b, final, cov, prox, prox_cov, bline, min(1.0, defsig), defsym,
                      min(1.0, pathsig), spec, present=[t.raw for t in present])

    # ---- snippets ---------------------------------------------------------- #
    def build_snippets(self, s: Scored) -> None:
        fa = self.analysis[s.fid]
        if fa.fd is None:
            return
        lines, lw = fa.fd.lines, fa.line_w
        if not lw:
            s.snippets = [Snippet(1, min(len(lines), 2 * self.ctx + 1), [], lines[:2 * self.ctx + 1])]
            return
        hit_lines = sorted(lw)
        win: dict[int, float] = {}
        lo = 0
        acc = 0.0
        # sliding window sum over +/-6 lines
        hi = 0
        for i in hit_lines:
            while hi < len(hit_lines) and hit_lines[hi] <= i + 6:
                acc += lw[hit_lines[hi]]
                hi += 1
            while hit_lines[lo] < i - 6:
                acc -= lw[hit_lines[lo]]
                lo += 1
            win[i] = acc
        picked: list[int] = []
        for i in sorted(win, key=lambda i: win[i] + 2 * lw[i], reverse=True):
            if all(abs(i - p) > 2 * self.ctx for p in picked):
                picked.append(i)
            if len(picked) >= self.per_file:
                break
        ranges = sorted((max(0, p - self.ctx), min(len(lines) - 1, p + self.ctx)) for p in picked)
        merged: list[list[int]] = []
        for a, b in ranges:
            if merged and a <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        qterms = self.qterms
        for a, b in merged:
            inside = [i for i in hit_lines if a <= i <= b]
            best = max(inside, key=lw.get)
            terms = [t.raw for t in qterms
                     if any(a <= ln <= b for ln in fa.term_lines.get(t.key, ()))]
            s.snippets.append(Snippet(a + 1, b + 1, [i + 1 for i in inside], lines[a:b + 1],
                                      enclosing_symbol(lines, best), terms,
                                      round(sum(lw[i] for i in inside), 2)))

    # ---- expansion (replaces the model's "next move") --------------------- #
    def expand(self, results: list[Scored], first: bool) -> int:
        known = set(self.terms) | {stem(k) for k in self.terms}
        new: list[Term] = []
        if first:
            for t in self.qterms:
                if t.kind != "word":
                    continue
                for syn in SYNONYMS.get(t.raw, ()) or SYNONYMS.get(t.key, ()):
                    if syn not in known and len(new) < 4:
                        new.append(make_word(syn, 0.5, "syn"))
                        known.add(syn)
        counter: Counter = Counter()
        for s in results[:5]:
            fa = self.analysis.get(s.fid)
            if not fa or fa.fd is None:
                continue
            lines = fa.fd.lines
            for ln in sorted(fa.line_w, key=fa.line_w.get, reverse=True)[:8]:
                w = fa.line_w[ln]
                for a in range(max(0, ln - 1), min(len(lines), ln + 2)):
                    mult = 2.0 if sym_match(lines[a]) else 1.0
                    for ident in HARVEST_RX.findall(lines[a]):
                        low = ident.lower()
                        if low in CODE_KEYWORDS or low in STOPWORDS or low in known or stem(low) in known:
                            continue
                        counter[low] += w * mult
        for low, _ in counter.most_common(MAX_TERMS_PER_TURN * 3):
            if len(new) >= MAX_TERMS_PER_TURN:
                break
            new.append(Term(low, low, 0.4, "expand", [low], [low]))
            known.add(low)
        for t in new:
            self.terms[t.key] = t
        self.follow_imports(results[:3])
        return len(new)

    def follow_imports(self, results: list[Scored]) -> None:
        fileset = set(self.idx.paths.values())
        for s in results:
            fa = self.analysis.get(s.fid)
            if not fa or fa.fd is None:
                continue
            base_dir = os.path.dirname(s.path)
            for line in fa.fd.lines[:300]:
                targets: list[str] = []
                m = PY_IMPORT_RX.match(line)
                if m:
                    dotted = m.group(1) or m.group(2)
                    if dotted.startswith("."):
                        up = len(dotted) - len(dotted.lstrip("."))
                        d = base_dir
                        for _ in range(up - 1):
                            d = os.path.dirname(d)
                        rel = os.path.normpath(os.path.join(d, dotted.lstrip(".").replace(".", "/")))
                        targets += [rel + ".py", rel + "/__init__.py"]
                    else:
                        if self._suffix_map is None:
                            self._suffix_map = defaultdict(list)
                            for p in fileset:
                                if p.endswith(".py"):
                                    parts = p[:-3].removesuffix("/__init__").split("/")
                                    for i in range(len(parts)):
                                        self._suffix_map["/".join(parts[i:])].append(p)
                        targets += self._suffix_map.get(dotted.replace(".", "/"), [])[:3]
                m = JS_IMPORT_RX.search(line)
                if m:
                    rel = os.path.normpath(os.path.join(base_dir, m.group(1)))
                    targets += [rel + e for e in ("", ".js", ".jsx", ".ts", ".tsx", "/index.js", "/index.ts")]
                for tgt in targets:
                    tgt = tgt.replace("\\", "/")
                    if tgt in fileset:
                        self.import_boost[tgt] = 1.15

    # ---- confidence -------------------------------------------------------- #
    def assess(self, results: list[Scored]) -> dict:
        qterms = self.qterms
        unmatched = [t.raw for t in qterms if t.kind == "word" and t.df == 0 and not t.path_fids]
        total_w = sum(t.weight for t in qterms) or 1.0
        unmatched_frac = sum(t.weight for t in qterms if t.raw in unmatched) / total_w
        if not results:
            return {"score": 0.0, "label": "very_low", "unmatched_terms": unmatched,
                    "reasons": ["no matches found for any query term"],
                    "suggestion": "check spelling, try different keywords, or use exact identifier names"}
        c = [r.confidence for r in results[:3]]
        score = 0.65 * c[0] + 0.25 * (sum(c) / len(c)) + 0.10 * (100 if self.converged else 60)
        score = round(max(0.0, score * (1 - 0.5 * unmatched_frac)), 1)

        top = results[0]
        reasons = []
        if top.defsym and top.defsig >= 0.5:
            name, line, kind = top.defsym
            reasons.append(f"definition match: {name} ({kind}) at {top.path}:{line}")
        nwords = sum(1 for t in qterms if t.kind == "word")
        if nwords >= 2 and top.prox_cov >= 0.99:
            reasons.append(f"all query terms appear together within ~8 lines near {top.path}:{top.prox_line + 1}")
        elif top.cov < 0.6:
            reasons.append(f"best file covers only {int(top.cov * 100)}% of the query's weight")
        if top.pathsig >= 0.5:
            reasons.append("file path matches the query terms")
        if unmatched:
            reasons.append("not found anywhere in the codebase: " + ", ".join(unmatched))
        if top.spec < 0.45:
            reasons.append("query terms are very common in this codebase (low specificity)")
        if len(results) > 1 and results[1].final >= 0.9 * top.final:
            reasons.append("several files score almost equally; the answer may span multiple files")
        out = {"score": score, "label": label_of(score), "unmatched_terms": unmatched, "reasons": reasons}
        if score < 50:
            out["suggestion"] = ("add exact identifier names (function/class), quote a multi-word phrase, "
                                 "or narrow with --ext/--glob")
        return out

    def definitions(self, results: list[Scored], limit: int = 8) -> list[dict]:
        wmap = {t.key: t.weight for t in self.qterms}
        total_w = sum(wmap.values()) or 1.0
        rows = []
        for fid, syms in self.file_syms.items():
            if fid not in self.base:
                continue
            for (name, line, kind), keys in syms.items():
                frac = sum(wmap.get(k, 0.0) for k in keys) / total_w
                if frac >= (0.6 if len(wmap) > 1 else 0.5):
                    rows.append((frac, self.base[fid], fid, name, kind, line))
        rows.sort(reverse=True)
        return [{"name": n, "kind": k, "path": self.idx.paths[f], "line": ln, "match": round(min(1, fr), 2)}
                for fr, _, f, n, k, ln in rows[:limit]]

    # ---- main loop --------------------------------------------------------- #
    def run(self, query: str, top_k: int = 8) -> dict:
        t_all = time.perf_counter()
        t0 = time.perf_counter()
        stats = self.idx.sync()
        self.idx.load()
        self.timings["index_sync"] = time.perf_counter() - t0
        self.log(f"[fc] index: {stats['files']} files ({stats['changed']} re-indexed, "
                 f"{stats['removed']} removed) in {self.timings['index_sync'] * 1000:.0f} ms")

        self.build_terms(query)
        results: list[Scored] = []
        assessment: dict = {}
        prev_score = 0.0
        for turn in range(1, self.max_turns + 1):
            pending = [t for t in self.terms.values() if not t.looked_up]
            if not pending:
                break
            t0 = time.perf_counter()
            for t in pending:
                self.lookup(t)
            self.timings["lookup"] += time.perf_counter() - t0
            results = self.rank(top_k)
            self.turns_used = turn
            self.converged = True
            assessment = self.assess(results)
            self.log(f"[turn {turn}] +{len(pending)} terms: {[t.raw for t in pending][:10]} "
                     f"-> confidence {assessment['score']} ({assessment['label']})")

            if not self.thorough and assessment["score"] >= EARLY_EXIT_CONFIDENCE:
                self.log("          confident enough; stopping early")
                break
            if turn >= 2 and assessment["score"] - prev_score < 1.0:
                self.log("          no further improvement; stopping")
                break
            prev_score = assessment["score"]
            if turn < self.max_turns:
                added = self.expand(results, first=(turn == 1))
                self.log(f"          expanded with {added} terms")
                if added == 0:
                    break
            else:
                self.converged = False
                assessment = self.assess(results)

        for s in results:
            self.build_snippets(s)
        self.timings["total"] = time.perf_counter() - t_all
        return {
            "query": query, "turns": self.turns_used, "results": results,
            "confidence": assessment or self.assess([]),
            "definitions": self.definitions(results),
            "elapsed_ms": round(self.timings["total"] * 1000),
            "index": stats,
        }


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _numbered(sn: Snippet, width_cap: int = 200) -> str:
    hits, w = set(sn.hit_lines), len(str(sn.end))
    return "\n".join(f"{'>' if sn.start + i in hits else ' '}{sn.start + i:>{w}}: {line.rstrip()[:width_cap]}"
                     for i, line in enumerate(sn.text))


def _attr(v) -> str:
    return str(v).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def apply_budget(results: list[Scored], max_tokens: int) -> int:
    """Trim snippets to fit an approximate token budget (4 chars ~ 1 token). Returns dropped count."""
    if max_tokens <= 0:
        return 0
    budget, dropped = max_tokens * 4, 0
    used = 0
    for rank, s in enumerate(results):
        kept = []
        for i, sn in enumerate(s.snippets):
            cost = len(_numbered(sn)) + 80
            if used + cost <= budget or (rank == 0 and i == 0):
                kept.append(sn)
                used += cost
            else:
                dropped += 1
        s.snippets = kept
    for s in [r for r in results if not r.snippets]:
        results.remove(s)
    return dropped


def format_llm(out: dict, explain: bool = False) -> str:
    conf, res = out["confidence"], out["results"]
    x = [f'<fast_context query="{_attr(out["query"])}" files="{len(res)}" turns="{out["turns"]}" '
         f'confidence="{conf["score"]}" confidence_label="{conf["label"]}" elapsed_ms="{out["elapsed_ms"]}">']
    x.append(f'  <confidence score="{conf["score"]}" label="{conf["label"]}">')
    for r in conf["reasons"]:
        x.append(f"    <reason>{_attr(r)}</reason>")
    if conf.get("suggestion"):
        x.append(f"    <suggestion>{_attr(conf['suggestion'])}</suggestion>")
    x.append("  </confidence>")
    if out["definitions"]:
        x.append("  <definitions>")
        for d in out["definitions"]:
            x.append(f"    {d['name']} ({d['kind']})  {d['path']}:{d['line']}")
        x.append("  </definitions>")
    if res:
        x.append("  <index>")
        for s in res:
            for sn in s.snippets:
                sym = f"  # {sn.symbol}" if sn.symbol else ""
                x.append(f"    {s.path}:{sn.start}-{sn.end}{sym}  [confidence {s.confidence:.0f}]")
        x.append("  </index>")
    for rank, s in enumerate(res, 1):
        lang = language_of(s.path)
        extra = ""
        if explain:
            extra = (f' signals="coverage={s.cov:.2f} proximity={s.prox:.2f} definition={s.defsig:.2f} '
                     f'path={s.pathsig:.2f} strength={s.strength:.2f} specificity={s.spec:.2f}"')
        x.append(f'  <file path="{_attr(s.path)}" rank="{rank}" score="{s.final:.1f}" '
                 f'confidence="{s.confidence:.0f}" confidence_label="{label_of(s.confidence)}" '
                 f'language="{lang}"{extra}>')
        x.append(f"    <matched_terms>{_attr(', '.join(dict.fromkeys(s.present)))}</matched_terms>")
        for sn in s.snippets:
            attrs = f'lines="{sn.start}-{sn.end}" hit_lines="{",".join(map(str, sn.hit_lines))}"'
            if sn.symbol:
                attrs += f' symbol="{_attr(sn.symbol)}"'
            if sn.terms:
                attrs += f' terms="{_attr(", ".join(sn.terms))}"'
            x.append(f"    <snippet {attrs}>")
            x.append(f"```{lang}")
            x.append(_numbered(sn))
            x.append("```")
            x.append("    </snippet>")
        x.append("  </file>")
    x.append("</fast_context>")
    return "\n".join(x)


def format_json(out: dict, explain: bool = False) -> str:
    return json.dumps({
        "query": out["query"], "turns": out["turns"], "elapsed_ms": out["elapsed_ms"],
        "confidence": out["confidence"],
        "definitions": out["definitions"],
        "results": [{
            "rank": i, "path": s.path, "language": language_of(s.path), "score": round(s.final, 3),
            "confidence": {"score": s.confidence, "label": label_of(s.confidence), "signals": {
                "coverage": round(s.cov, 3), "proximity": round(s.prox, 3), "definition": round(s.defsig, 3),
                "path": round(s.pathsig, 3), "strength": round(s.strength, 3), "specificity": round(s.spec, 3)}},
            "matched_terms": list(dict.fromkeys(s.present)),
            "snippets": [{
                "location": f"{s.path}:{sn.start}-{sn.end}", "start_line": sn.start, "end_line": sn.end,
                "hit_lines": sn.hit_lines, "symbol": sn.symbol, "terms": sn.terms,
                "code": "\n".join(sn.text), "code_numbered": _numbered(sn)} for sn in s.snippets],
        } for i, s in enumerate(out["results"], 1)],
    }, indent=2)


def format_paths(out: dict, explain: bool = False) -> str:
    lines = [f"# confidence {out['confidence']['score']} ({out['confidence']['label']})"]
    for s in out["results"]:
        for sn in s.snippets:
            sym = f"  # {sn.symbol}" if sn.symbol else ""
            lines.append(f"{s.path}:{sn.start}-{sn.end}{sym}  [{s.confidence:.0f}]")
    return "\n".join(lines)


def format_text(out: dict, explain: bool = False) -> str:
    conf = out["confidence"]
    bar = "#" * int(conf["score"] / 10) + "." * (10 - int(conf["score"] / 10))
    x = [f"Query: {out['query']}",
         f"Confidence: {conf['score']:.0f}/100 [{bar}] {conf['label']}   "
         f"({out['turns']} turn(s), {out['elapsed_ms']} ms)"]
    x += [f"  - {r}" for r in conf["reasons"]]
    if conf.get("suggestion"):
        x.append(f"  tip: {conf['suggestion']}")
    if out["definitions"]:
        x.append("\nDefinitions:")
        x += [f"  {d['name']} ({d['kind']})  {d['path']}:{d['line']}" for d in out["definitions"]]
    if not out["results"]:
        x.append("\nNo relevant code found.")
    for i, s in enumerate(out["results"], 1):
        x.append(f"\n{i}. {s.path}   confidence {s.confidence:.0f} ({label_of(s.confidence)})")
        x.append(f"   matched: {', '.join(dict.fromkeys(s.present))}")
        if explain:
            x.append(f"   signals: cov={s.cov:.2f} prox={s.prox:.2f} def={s.defsig:.2f} "
                     f"path={s.pathsig:.2f} strength={s.strength:.2f} spec={s.spec:.2f}")
        for sn in s.snippets:
            sym = f"  [{sn.symbol}]" if sn.symbol else ""
            x.append(f"   --- {s.path}:{sn.start}-{sn.end}{sym}")
            x += ["   " + row for row in _numbered(sn, 160).split("\n")]
    return "\n".join(x)


FORMATTERS = {"llm": format_llm, "json": format_json, "text": format_text, "paths": format_paths}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="LLM-free Fast Context with confidence scoring.")
    ap.add_argument("query", nargs="?", help='e.g. "lazy load"  (quote phrases, use identifiers freely)')
    ap.add_argument("--root", default=".", help="codebase root (default: current directory)")
    ap.add_argument("--top", type=int, default=6, help="max files to return (default 6)")
    ap.add_argument("--context", type=int, default=3, help="context lines around each hit")
    ap.add_argument("--per-file", type=int, default=3, help="max snippets per file")
    ap.add_argument("--turns", type=int, default=MAX_TURNS, help=f"max turns (<= {MAX_TURNS})")
    ap.add_argument("--format", choices=FORMATTERS, default="llm",
                    help="llm (default, XML-tagged), json, text, or paths (path:lines list)")
    ap.add_argument("--json", action="store_true", help="shortcut for --format json")
    ap.add_argument("--max-tokens", type=int, default=6000,
                    help="approx token budget for snippets (default 6000, 0 = unlimited)")
    ap.add_argument("--ext", help="only these extensions, e.g. py,ts")
    ap.add_argument("--glob", help='only paths matching glob(s), e.g. "src/*.py,lib/*"')
    ap.add_argument("--tests", action="store_true", help="don't down-rank test files")
    ap.add_argument("--thorough", action="store_true", help="always run all turns (no early exit)")
    ap.add_argument("--no-cache", action="store_true", help="don't use the persistent index")
    ap.add_argument("--reindex", action="store_true", help="rebuild the index from scratch")
    ap.add_argument("--stats", action="store_true", help="print index statistics and exit")
    ap.add_argument("--explain", action="store_true", help="show the confidence signals per file")
    ap.add_argument("-v", "--verbose", action="store_true", help="log turns and timings to stderr")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        ap.error(f"--root {args.root!r} is not a directory")

    fc = Retriever(root, use_cache=not args.no_cache, reindex=args.reindex, max_turns=args.turns,
                   context=args.context, per_file=args.per_file, include_tests=args.tests,
                   exts=args.ext.split(",") if args.ext else None,
                   globs=args.glob.split(",") if args.glob else None,
                   thorough=args.thorough, verbose=args.verbose)
    if args.stats:
        fc.idx.sync()
        print(json.dumps(fc.idx.stats(), indent=2))
        return
    if not args.query:
        ap.error("a query is required, e.g.  python -m shelldeck.addons.fast_context \"lazy load\"")

    out = fc.run(args.query, top_k=args.top)
    dropped = apply_budget(out["results"], args.max_tokens)
    if args.verbose:
        tm = fc.timings
        print(f"[fc] timings ms: index_sync={tm['index_sync'] * 1000:.0f} lookup={tm['lookup'] * 1000:.0f} "
              f"rank={tm['rank_index'] * 1000:.0f} read+scan={tm['read_scan'] * 1000:.0f} "
              f"total={tm['total'] * 1000:.0f}; snippets dropped by budget: {dropped}", file=sys.stderr)
    print(FORMATTERS["json" if args.json else args.format](out, args.explain))


if __name__ == "__main__":
    main()