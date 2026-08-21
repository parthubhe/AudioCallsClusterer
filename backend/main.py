import re
import io
import uuid
import json
import asyncio
import hashlib
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import pandas as pd

from pipeline import run_pipeline, run_pipeline_from_cache, list_cache_files, CACHE_DIR, download_all
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="Audio Clusterer")

SAVED_BATCHES_DIR = Path("saved_batches")
EXTRACTED_CLUSTERS_DIR = Path("extracted_clusters")
SAVED_BATCHES_DIR.mkdir(exist_ok=True)
EXTRACTED_CLUSTERS_DIR.mkdir(exist_ok=True)
# Allow CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- In-memory job storage -----
JOBS_DIR = Path("./jobs")
JOBS_DIR.mkdir(exist_ok=True)

# { job_id: { "clusters": [...], "errors": [...], "labels": { cluster_idx: "label" }, "total_urls": int } }
jobs: Dict[str, dict] = {}

def save_job(job_id: str):
    """Persist job data to disk."""
    if job_id in jobs:
        with open(JOBS_DIR / f"{job_id}.json", "w") as f:
            json.dump(jobs[job_id], f)

def load_job(job_id: str) -> dict:
    """Load job from disk if not in memory."""
    if job_id in jobs:
        return jobs[job_id]
    
    path = JOBS_DIR / f"{job_id}.json"
    if path.exists():
        with open(path) as f:
            jobs[job_id] = json.load(f)
        return jobs[job_id]
    
    saved_path = SAVED_BATCHES_DIR / f"{job_id}.json"
    if saved_path.exists():
        with open(saved_path) as f:
            jobs[job_id] = json.load(f)
        return jobs[job_id]
        
    return None

# ----- Request/Response models -----
class ClusterRequest(BaseModel):
    text: str
    enable_caller_tune: bool = False

class AudioClustersResponse(BaseModel):
    clusters: List[List[str]]
    errors: List[str]

class SavedBatch(BaseModel):
    name: str
    clusters: List[List[str]]
    labels: Dict[str, str]
    total_files: Optional[int] = None

class ExtractClusterRequest(BaseModel):
    batch_name: str
    selected_clusters: List[Dict[str, Any]]

class ImportBatchRequest(BaseModel):
    name: str
    clusters: List[List[str]]
    labels: Dict[str, str] = {}
    skip_download: bool = False

class ClusterResponse(BaseModel):
    job_id: str
    clusters: List[List[str]]
    errors: List[dict]
    total_urls: int

class LabelRequest(BaseModel):
    labels: Dict[str, str]  # { "0": "Not Reachable - Hindi", "1": "Busy - English" }

def parse_urls(text: str) -> list[str]:
    """Extract and dedupe .wav URLs from pasted text."""
    urls = re.findall(r'https?://[^\s,;"\']+\.wav', text)
    return list(set(urls))

# ----- Health -----

@app.get("/api/health")
async def health():
    return {"status": "ok"}

# ----- Endpoints -----

@app.post("/api/cluster", response_model=ClusterResponse)
async def cluster_audio(request: ClusterRequest):
    # Phase 1: Parse & dedupe URLs
    urls = parse_urls(request.text)
    
    if not urls:
        raise HTTPException(status_code=400, detail="No valid .wav URLs found in input.")
        
    # Phase 2-5: Pipeline (now returns clusters + errors)
    clusters, errors, caller_tune_idx = await run_pipeline(urls, enable_yamnet=request.enable_caller_tune)
    
    ct_cluster = None
    if caller_tune_idx is not None:
        ct_cluster = clusters[caller_tune_idx]
        
    # Sort clusters by size descending
    clusters.sort(key=len, reverse=True)
    
    # Store job
    job_id = str(uuid.uuid4())[:8]
    
    labels = {}
    if ct_cluster is not None:
        try:
            new_ct_idx = clusters.index(ct_cluster)
            labels[str(new_ct_idx)] = "Caller Tunes"
        except ValueError:
            pass
            
    jobs[job_id] = {
        "clusters": clusters,
        "errors": errors,
        "labels": labels,
        "total_urls": len(urls)
    }
    save_job(job_id)
    
    return {"job_id": job_id, "clusters": clusters, "errors": errors, "total_urls": len(urls)}


