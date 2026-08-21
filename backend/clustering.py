import wave
import hashlib
import numpy as np
import librosa
from collections import defaultdict
import scipy.spatial.distance as distance
import asyncio
def pcm_hash(path: str) -> str:
    """Compute SHA1 hash of the raw PCM data directly (ignoring 44-byte WAV header)."""
    try:
        from pathlib import Path
        data = Path(path).read_bytes()
        # Skip the 44-byte header to hash only the audio data
        return hashlib.sha1(data[44:]).hexdigest()
    except Exception as e:
        print(f"Error hashing {path}: {e}")
        return str(path)  # Fallback to unique path

def extract_features(path: str):
    """Extract MFCC features for similarity comparison using librosa."""
    try:
        # Load audio (downsample to 16kHz for speed and consistency)
        y, sr = librosa.load(path, sr=16000)
        # Trim leading/trailing silence
        y, _ = librosa.effects.trim(y)
        # Extract MFCC
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        # We need a fixed length vector for simple distance computation.
        # Mean and variance across time usually works well as a robust fingerprint.
        mfcc_mean = np.mean(mfcc, axis=1)
        mfcc_std = np.std(mfcc, axis=1)
        return np.concatenate((mfcc_mean, mfcc_std))
    except Exception as e:
        print(f"Error extracting features for {path}: {e}")
        return None

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

def cluster_audio_files(duration_buckets: dict, distance_threshold=25.0, progress_callback=None):
    """
    Apply Two-tier clustering within each bucket.
    Tier 1: PCM Hash
    Tier 2: MFCC similarity
    Returns a list of clusters, where each cluster is a list of file paths (or URLs).
    """
    final_clusters = []
    
    total_buckets = len(duration_buckets)
    completed_buckets = 0
    step = max(1, total_buckets // 100)

    for dur_key, file_paths in duration_buckets.items():
        completed_buckets += 1
        if progress_callback and completed_buckets % step == 0:
            progress_callback("clustering", completed_buckets, total_buckets, f"Clustering buckets... ({completed_buckets}/{total_buckets})")
            
        if len(file_paths) == 1:
            final_clusters.append(file_paths)
            continue
        
        # Tier 1: Exact Hash matching
        hash_groups = defaultdict(list)
        for i_path, path in enumerate(file_paths):
            h = pcm_hash(path)
            hash_groups[h].append(path)
            
        # Representatives for Tier 2 (one per hash group)
        representatives = list(hash_groups.keys())
        rep_to_paths = hash_groups
        
        if len(representatives) == 1:
            # Everything in this bucket is identical
            final_clusters.append(rep_to_paths[representatives[0]])
            continue
            
        # Tier 2: MFCC similarity among representatives
        features = {}
        valid_reps = []
        for i_rep, rep in enumerate(representatives):
            feat = extract_features(rep_to_paths[rep][0])
            if feat is not None:
                features[rep] = feat
                valid_reps.append(rep)
        
        if not valid_reps:
            # Fallback if feature extraction failed for all
            for rep in representatives:
                final_clusters.append(rep_to_paths[rep])
            continue
            
        # Compute pairwise distances
        uf = UnionFind(valid_reps)
        for i in range(len(valid_reps)):
            for j in range(i + 1, len(valid_reps)):
                rep_i = valid_reps[i]
                rep_j = valid_reps[j]
                dist = distance.euclidean(features[rep_i], features[rep_j])
                if dist < distance_threshold:
                    uf.union(rep_i, rep_j)
                    
        # Group by UnionFind components
        components = defaultdict(list)
        for rep in valid_reps:
            root = uf.find(rep)
            # Add all actual files that map to this representative
            components[root].extend(rep_to_paths[rep])
            
        for root, paths in components.items():
            final_clusters.append(paths)
            
        # Handle failed feature extractions (isolated clusters)
        for rep in representatives:
            if rep not in valid_reps:
                final_clusters.append(rep_to_paths[rep])

    return final_clusters
