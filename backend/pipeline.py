import asyncio
import aiohttp
import hashlib
import wave
from pathlib import Path
from collections import defaultdict
from clustering import cluster_audio_files

# Try to import yamnet_classifier for post-processing
try:
    from yamnet_classifier import is_caller_tune
    YAMNET_AVAILABLE = True
except ImportError:
    YAMNET_AVAILABLE = False

CACHE_DIR = Path("./audio_cache")
CACHE_DIR.mkdir(exist_ok=True)

async def download_one(session, url, sem):
    """Downloads one URL into the cache based on its UUID or Hash."""
    # Assuming UUID is in the URL, e.g. ebf845fe-5acf-4d91-9413-bcaacc540f27.wav
    # If not, hash the URL to generate a unique filename
    try:
        url_hash = hashlib.md5(url.encode()).hexdigest()
        fname = f"{url_hash}.wav"
        dest = CACHE_DIR / fname
        if dest.exists():
            return dest, url
            
        async with sem:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.read()
                dest.write_bytes(data)
        return dest, url
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return None, url

async def download_all(urls, concurrency=15, progress_callback=None):
    """Concurrent download of all URLs with progress reporting."""
    sem = asyncio.Semaphore(concurrency)
    total = len(urls)
    completed = 0
    errors = []

    async def download_with_progress(session, url):
        nonlocal completed
        result = await download_one(session, url, sem)
        completed += 1
        if progress_callback:
            await progress_callback("downloading", completed, total, f"Downloading files... ({completed}/{total})")
        if result[0] is None:
            errors.append({"url": url, "error": "Download failed"})
        return result

    async with aiohttp.ClientSession() as session:
        tasks = [download_with_progress(session, u) for u in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
    valid_results = []
    for r in results:
        if isinstance(r, Exception):
            continue
        p, u = r
        if p is not None:
            valid_results.append((p, u))
    
    return valid_results, errors

def get_duration_seconds(path) -> float:
    """Reads duration from WAV header."""
    try:
        with wave.open(str(path), 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate)
    except Exception as e:
        print(f"Error reading duration for {path}: {e}")
        return -1.0

def bucket_by_duration(durations: dict, tolerance=0.5):
    """Bucket audio paths by their duration."""
    buckets = defaultdict(list)
    for path, dur in durations.items():
        if dur < 0:
            continue
        # Round to the nearest tolerance
        key = round(dur / tolerance) * tolerance
        buckets[key].append(path)
    return buckets

async def run_pipeline(urls: list[str], progress_callback=None, enable_yamnet=False):
    """
    Orchestrates the download, bucketing, and clustering.
    Returns: A tuple of (clusters, errors) where clusters is a list of URL lists.
    
    progress_callback: async function(phase, current, total, message) for progress reporting.
    """
    total_urls = len(urls)
    print(f"Processing {total_urls} urls...")
    
    if progress_callback:
        await progress_callback("starting", 0, total_urls, f"Starting pipeline with {total_urls} URLs...")
    
    # 1. Download
    if progress_callback:
        await progress_callback("downloading", 0, total_urls, "Starting downloads...")
    downloaded, download_errors = await download_all(urls, progress_callback=progress_callback)
    
    if progress_callback:
        await progress_callback("extracting", 0, len(downloaded), f"Extracting duration from {len(downloaded)} files...")
    
    # 2. Extract Duration
    path_to_url = {str(p): u for p, u in downloaded}
    durations = {}
    for i, (p, u) in enumerate(downloaded):
        durations[str(p)] = get_duration_seconds(p)
        if progress_callback and (i + 1) % 10 == 0:
            await progress_callback("extracting", i + 1, len(downloaded), f"Extracting durations... ({i+1}/{len(downloaded)})")
    
    if progress_callback:
        await progress_callback("extracting", len(downloaded), len(downloaded), "Duration extraction complete.")
    
    # 3. Bucket
    if progress_callback:
        await progress_callback("bucketing", 0, 1, "Bucketing by duration...")
    buckets = bucket_by_duration(durations, tolerance=0.5)
    if progress_callback:
        await progress_callback("bucketing", 1, 1, f"Created {len(buckets)} duration buckets.")
    
    # 4. Cluster
    if progress_callback:
        await progress_callback("clustering", 0, len(buckets), f"Clustering within {len(buckets)} buckets...")
    clusters = cluster_audio_files(buckets, distance_threshold=25.0)
    if progress_callback:
        await progress_callback("clustering", len(buckets), len(buckets), f"Clustering complete. Found {len(clusters)} clusters.")
    
    # 5. Map back to URLs
    url_clusters = []
    cluster_paths_list = [] # Keep paths for potential post-processing
    for cluster in clusters:
        url_cluster = [path_to_url[str(p)] for p in cluster if str(p) in path_to_url]
        if url_cluster:
            url_clusters.append(url_cluster)
            cluster_paths_list.append(cluster)
            
    # 6. Post-processing: YAMNet Caller Tune Classification
    caller_tune_cluster = []
    final_clusters = []
    
    caller_tune_idx = None
    if enable_yamnet and YAMNET_AVAILABLE:
        if progress_callback:
            await progress_callback("post_processing", 0, len(url_clusters), "Running YAMNet classification for caller tunes...")
            
        for i, (url_cluster, paths_cluster) in enumerate(zip(url_clusters, cluster_paths_list)):
            if progress_callback and (i + 1) % 5 == 0:
                await progress_callback("post_processing", i + 1, len(url_clusters), f"Running YAMNet classification... ({i+1}/{len(url_clusters)})")
                
            # Pick one representative file from the cluster
            rep_path = paths_cluster[0]
            
            # Run the heavy TF/librosa work in a thread pool to avoid blocking the event loop
            is_ct = await asyncio.to_thread(is_caller_tune, str(rep_path))
            
            if is_ct:
                caller_tune_cluster.extend(url_cluster)
            else:
                final_clusters.append(url_cluster)
                
        if caller_tune_cluster:
            # Add the large caller tune cluster at the end
            final_clusters.append(caller_tune_cluster)
            caller_tune_idx = len(final_clusters) - 1
    else:
        if enable_yamnet and not YAMNET_AVAILABLE:
            print("Warning: YAMNet dependencies not found. Skipping post-processing.")
        final_clusters = url_clusters
    
    if progress_callback:
        await progress_callback("done", len(final_clusters), len(final_clusters), f"Done! {len(final_clusters)} clusters found.")
        
    return final_clusters, download_errors, caller_tune_idx
