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

async def download_one(session, url, dest, sem, max_retries=3):
    """Downloads one URL into the cache with retry logic."""
    for attempt in range(max_retries):
        try:
            async with sem:
                # Add a timeout to prevent hanging connections
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
                    dest.write_bytes(data)
            return dest, url
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to download {url} after {max_retries} attempts: {e}")
                if dest.exists():
                    try:
                        dest.unlink()
                    except:
                        pass
                return None, url
            await asyncio.sleep(1 + attempt)  # Simple backoff

async def download_all(urls, concurrency=15, progress_callback=None):
    """Concurrent download of URLs, skipping cached ones, with progress reporting."""
    sem = asyncio.Semaphore(concurrency)
    
    def scan_cache(url_list):
        cached = []
        to_dl = []
        for url in url_list:
            url_hash = hashlib.md5(url.encode()).hexdigest()
            dest = CACHE_DIR / f"{url_hash}.wav"
            # Check if exists and is not an empty file
            if dest.exists() and dest.stat().st_size > 0:
                cached.append((dest, url))
            else:
                to_dl.append((url, dest))
        return cached, to_dl

    # 1. Synchronously scan cache in a background thread to prevent blocking Uvicorn
    cached_results, to_download = await asyncio.to_thread(scan_cache, urls)
            
    if progress_callback and cached_results:
        await progress_callback("downloading", len(cached_results), len(urls), f"Found {len(cached_results)} cached files, skipping download...")
    
    total = len(to_download)
    completed = 0
    errors = []
    
    if total == 0:
        return cached_results, errors

    async def download_with_progress(session, url, dest):
        nonlocal completed
        result = await download_one(session, url, dest, sem)
        completed += 1
        if progress_callback:
            # We add len(cached_results) to show absolute progress across all URLs
            current_abs = completed + len(cached_results)
            await progress_callback("downloading", current_abs, len(urls), f"Downloading files... ({current_abs}/{len(urls)})")
        if result[0] is None:
            errors.append({"url": url, "error": "Download failed"})
        return result

    async with aiohttp.ClientSession() as session:
        tasks = [download_with_progress(session, u, d) for u, d in to_download]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
    downloaded = [r for r in results if isinstance(r, tuple) and r[0] is not None]
    return cached_results + downloaded, errors


def get_duration_seconds(path) -> float:
    """Reads duration from WAV header based on file size (assuming 8kHz 16-bit mono)."""
    try:
        if isinstance(path, str):
            from pathlib import Path
            path = Path(path)
        size = path.stat().st_size
        # (size - header) / (sample_rate * channels * sample_width)
        return max(0.0, (size - 44) / 16000.0)
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
    
    loop = asyncio.get_running_loop()
    
    def sync_progress(phase, current, total, message):
        if progress_callback:
            asyncio.run_coroutine_threadsafe(
                progress_callback(phase, current, total, message), 
                loop
            )

    def do_extraction(downloaded_files):
        durs = {}
        total = len(downloaded_files)
        step = max(1, total // 100)  # Update exactly every 1%
        for i, (p, u) in enumerate(downloaded_files):
            durs[str(p)] = get_duration_seconds(p)
            if (i + 1) % step == 0:
                sync_progress("extracting", i + 1, total, f"Extracting durations... ({i+1}/{total})")
        return durs

    durations = await asyncio.to_thread(do_extraction, downloaded)
    
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
        
    def do_clustering(buckets_dict):
        return cluster_audio_files(buckets_dict, distance_threshold=25.0, progress_callback=sync_progress)
        
    clusters = await asyncio.to_thread(do_clustering, buckets)
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


def list_cache_files() -> list[dict]:
    """List all valid .wav files in the audio cache directory."""
    files = []
    for f in CACHE_DIR.glob("*.wav"):
        try:
            size = f.stat().st_size
            if size > 0:
                files.append({
                    "path": str(f),
                    "filename": f.name,
                    "size_bytes": size
                })
        except Exception:
            continue
    return files


async def run_pipeline_from_cache(file_paths: list[str], progress_callback=None, enable_yamnet=False):
    """
    Run clustering pipeline on files already in cache (skips download).
    file_paths: list of absolute paths to .wav files in audio_cache.
    Returns: (clusters_as_paths, errors, caller_tune_idx)
    """
    total = len(file_paths)
    print(f"Batch processing {total} cached files...")
    
    if progress_callback:
        await progress_callback("starting", 0, total, f"Starting batch pipeline with {total} files...")
    
    loop = asyncio.get_running_loop()
    
    def sync_progress(phase, current, total, message):
        if progress_callback:
            asyncio.run_coroutine_threadsafe(
                progress_callback(phase, current, total, message), 
                loop
            )

    # 1. Extract Duration (in background thread)
    if progress_callback:
        await progress_callback("extracting", 0, total, f"Extracting durations from {total} files...")

    def do_extraction(paths):
        durs = {}
        step = max(1, len(paths) // 100)
        for i, p in enumerate(paths):
            durs[p] = get_duration_seconds(p)
            if (i + 1) % step == 0:
                sync_progress("extracting", i + 1, len(paths), f"Extracting durations... ({i+1}/{len(paths)})")
        return durs

    durations = await asyncio.to_thread(do_extraction, file_paths)
    
    if progress_callback:
        await progress_callback("extracting", total, total, "Duration extraction complete.")
    
    # 2. Bucket
    if progress_callback:
        await progress_callback("bucketing", 0, 1, "Bucketing by duration...")
    buckets = bucket_by_duration(durations, tolerance=0.5)
    if progress_callback:
        await progress_callback("bucketing", 1, 1, f"Created {len(buckets)} duration buckets.")
    
    # 3. Cluster (in background thread)
    if progress_callback:
        await progress_callback("clustering", 0, len(buckets), f"Clustering within {len(buckets)} buckets...")
        
    def do_clustering(buckets_dict):
        return cluster_audio_files(buckets_dict, distance_threshold=25.0, progress_callback=sync_progress)
        
    clusters = await asyncio.to_thread(do_clustering, buckets)
    if progress_callback:
        await progress_callback("clustering", len(buckets), len(buckets), f"Clustering complete. Found {len(clusters)} clusters.")
    
    # 4. Post-processing: YAMNet (optional)
    caller_tune_idx = None
    if enable_yamnet and YAMNET_AVAILABLE:
        caller_tune_cluster = []
        final_clusters = []
        
        if progress_callback:
            await progress_callback("post_processing", 0, len(clusters), "Running YAMNet classification...")
            
        for i, paths_cluster in enumerate(clusters):
            if progress_callback and (i + 1) % 5 == 0:
                await progress_callback("post_processing", i + 1, len(clusters), f"YAMNet classification... ({i+1}/{len(clusters)})")
            
            rep_path = paths_cluster[0]
            is_ct = await asyncio.to_thread(is_caller_tune, str(rep_path))
            
            if is_ct:
                caller_tune_cluster.extend(paths_cluster)
            else:
                final_clusters.append(paths_cluster)
                
        if caller_tune_cluster:
            final_clusters.append(caller_tune_cluster)
            caller_tune_idx = len(final_clusters) - 1
    else:
        final_clusters = clusters
    
    if progress_callback:
        await progress_callback("done", len(final_clusters), len(final_clusters), f"Done! {len(final_clusters)} clusters found.")
        
    return final_clusters, [], caller_tune_idx