@app.post("/api/cluster-stream")
async def cluster_audio_stream(request: ClusterRequest):
    """SSE endpoint that streams progress events during clustering."""
    urls = parse_urls(request.text)
    
    if not urls:
        raise HTTPException(status_code=400, detail="No valid .wav URLs found in input.")
    
    queue = asyncio.Queue()
    
    async def progress_callback(phase: str, current: int, total: int, message: str):
        await queue.put({
            "event": "progress",
            "data": json.dumps({
                "phase": phase,
                "current": current,
                "total": total,
                "message": message
            })
        })
    
    async def event_generator():
        # Start the pipeline in a background task
        task = asyncio.create_task(run_pipeline(urls, progress_callback=progress_callback, enable_yamnet=request.enable_caller_tune))
        
        # Keep yielding progress events until pipeline completes
        while not task.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
                yield event
            except asyncio.TimeoutError:
                # Send heartbeat to keep connection alive
                yield {"event": "heartbeat", "data": "ping"}
        
        # Drain remaining events from the queue
        while not queue.empty():
            event = await queue.get()
            yield event
        
        # Get result
        clusters, errors, caller_tune_idx = task.result()
        
        ct_cluster = None
        if caller_tune_idx is not None:
            ct_cluster = clusters[caller_tune_idx]
            
        clusters.sort(key=len, reverse=True)
        
        job_id = str(uuid.uuid4())[:8]
        
        labels = {}
        if ct_cluster is not None:
            try:
                new_ct_idx = clusters.index(ct_cluster)
                labels[str(new_ct_idx)] = "Caller Tunes"
            except ValueError:
                pass
        
        jobs[job_id] = {
            "clusters": clusters,
            "errors": errors,
            "labels": labels,
            "total_urls": len(urls)
        }
        save_job(job_id)
        
        # Send final result
        yield {
            "event": "result",
            "data": json.dumps({
                "job_id": job_id,
                "clusters": clusters,
                "errors": errors,
                "labels": labels,
                "total_urls": len(urls)
            })
        }
    
    return EventSourceResponse(event_generator())


def _parse_file(contents: bytes, filename: str) -> pd.DataFrame:
    if filename.lower().endswith('.csv'):
        return pd.read_csv(io.BytesIO(contents))
    return pd.read_excel(io.BytesIO(contents))

@app.post("/api/upload-excel-stream")
async def upload_excel_stream(
    file: UploadFile = File(...),
    enable_caller_tune: str = "false"
):
    """Upload an Excel/CSV file and stream progress via SSE."""
    is_caller_tune_enabled = enable_caller_tune.lower() == "true"
    try:
        contents = await file.read()
        df = await asyncio.to_thread(_parse_file, contents, file.filename)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")
    
    # Try to find the column with URLs
    url_column = None
    for col in df.columns:
        col_str = str(col).lower()
        if 'url' in col_str or 'recording' in col_str or 'link' in col_str:
            url_column = col
            break
    
    if url_column is None:
        # Fallback: scan all columns for any URL-like values
        all_text = '\n'.join(df.astype(str).values.flatten())
        urls = re.findall(r'https?://[^\s,;"\']+\.wav', all_text)
    else:
        raw_urls = df[url_column].dropna().astype(str).tolist()
        urls = [u.strip() for u in raw_urls if u.strip().startswith('http')]
    
    urls = list(set(urls))
    
    if not urls:
        raise HTTPException(status_code=400, detail="No valid URLs found in the Excel file.")
    
    queue = asyncio.Queue()
    
    async def progress_callback(phase, current, total, message):
        await queue.put({
            "event": "progress",
            "data": json.dumps({
                "phase": phase, "current": current, "total": total, "message": message
            })
        })
    
    async def event_generator():
        task = asyncio.create_task(run_pipeline(urls, progress_callback=progress_callback, enable_yamnet=is_caller_tune_enabled))
        
        while not task.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
                yield event
            except asyncio.TimeoutError:
                yield {"event": "heartbeat", "data": "ping"}
        
        while not queue.empty():
            event = await queue.get()
            yield event
        
        try:
            clusters, errors, caller_tune_idx = task.result()
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield {
                "event": "error",
                "data": json.dumps({"detail": f"Backend Error: {str(e)}"})
            }
            return
            
        ct_cluster = None
        if caller_tune_idx is not None:
            ct_cluster = clusters[caller_tune_idx]
            
        clusters.sort(key=len, reverse=True)
        
        job_id = str(uuid.uuid4())[:8]
        
        labels = {}
        if ct_cluster is not None:
            try:
                new_ct_idx = clusters.index(ct_cluster)
                labels[str(new_ct_idx)] = "Caller Tunes"
            except ValueError:
                pass
        
        jobs[job_id] = {
            "clusters": clusters, "errors": errors,
            "labels": labels, "total_urls": len(urls)
        }
        save_job(job_id)
        
        yield {
            "event": "result",
            "data": json.dumps({
                "job_id": job_id, "clusters": clusters,
                "errors": errors, "total_urls": len(urls)
            })
        }
    
    return EventSourceResponse(event_generator())


