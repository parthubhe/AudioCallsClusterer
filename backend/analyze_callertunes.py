import os
import sys
import shutil
import librosa
import numpy as np
from pathlib import Path

# Add the current directory to python path to import yamnet_classifier
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from yamnet_classifier import get_yamnet_model, get_class_indices

TARGET_SR = 16000
SPEECH_THRESHOLD = 0.2
MUSIC_THRESHOLD = 0.3
MIN_OVERLAP_FRAMES = 2

def classify_caller_tune(file_path, model, speech_idx, music_idx):
    try:
        # Load audio
        wav_data, _ = librosa.load(file_path, sr=TARGET_SR, mono=True)
        
        # Run model
        scores, embeddings, spectrogram = model(wav_data)
        
        # scores shape: (N_frames, 521)
        speech_scores = scores[:, speech_idx].numpy()
        music_scores = scores[:, music_idx].numpy()
        
        # Boolean masks
        speech_active = speech_scores >= SPEECH_THRESHOLD
        music_active = music_scores >= MUSIC_THRESHOLD
        
        # Overlap analysis
        overlap = speech_active & music_active
        overlap_frames = np.sum(overlap)
        
        if overlap_frames >= MIN_OVERLAP_FRAMES:
            return "speech_over_music"
        
        # If no significant overlap, check if there's significant speech anywhere
        # If there are at least 2 frames of speech, consider it half_and_half
        # (Assuming it's not overlapping music, so it must be temporally separated)
        if np.sum(speech_active) >= 2:
            return "half_and_half"
            
        return "pure_music"
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return "error"

def main():
    # Look for exported_callertunes relative to the backend directory or cwd
    base_dir = Path("exported_callertunes")
    if not base_dir.exists():
        # Fallback if run from one directory up
        base_dir = Path("backend/exported_callertunes")
        
    if not base_dir.exists():
        print(f"Error: Directory exported_callertunes not found.")
        return
        
    print("Loading YAMNet model...")
    model = get_yamnet_model()
    class_names = get_class_indices()
    
    speech_idx = class_names.index('Speech')
    music_idx = class_names.index('Music')
    
    dirs = {
        "pure_music": base_dir / "pure_music",
        "half_and_half": base_dir / "half_and_half",
        "speech_over_music": base_dir / "speech_over_music"
    }
    
    for d in dirs.values():
        d.mkdir(exist_ok=True)
        
    files = list(base_dir.glob("*.wav"))
    print(f"Found {len(files)} audio files to process.")
    
    for i, file_path in enumerate(files):
        print(f"[{i+1}/{len(files)}] {file_path.name}", end=" -> ")
        category = classify_caller_tune(file_path, model, speech_idx, music_idx)
        print(category)
        
        if category in dirs:
            dest = dirs[category] / file_path.name
            shutil.move(str(file_path), str(dest))

if __name__ == "__main__":
    main()
