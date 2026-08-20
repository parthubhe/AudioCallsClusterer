# Audio Call Clustering Tool — Implementation Plan

## 0. Context: telecom call-termination announcements

This is a strong fit for duration + fingerprint clustering. These are operator-side pre-recorded templates ("not reachable," "busy," "on another call," etc.) that get replayed **verbatim** every time a call ends in that state — so within one (language, pattern) combination, every clip is a literal repeat of the same underlying audio file. Different languages or operator variants of the same pattern will naturally land in *separate* clusters (different recording → different duration/fingerprint) — that's expected and desired, since each cluster corresponds to one localized pattern you want to identify.

Since your main classifier already handles most of this via embeddings, this pipeline is best used as a **complementary discovery/QA layer**, not a replacement:
- Embeddings generalize well (semantic/acoustic similarity), but can conflate templates that sound similar, or fail to cleanly separate ones with subtle differences — especially across languages where prosody/spectral shape can be closer than the actual content.
- Exact-duration + fingerprint matching is the opposite: **very high precision, low recall.** If two clips match, they are almost certainly the exact same underlying template. It won't generalize to unseen variants, but that precision is exactly what's useful for finding the classifier's blind spots and building a clean reference set of known patterns.

See §6 below for how to use it that way — cross-checking against your classifier's predictions, mining disagreements, and building a growing reference library of known templates.

---

## 1. Architecture

**Pipeline:** Paste URLs → Parse & dedupe → Concurrent download → Extract duration (header-only) → Duration bucketing → Two-tier similarity clustering (exact hash, then fingerprint/waveform match) → Cluster review UI.

The key design decision: **never compare all N files against all N files.** Bucket by duration first (cheap, O(n)), then only run expensive similarity comparisons *within* each bucket. If you have 5,000 calls spread across 200 distinct durations, you've turned a ~12.5M-pair problem into ~200 much smaller problems.

---

## 2. Tech stack

### Fast path (recommended to start): Python + Streamlit
- Single Python app, paste box is just a `st.text_area` (Ctrl+V works natively).
- Can go from zero to a working internal tool in a day or two.
- Good enough for personal/team use; not meant to be a polished product.

### Scalable path: React frontend + FastAPI backend
- Use once the Streamlit version proves the clustering logic works and you need multi-user access, job queues, or a nicer review UI.
- Backend does the same pipeline; frontend just becomes a proper paste-box + cluster browser with a job status poll.

Either way, the **core pipeline logic (Phases 1–5 below) is identical** — only the UI shell changes. Build the pipeline as a standalone Python module/CLI first so it's reusable regardless of which UI you pick.

### Libraries
| Purpose | Library |
|---|---|
| Async downloads | `aiohttp` or `httpx` (async client) |
| WAV metadata (duration, sample rate) | Python's built-in `wave` module, or `soundfile` |
| Audio fingerprinting | `pyacoustid` (wraps Chromaprint/`fpcalc`) — purpose-built for near-duplicate audio detection, robust to minor encoding differences |
| Waveform correlation (optional, for byte-level "exact" checks) | `numpy` / `scipy.signal.correlate` |
| MFCC features (fallback/refinement) | `librosa` |
| Clustering | `scikit-learn` (`AgglomerativeClustering` with a distance threshold) or plain Union-Find for connected components |

---

## 3. Phase-by-phase implementation

### Phase 1 — Paste & parse
- Textarea/input accepts the pasted Excel column (newline or tab separated).
- Regex-extract valid URLs: `https?://\S+\.wav` (or whatever extensions you expect).
- Dedupe exact-URL repeats before doing anything else — Excel copy-paste often has accidental repeats.
- Show a count: "Found 214 unique links."

```python
import re

def parse_urls(pasted_text: str) -> list[str]:
    urls = re.findall(r'https?://\S+', pasted_text)
    # strip trailing punctuation/whitespace artifacts from paste
    urls = [u.rstrip(',;') for u in urls]
    return sorted(set(urls))
```