@app.post("/api/upload-excel", response_model=ClusterResponse)
async def upload_excel(
    file: UploadFile = File(...),
    enable_caller_tune: str = "false"
):
    """Upload an Excel/CSV file. Parses all URL-like values from the RecordingURL column."""
    is_caller_tune_enabled = enable_caller_tune.lower() == "true"
    try:
        contents = await file.read()
        df = await asyncio.to_thread(_parse_file, contents, file.filename)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")
    
    # Try to find the column with URLs
    url_column = None
    for col in df.columns:
        col_str = str(col).lower()
        if 'url' in col_str or 'recording' in col_str or 'link' in col_str:
            url_column = col
            break
    
    if url_column is None:
        # Fallback: scan all columns for any URL-like values
        all_text = '\n'.join(df.astype(str).values.flatten())
        urls = re.findall(r'https?://[^\s,;"\']+\.wav', all_text)
    else:
        raw_urls = df[url_column].dropna().astype(str).tolist()
        urls = [u.strip() for u in raw_urls if u.strip().startswith('http')]
    
    urls = list(set(urls))
    
    if not urls:
        raise HTTPException(status_code=400, detail="No valid URLs found in the Excel file.")
    
    clusters, errors, caller_tune_idx = await run_pipeline(urls, enable_yamnet=is_caller_tune_enabled)
    
    ct_cluster = None
    if caller_tune_idx is not None:
        ct_cluster = clusters[caller_tune_idx]
        
    clusters.sort(key=len, reverse=True)
    
    job_id = str(uuid.uuid4())[:8]
    
    labels = {}
    if ct_cluster is not None:
        try:
            new_ct_idx = clusters.index(ct_cluster)
            labels[str(new_ct_idx)] = "Caller Tunes"
        except ValueError:
            pass
            
    jobs[job_id] = {
        "clusters": clusters, "errors": errors,
        "labels": labels, "total_urls": len(urls)
    }
    save_job(job_id)
    
    return {"job_id": job_id, "clusters": clusters, "errors": errors, "total_urls": len(urls)}


# ----- Cache / Batch -----

