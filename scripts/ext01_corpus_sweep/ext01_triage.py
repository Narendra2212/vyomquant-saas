"""EXT-01: triage the 113-file staging/PHASE11B_* corpus to find any buried technical finding.

THE RESIDUAL RISK THIS ADDRESSES
--------------------------------
EXT-01's remaining_action says the ~95-file "A-cluster" was "sampled, not exhaustively
read", and that "the synthesis explicitly flags a nonzero chance a distinct finding is
buried in an unread file there, judged a low-probability, low-expected-value risk given
the uniformity of every sampled file".

The corpus is 43,566 lines / 2.6 MB. Reading it linearly is not a good use of anyone's
attention, and "I read 95 files of governance prose" is a claim nobody can check. So
instead: measure the uniformity the prior judgement RESTED on, and surface the files that
break it. A buried technical finding is, by definition, an OUTLIER against a uniform
governance saga -- so the files worth reading in full are exactly the ones this ranks last
for similarity and first for technical signal.

WHAT IT MEASURES PER FILE
  * technical signal -- references to real source paths that exist in this repo, defect
    vocabulary, and finding-id patterns;
  * similarity to the corpus centroid -- shared vocabulary, so the "one continuous
    internally-redundant saga" claim is tested rather than taken on trust.

Everything is reported; nothing is filtered away silently.
"""
import collections
import pathlib
import re

# Repo-relative: this file lives at scripts/ext01_corpus_sweep/, so the repository root is
# two parents up. Committed deliberately -- EXT-01's closure rests on these measurements,
# and a claim nobody can re-run is not evidence.
REPO = pathlib.Path(__file__).resolve().parent.parent.parent
STAGING = REPO / "staging"

FILES = sorted(STAGING.glob("PHASE11B_*.md"))

#: Words that suggest a concrete technical defect rather than process/sign-off prose.
DEFECT_WORDS = [
    "vulnerab", "exploit", "injection", "rce", "privilege escalation", "data loss",
    "corrupt", "race condition", "deadlock", "leak", "unsafe", "silently", "double",
    "duplicate", "lost", "incorrect", "wrong", "mismatch", "stale", "fabricat",
    "not enforced", "bypass", "unauthenticated", "unauthorized", "plaintext",
    "hardcoded", "secret", "credential", "money", "balance", "p0", "critical",
]

#: A real source path reference is the strongest signal: governance prose about a sign-off
#: process does not usually cite implementation files.
PATH_RE = re.compile(
    r"\b(?:backend_app|algo22-terminal/src|scripts|tests|migrations|mds|api_ws)"
    r"/[A-Za-z0-9_./-]+\.(?:py|jsx|js|sql|yml|yaml|json)\b"
)
FINDING_ID_RE = re.compile(r"\b(?:[A-Z]{2,8}-\d{1,3}|W-\d{1,3}|U\d{1,3}|BUG-?\d{2,3})\b")

STOP = set("""the a an and or of to in is are was were be been for on with that this it as
by at from not no any all each per if then else when which what who whom whose how why
all can could should would may might must shall will do does did done has have had having
but so than such very more most less least only also into over under again further once
here there both few other same own too s t just now
""".split())


def tokens(text):
    return [w for w in re.findall(r"[a-z][a-z0-9_]{2,}", text.lower()) if w not in STOP]


print("EXT-01 corpus triage")
print("=" * 104)
print(f"files: {len(FILES)}")
total_lines = 0
records = []
vocab_counter = collections.Counter()

for path in FILES:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.count("\n") + 1
    total_lines += lines
    low = text.lower()

    paths_cited = sorted(set(PATH_RE.findall(text)))
    # Only count a cited path as signal if the file actually EXISTS in the repo: a real
    # defect report points at real code, invented or renamed paths are noise.
    real_paths = [p for p in paths_cited if (REPO / p).exists()]
    defect_hits = {w: low.count(w) for w in DEFECT_WORDS if w in low}
    ids = collections.Counter(FINDING_ID_RE.findall(text))

    toks = tokens(text)
    vocab = set(toks)
    vocab_counter.update(vocab)

    records.append({
        "name": path.name,
        "lines": lines,
        "bytes": len(text),
        "real_paths": real_paths,
        "n_real_paths": len(real_paths),
        "defect_hits": defect_hits,
        "defect_score": sum(defect_hits.values()),
        "ids": ids,
        "vocab": vocab,
    })

print(f"total lines: {total_lines}")

# Corpus centroid: vocabulary present in a majority of files = the shared saga vocabulary.
n = len(records)
centroid = {w for w, c in vocab_counter.items() if c >= n * 0.5}
print(f"shared vocabulary (in >=50% of files): {len(centroid)} terms")

for r in records:
    overlap = len(r["vocab"] & centroid) / max(1, len(centroid))
    r["similarity"] = overlap

sims = sorted(r["similarity"] for r in records)
print(f"similarity to centroid: min={sims[0]:.2f} median={sims[n // 2]:.2f} max={sims[-1]:.2f}")

print()
print("=" * 104)
print("UNIFORMITY TEST -- is this really 'one continuous internally-redundant saga'?")
print("=" * 104)
high = sum(1 for r in records if r["similarity"] >= 0.60)
print(f"  files sharing >=60% of the corpus vocabulary: {high}/{n}")
print(f"  files citing NO real source path:             "
      f"{sum(1 for r in records if r['n_real_paths'] == 0)}/{n}")

print()
print("=" * 104)
print("CANDIDATES TO READ IN FULL -- ranked by technical signal, then by dissimilarity")
print("=" * 104)
ranked = sorted(
    records,
    key=lambda r: (-r["n_real_paths"], -r["defect_score"], r["similarity"]),
)
for r in ranked[:20]:
    print(f"\n{r['name']}  ({r['lines']} lines, sim={r['similarity']:.2f})")
    print(f"   real source paths cited: {r['n_real_paths']}")
    for p in r["real_paths"][:6]:
        print(f"      - {p}")
    if r["defect_hits"]:
        top = sorted(r["defect_hits"].items(), key=lambda kv: -kv[1])[:8]
        print(f"   defect vocabulary: {', '.join(f'{w}x{c}' for w, c in top)}")
    if r["ids"]:
        print(f"   finding ids: {', '.join(f'{k}x{v}' for k, v in r['ids'].most_common(8))}")

print()
print("=" * 104)
print("MOST DISSIMILAR FILES (an outlier is where a buried finding would hide)")
print("=" * 104)
for r in sorted(records, key=lambda r: r["similarity"])[:12]:
    print(f"  sim={r['similarity']:.2f}  paths={r['n_real_paths']:<3} "
          f"defect={r['defect_score']:<4} {r['name']} ({r['lines']} lines)")

print()
print("=" * 104)
print("ALL FINDING IDS MENTIONED ANYWHERE IN THE CORPUS")
print("=" * 104)
all_ids = collections.Counter()
for r in records:
    all_ids.update(r["ids"])
print(f"distinct ids: {len(all_ids)}")
print("  " + ", ".join(f"{k}({v})" for k, v in all_ids.most_common(60)))