### Phase 2 — Concurrent download with caching
- Download to a local cache directory, **keyed by a hash of the URL** (or the UUID already in your filenames, e.g. `ebf845fe-5acf-4d91-9413-bcaacc540f27.wav`) — so re-running the tool on an overlapping batch doesn't re-download everything.
- Limit concurrency (e.g. semaphore of 15–20) to avoid hammering S3 / getting throttled.
- Your example URLs have no signed query params, suggesting the bucket/objects may be publicly readable — confirm this early (a plain `GET` should just work). If you get 403s, you'll need AWS credentials (`boto3` + `get_object`) instead of anonymous HTTP.

```python
import asyncio, aiohttp, hashlib
from pathlib import Path

CACHE_DIR = Path("./audio_cache")
CACHE_DIR.mkdir(exist_ok=True)

async def download_one(session, url, sem):
    fname = url.split("/")[-1]  # reuse the UUID.wav name
    dest = CACHE_DIR / fname
    if dest.exists():
        return dest
    async with sem:
        async with session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.read()
            dest.write_bytes(data)
    return dest

async def download_all(urls, concurrency=15):
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession() as session:
        tasks = [download_one(session, u, sem) for u in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)
```

### Phase 3 — Extract duration (cheap, header-only)
Since these are `.wav` files, you don't need to decode audio to get the duration — just read the header.

```python
import wave

def get_duration_seconds(path) -> float:
    with wave.open(str(path), 'rb') as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate)
```

This is essentially instant even for thousands of files.

### Phase 4 — Duration bucketing
Round durations to a tolerance window (e.g. nearest 0.25–0.5s, or use a sliding tolerance of ±200ms) since encoders/silence padding can cause trivial length differences even for the "same" recording.

```python
from collections import defaultdict

def bucket_by_duration(durations: dict, tolerance=0.5):
    buckets = defaultdict(list)
    for path, dur in durations.items():
        key = round(dur / tolerance) * tolerance
        buckets[key].append(path)
    return buckets
```

### Phase 5 — Two-tier similarity clustering (within each bucket)

**Tier 1 — exact/near-exact duplicate detection (fast, catches literal repeats):**
Hash the raw PCM samples (not the file bytes, in case of different headers/metadata) and group identical hashes. This catches cases where the *exact same audio file* was played/saved multiple times (e.g. a static IVR announcement).

```python
import hashlib, wave

def pcm_hash(path) -> str:
    with wave.open(str(path), 'rb') as wf:
        pcm = wf.readframes(wf.getnframes())
    return hashlib.sha1(pcm).hexdigest()
```

**Tier 2 — perceptual fingerprint clustering (catches near-duplicates: same script, minor volume/silence/compression differences):**
Use Chromaprint via `pyacoustid` — it's designed exactly for "is this substantially the same audio" comparisons and is far more robust than raw correlation.

```python
import acoustid  # pyacoustid

def fingerprint(path):
    duration, fp = acoustid.fingerprint_file(str(path))
    return fp  # compact fingerprint, compare via acoustid.compare or hamming distance on decoded ints
```

Within each duration bucket:
1. Compute fingerprints for all files not already grouped by Tier 1.
2. Build a similarity graph: edge between two files if fingerprint distance is below a threshold you tune empirically (start conservative, listen to a few clusters, adjust).
3. Get clusters via connected components (Union-Find) — cheap and avoids needing to pick a cluster count up front. Alternatively use `AgglomerativeClustering(distance_threshold=X, n_clusters=None)` from scikit-learn if you want a dendrogram to help pick the threshold visually.

```python
class UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}
    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb
```

---

## 4. Review UI (what the person actually interacts with)

- Paste box → "Process" button → progress bar (downloading → extracting → clustering).
- Results view: one row per cluster, sorted by cluster size descending (biggest recurring templates surface first — usually the highest-value ones to label).
- Each cluster row: count, inline audio player for 1–3 representative clips, a text field to assign a label, a "apply to all N in cluster" button.
- Outliers (singleton clusters) shown separately at the bottom — these are the ones you'll still have to listen to manually, but the number should now be small.
- Export: label back to a CSV/Excel with `url, cluster_id, label` columns so it maps straight back to your original sheet.