@app.get("/api/cache-files")
async def get_cache_files():
    try:
        files = await asyncio.to_thread(list_cache_files)
        return {"total_cached_files": len(files)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/saved-batches")
async def save_batch(batch: SavedBatch):
    import datetime
    import re
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", batch.name)
    file_path = SAVED_BATCHES_DIR / f"{safe_name}.json"
    data = {
        "batch_name": safe_name,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "total_files": batch.total_files or sum(len(c) for c in batch.clusters),
        "clusters": batch.clusters,
        "labels": batch.labels
    }
    await asyncio.to_thread(lambda: file_path.write_text(json.dumps(data)))
    return {"message": "Batch saved successfully"}

@app.get("/api/saved-batches")
async def list_saved_batches():
    def get_files():
        files = list(SAVED_BATCHES_DIR.glob("*.json"))
        return sorted([f.stem for f in files])
    
    batches = await asyncio.to_thread(get_files)
    return {"batches": batches}

@app.get("/api/saved-batches/{name}")
async def get_saved_batch(name: str):
    file_path = SAVED_BATCHES_DIR / f"{name}.json"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Batch not found")
    content = await asyncio.to_thread(file_path.read_text)
    return json.loads(content)

@app.post("/api/extract-clusters")
async def extract_clusters(req: ExtractClusterRequest):
    import shutil
    import urllib.parse
    
    def do_extraction():
        extracted_count = 0
        for cluster in req.selected_clusters:
            label = cluster.get("label", "unlabeled").replace("/", "_").replace("\\", "_")
            if not label.strip():
                label = "unlabeled"
            urls = cluster.get("urls", [])
            
            cluster_dir = EXTRACTED_CLUSTERS_DIR / req.batch_name / label
            cluster_dir.mkdir(parents=True, exist_ok=True)
            
            for url in urls:
                url_hash = hashlib.md5(url.encode()).hexdigest()
                cached_file = CACHE_DIR / f"{url_hash}.wav"
                
                if cached_file.exists():
                    # Extract UUID from URL
                    parsed = urllib.parse.urlparse(url)
                    filename = Path(parsed.path).name
                    if not filename:
                        filename = f"{url_hash}.wav"
                    
                    dest_file = cluster_dir / filename
                    shutil.copy2(cached_file, dest_file)
                    extracted_count += 1
        return extracted_count
        
    count = await asyncio.to_thread(do_extraction)
    return {"message": f"Successfully extracted {count} files"}

class BatchRequest(BaseModel):
    batch_size: int = 5000
    offset: int = 0
    enable_caller_tune: bool = False


@app.post("/api/cluster-batch-stream")
async def cluster_batch_stream(request: BatchRequest):
    """Process a batch of cached files and stream progress via SSE."""
    import asyncio
    
    # Get all cache files in a background thread
    all_files = await asyncio.to_thread(list_cache_files)
    total_files = len(all_files)
    
    if total_files == 0:
        raise HTTPException(status_code=400, detail="No cached files found. Upload and process an Excel file first.")
    
    # Slice for this batch
    batch = all_files[request.offset : request.offset + request.batch_size]
    
    if not batch:
        raise HTTPException(status_code=400, detail=f"No files in range offset={request.offset}, batch_size={request.batch_size}. Total cached: {total_files}")
    
    file_paths = [f["path"] for f in batch]
    
    queue = asyncio.Queue()
    
    async def progress_callback(phase, current, total, message):
        await queue.put({
            "event": "progress",
            "data": json.dumps({
                "phase": phase, "current": current, "total": total, "message": message
            })
        })
    
    async def event_generator():
        task = asyncio.create_task(
            run_pipeline_from_cache(file_paths, progress_callback=progress_callback, enable_yamnet=request.enable_caller_tune)
        )
        
        while not task.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
                yield event
            except asyncio.TimeoutError:
                yield {"event": "heartbeat", "data": "ping"}
        
        # Drain remaining events
        while not queue.empty():
            event = await queue.get()
            yield event
        
        clusters, errors, caller_tune_idx = task.result()
        
        # The clusters here contain file paths, not URLs.
        # We'll return paths so the frontend can reference them.
        ct_cluster = None
        if caller_tune_idx is not None:
            ct_cluster = clusters[caller_tune_idx]
            
        clusters.sort(key=len, reverse=True)
        
        job_id = str(uuid.uuid4())[:8]
        
        labels = {}
        if ct_cluster is not None:
            try:
                new_ct_idx = clusters.index(ct_cluster)
                labels[str(new_ct_idx)] = "Caller Tunes"
            except ValueError:
                pass
        
        jobs[job_id] = {
            "clusters": clusters, "errors": errors,
            "labels": labels, "total_urls": len(file_paths)
        }
        save_job(job_id)
        
        yield {
            "event": "result",
            "data": json.dumps({
                "job_id": job_id,
                "clusters": clusters,
                "errors": errors,
                "labels": labels,
                "total_files": total_files,
                "batch_offset": request.offset,
                "batch_size": len(batch),
                "total_urls": len(file_paths)
            })
        }
    
    return EventSourceResponse(event_generator())


@app.post("/api/import-batch-stream")
async def import_batch_stream(request: ImportBatchRequest):
    """Import a pre-clustered JSON file and download missing audio files to cache."""
    import asyncio
    import datetime

    # 1. Extract all unique URLs from the clusters
    unique_urls = set()
    for cluster in request.clusters:
        for url in cluster:
            unique_urls.add(url)
    
    urls_list = list(unique_urls)
    queue = asyncio.Queue()

    async def progress_callback(phase, current, total, message):
        await queue.put({
            "event": "progress",
            "data": json.dumps({
                "phase": phase, "current": current, "total": total, "message": message
            })
        })

    async def event_generator():
        if request.skip_download:
            results, errors = [], []
            await queue.put({
                "event": "progress",
                "data": json.dumps({
                    "phase": "skipping", "current": len(urls_list), "total": len(urls_list),
                    "message": "Skipping audio download as requested."
                })
            })
            await asyncio.sleep(0.5)  # small delay for UI to register
        else:
            # Yield an initial progress message
            await queue.put({
                "event": "progress",
                "data": json.dumps({
                    "phase": "starting", "current": 0, "total": len(urls_list),
                    "message": f"Scanning cache for {len(urls_list)} files..."
                })
            })

            # Start download task
            task = asyncio.create_task(
                download_all(urls_list, concurrency=20, progress_callback=progress_callback)
            )

            while not task.done():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.5)
                    yield event
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "ping"}
            
            # Drain remaining events
            while not queue.empty():
                event = await queue.get()
                yield event

            results, errors = task.result()

        # 2. Save the batch to saved_batches/
        import re
        safe_name = re.sub(r'[\\/*?:"<>|]', "_", request.name)
        file_path = SAVED_BATCHES_DIR / f"{safe_name}.json"
        data = {
            "batch_name": safe_name,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "total_files": sum(len(c) for c in request.clusters),
            "clusters": request.clusters,
            "labels": request.labels
        }
        
        await asyncio.to_thread(lambda: file_path.write_text(json.dumps(data)))

        yield {
            "event": "result",
            "data": json.dumps({
                "message": f"Successfully imported {safe_name} and synced cache.",
                "batch_name": safe_name,
                "errors": errors
            })
        }

    return EventSourceResponse(event_generator())


