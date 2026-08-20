import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' # Suppress TF logs

import tensorflow as tf
import tensorflow_hub as hub
import librosa
import numpy as np

# Load the model from TensorFlow Hub
# This will download the model (~4MB) on first run and cache it.
_MODEL_HANDLE = 'https://tfhub.dev/google/yamnet/1'
_MODEL = None

def get_yamnet_model():
    global _MODEL
    if _MODEL is None:
        print("Loading YAMNet model from TF Hub...")
        _MODEL = hub.load(_MODEL_HANDLE)
    return _MODEL

# The model expects audio at 16kHz
_TARGET_SR = 16000

# Top-level class indices in YAMNet:
# YAMNet has 521 classes. The class map is available inside the model,
# but for our purposes, we know the typical indices for Speech and Music from the AudioSet ontology.
# We will use the model's class_map to dynamically find them to be safe.
_CLASS_MAP = None

def get_class_indices():
    global _CLASS_MAP
    if _CLASS_MAP is None:
        model = get_yamnet_model()
        class_map_path = model.class_map_path().numpy().decode('utf-8')
        _CLASS_MAP = []
        with tf.io.gfile.GFile(class_map_path) as f:
            next(f)  # Skip header
            for line in f:
                _CLASS_MAP.append(line.strip().split(',')[2]) # Display name is the 3rd column
    return _CLASS_MAP

def is_caller_tune(audio_path: str) -> bool:
    """
    Analyzes an audio file using YAMNet.
    Returns True if it's primarily music/singing (Caller Tune),
    False if it's primarily speech (Operator Announcement).
    """
    try:
        model = get_yamnet_model()
        class_names = get_class_indices()
        
        # Find indices for relevant classes
        speech_idx = class_names.index('Speech')
        music_idx = class_names.index('Music')
        singing_idx = class_names.index('Singing') if 'Singing' in class_names else -1
        
        # Load audio using librosa (resamples to 16kHz and converts to mono as required by YAMNet)
        wav_data, _ = librosa.load(audio_path, sr=_TARGET_SR, mono=True)
        
        # Normalize to [-1.0, 1.0] as expected by YAMNet (librosa usually does this, but to be safe)
        if np.max(np.abs(wav_data)) > 0:
            wav_data = wav_data / np.max(np.abs(wav_data))
        
        # Run YAMNet
        # YAMNet returns: scores, embeddings, spectrogram
        scores, embeddings, spectrogram = model(wav_data)
        
        # Scores shape: (N_frames, 521). Average across frames to get global scores.
        mean_scores = np.mean(scores.numpy(), axis=0)
        
        speech_score = mean_scores[speech_idx]
        music_score = mean_scores[music_idx]
        singing_score = mean_scores[singing_idx] if singing_idx != -1 else 0.0
        
        print(f"YAMNet Scores for {os.path.basename(audio_path)} - Speech: {speech_score:.3f}, Music: {music_score:.3f}, Singing: {singing_score:.3f}")
        
        # Logic: If music or singing is substantial (e.g. > 0.1 and relatively high compared to speech), it's a caller tune.
        # Thresholds can be tuned. Caller tunes almost always have strong music scores.
        if (music_score > 0.1 or singing_score > 0.1) and (music_score + singing_score) > (speech_score * 0.5):
            return True
            
        return False
        
    except Exception as e:
        print(f"YAMNet classification failed for {audio_path}: {e}")
        # Default to False (leave it in exact-match clustering) if classification fails
        return False