---

## 5. Practical tuning notes / edge cases

- **Silence padding**: many call recordings have leading/trailing silence of varying length. Consider trimming silence (`librosa.effects.trim`) before duration bucketing and fingerprinting, so two genuinely-identical calls with different amounts of dead air still land in the same bucket.
- **Different speakers, same script**: if audio isn't a literal replay of the same file (e.g. a live agent reading a script), duration/waveform matching won't cluster it — this pipeline only catches genuine repeats of the same underlying recording, which fits your case since these are fixed operator templates.
- **Many small buckets is expected, not a bug**: with N languages × M call-termination patterns, you'll naturally end up with up to N×M distinct duration values, each a literal template. Don't widen your tolerance to force buckets together — that's exactly the granularity you want, since each bucket is a candidate localized pattern.
- **Threshold tuning**: don't guess the fingerprint-distance threshold — run it on a labeled sample of ~50–100 calls you already know the groupings for, and pick the threshold that best reproduces that grouping.
- **Cost/time**: header-only duration reads and Tier-1 hashing are near-instant even at scale (thousands of files). Chromaprint fingerprinting is fast per file (~real-time or faster). The bottleneck will be the S3 downloads, not the analysis — so concurrency tuning in Phase 2 matters most for overall speed.

---

## 6. Using this as a QA / discovery layer for your embedding classifier

This is where the pipeline earns its keep beyond one-off manual review. Once you have fingerprint-based clusters:

**a) Cross-tabulate against the classifier's predictions.**
For every clip, you now have two labels: `cluster_id` (from this pipeline) and `predicted_pattern` (from your embedding classifier). Group by `cluster_id` and look at the distribution of `predicted_pattern` within each cluster:
- **One cluster, one dominant predicted label, high agreement** → classifier is doing fine here, nothing to do.
- **One cluster, predictions split across 2+ labels** → since everything in the cluster is (near-)identical audio, this is very likely a **classifier error**, not genuine ambiguity. These are your highest-confidence error cases to inspect first — far more efficient than random sampling misclassifications.
- **One cluster, all low-confidence or "unknown/other" predictions, cluster is reasonably large** → likely a **pattern the classifier hasn't learned at all** (new language variant, new operator template, etc.) — a discovery, not just an error.
- **Large number of singleton clusters after fingerprinting** → either genuine one-off/rare variants, or a sign your duration tolerance / fingerprint threshold is too tight; worth spot-checking a few before assuming they're all noise.

**b) Build a growing reference/exemplar library.**
Once a cluster is confirmed and labeled (pattern + language), store its canonical fingerprint(s) — not just the label. New incoming clips can then be checked against this reference set directly (nearest-fingerprint lookup) as a fast, deterministic pre-filter *before* they even hit the embedding classifier. Over time this reference set absorbs more and more of the traffic with near-zero false positives, and the embedding classifier is left to handle only genuinely novel or ambiguous audio — which is where it's strongest anyway.

**c) Close the loop.**
Feed newly-discovered, confirmed clusters back into the embedding classifier's training data. This pipeline becomes a recurring "find what's new or wrong" pass you can re-run periodically on fresh call batches, rather than a one-time clustering exercise.

---

## 7. Suggested build order

1. CLI script: paste-a-file-of-URLs → download → print durations. (Validates access to S3 links.)
2. Add duration bucketing, print bucket sizes. (Sanity check: do you see the expected clumping?)
3. Add Tier 1 hash matching within buckets, print cluster sizes. (See how much Tier 1 alone catches — could be most of your win.)
4. Add Tier 2 fingerprinting for anything not caught by Tier 1.
5. Wrap steps 1–4 in a Streamlit app with the paste box + progress + cluster review table.
6. (Optional) Rebuild as React + FastAPI if you need multi-user access later.
