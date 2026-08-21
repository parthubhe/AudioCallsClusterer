import urllib.request
import wave
import librosa
import numpy as np
from scipy.spatial.distance import euclidean

urls = [
    "https://smartcollectv5.s3.amazonaws.com/smartcollect2/mixmonitor/tuljabhavani/2026/08/19/c479e9c8-5b16-4ba1-89ad-5aa58552fa7f.wav",
    "https://smartcollectv5.s3.amazonaws.com/smartcollect2/mixmonitor/vedika/2026/08/19/d0bdec50-7ae6-436b-afdd-159399af43da.wav",
    "https://smartcollectv5.s3.amazonaws.com/smartcollect2/mixmonitor/vedika/2026/08/19/ea5b30af-d8c2-4d2f-912e-6291c8441bf3.wav"
]

files = ["file1.wav", "file2.wav", "file3.wav"]

def get_duration(path):
    with wave.open(path, 'r') as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate)

def get_mfcc(path):
    y, sr = librosa.load(path, sr=None)
    mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    return np.mean(mfccs.T, axis=0)

for i, url in enumerate(urls):
    print(f"Downloading file {i+1}...")
    urllib.request.urlretrieve(url, files[i])

print("\n--- Durations & Buckets ---")
durations = []
buckets = []
mfccs = []
for i, f in enumerate(files):
    dur = get_duration(f)
    bucket = round(dur / 0.5) * 0.5
    mfcc = get_mfcc(f)
    durations.append(dur)
    buckets.append(bucket)
    mfccs.append(mfcc)
    print(f"File {i+1}: Duration = {dur:.4f}s, Bucket = {bucket}")

print("\n--- MFCC Distances ---")
print(f"Distance 1 vs 2: {euclidean(mfccs[0], mfccs[1]):.4f}")
print(f"Distance 1 vs 3: {euclidean(mfccs[0], mfccs[2]):.4f}")
print(f"Distance 2 vs 3: {euclidean(mfccs[1], mfccs[2]):.4f}")
