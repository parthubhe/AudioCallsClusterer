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
from typing import List, Optional, Dict
import pandas as pd

from pipeline import run_pipeline, CACHE_DIR
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="Audio Clusterer")

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
    return None

# ----- Request/Response models -----
class ClusterRequest(BaseModel):
    text: str
    enable_caller_tune: bool = False

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


@app.post("/api/upload-excel-stream")
async def upload_excel_stream(
    file: UploadFile = File(...),
    enable_caller_tune: str = "false"
):
    """Upload an Excel file and stream progress via SSE."""
    is_caller_tune_enabled = enable_caller_tune.lower() == "true"
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read Excel file: {e}")
    
    # Try to find the column with URLs
    url_column = None
    for col in df.columns:
        if 'url' in col.lower() or 'recording' in col.lower() or 'link' in col.lower():
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
    """Upload an Excel file. Parses all URL-like values from the RecordingURL column."""
    is_caller_tune_enabled = enable_caller_tune.lower() == "true"
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read Excel file: {e}")
    
    # Try to find the column with URLs
    url_column = None
    for col in df.columns:
        if 'url' in col.lower() or 'recording' in col.lower() or 'link' in col.lower():
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
    
    if cached.exists():
        filename = url.split("/")[-1]
        return FileResponse(
            str(cached),
            media_type="audio/wav",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    
    # If not cached, return a redirect or error
    raise HTTPException(status_code=404, detail="Audio file not found in cache. Run clustering first.")