# ----- Labels -----

@app.post("/api/labels/{job_id}")
async def save_labels(job_id: str, request: LabelRequest):
    """Save cluster labels for a job."""
    job = load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    job["labels"] = request.labels
    save_job(job_id)
    return {"status": "ok", "labels": job["labels"]}

@app.get("/api/labels/{job_id}")
async def get_labels(job_id: str):
    """Get cluster labels for a job."""
    job = load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"labels": job.get("labels", {})}


# ----- Export -----

@app.get("/api/export/{job_id}")
async def export_csv(job_id: str):
    """Export clustering results as a CSV file."""
    job = load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    
    rows = []
    labels = job.get("labels", {})
    for idx, cluster in enumerate(job["clusters"]):
        label = labels.get(str(idx), "")
        for url in cluster:
            rows.append({
                "url": url,
                "cluster_id": idx + 1,
                "cluster_size": len(cluster),
                "label": label
            })
    
    df = pd.DataFrame(rows)
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)
    
    return StreamingResponse(
        io.BytesIO(buffer.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=clusters_{job_id}.csv"}
    )

import shutil

@app.post("/api/export-callertunes/{job_id}")
async def export_callertunes(job_id: str):
    """Export the caller tunes cluster to a local folder in the backend."""
    job = load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    
    # Find the cluster with the label "Caller Tunes" (case insensitive)
    labels = job.get("labels", {})
    caller_tune_cluster_idx = None
    for idx, label in labels.items():
        if label.strip().lower() == "caller tunes":
            caller_tune_cluster_idx = int(idx)
            break
    
    if caller_tune_cluster_idx is None:
        raise HTTPException(status_code=404, detail="No cluster labeled 'Caller Tunes' found.")
        
    if caller_tune_cluster_idx >= len(job["clusters"]):
        raise HTTPException(status_code=500, detail="Invalid cluster index.")
        
    cluster_urls = job["clusters"][caller_tune_cluster_idx]
    
    export_dir = Path("./exported_callertunes")
    export_dir.mkdir(exist_ok=True)
    
    # Pre-download any missing files in case this was a skip-download import
    await download_all(cluster_urls, concurrency=15)
    
    copied_count = 0
    errors = []
    
    for url in cluster_urls:
        url_hash = hashlib.md5(url.encode()).hexdigest()
        cached_path = CACHE_DIR / f"{url_hash}.wav"
        
        if cached_path.exists():
            dest_filename = url.split("/")[-1]
            dest_path = export_dir / dest_filename
            try:
                shutil.copy2(cached_path, dest_path)
                copied_count += 1
            except Exception as e:
                errors.append(f"Failed to copy {dest_filename}: {e}")
        else:
            errors.append(f"File not in cache for {url}")
            
    return {
        "status": "ok",
        "message": f"Exported {copied_count} caller tunes to {export_dir.absolute()}",
        "copied": copied_count,
        "errors": errors
    }


# ----- Audio Proxy (for download support) -----

@app.get("/api/audio-proxy")
async def audio_proxy(url: str):
    """Proxy an audio file for playback/download, serving from cache if available."""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    cached = CACHE_DIR / f"{url_hash}.wav"
    
    if not cached.exists() or cached.stat().st_size == 0:
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
                    cached.write_bytes(data)
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Failed to download audio file on demand: {e}")
    
    filename = url.split("/")[-1]
    return FileResponse(
        str(cached),
        media_type="audio/wav",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
