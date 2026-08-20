import wave
import hashlib
import numpy as np
import librosa
from collections import defaultdict
import scipy.spatial.distance as distance

def pcm_hash(path: str) -> str:
    """Compute SHA1 hash of the raw PCM data (ignoring headers)."""
    try:
        with wave.open(str(path), 'rb') as wf:
            pcm = wf.readframes(wf.getnframes())
        return hashlib.sha1(pcm).hexdigest()
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

def cluster_audio_files(duration_buckets: dict, distance_threshold=25.0):
    """
    Apply Two-tier clustering within each bucket.
    Tier 1: PCM Hash
    Tier 2: MFCC similarity
    Returns a list of clusters, where each cluster is a list of file paths (or URLs).
    """
    final_clusters = []

    for dur_key, file_paths in duration_buckets.items():
        if len(file_paths) == 1:
            final_clusters.append(file_paths)
            continue
        
        # Tier 1: Exact Hash matching
        hash_groups = defaultdict(list)
        for path in file_paths:
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
        for rep in representatives:
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
