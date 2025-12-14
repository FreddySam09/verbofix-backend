# therapy_sessions/ml_model.py
import os, time, logging
from django.conf import settings

logger = logging.getLogger(__name__)

# optional imports
try:
    import numpy as np
    import librosa
except Exception:
    librosa = None
    np = None

# keras
try:
    from tensorflow.keras.models import load_model
except Exception:
    load_model = None

# whisper optional
try:
    import whisper
except Exception:
    whisper = None

MODEL_PATH = getattr(settings, "ML_MODEL_PATH", None)
WHISPER_MODEL_NAME = getattr(settings, "WHISPER_MODEL_NAME", "base")

# load model at import time (so subsequent requests are fast)
keras_model = None
if MODEL_PATH and load_model is not None and os.path.exists(MODEL_PATH):
    try:
        logger.info("Loading Keras model from %s", MODEL_PATH)
        keras_model = load_model(MODEL_PATH)
    except Exception:
        logger.exception("Failed to load Keras model")

whisper_model = None
if whisper is not None:
    try:
        logger.info("Loading Whisper model: %s", WHISPER_MODEL_NAME)
        whisper_model = whisper.load_model(WHISPER_MODEL_NAME)
    except Exception:
        logger.exception("Failed to load Whisper model")

# helper transcription
def transcribe(path):
    if whisper_model is None:
        return ""
    try:
        r = whisper_model.transcribe(path, language="en")
        return r.get("text", "").strip()
    except Exception:
        logger.exception("Whisper transcription failed")
        return ""

def _predict_from_model(features, keras_model, threshold=0.7):
    """Given features (n, ...) run keras_model and return binary preds and confidences list."""
    if features is None or features.shape[0] == 0 or keras_model is None:
        return [], []
    try:
        preds = keras_model.predict(features, verbose=0).reshape(-1)
        confidences = [float(p) for p in preds]
        bins = [1 if p > threshold else 0 for p in preds]
        return bins, confidences
    except Exception:
        logger.exception("_predict_from_model failed")
        return [], []

def _smooth_preds(pred_bins, confidences, window_size=3):
    """Simple majority smoothing across a sliding window; returns list of (bin, conf)."""
    out = []
    n = len(pred_bins)
    for i in range(n):
        start = max(0, i - window_size//2)
        end = min(n, i + window_size//2 + 1)
        window = pred_bins[start:end]
        sm = 1 if sum(window) > (len(window)//2) else 0
        conf = float(confidences[i]) if confidences else 0.0
        out.append((int(sm), conf))
    return out

def _group_consecutive(pred_pairs, sr, hop_length, chunk_duration, audio_duration):
    """
    Group consecutive same-label chunks into time ranges.
    Returns list of dicts: {start, end, label, avg_conf, chunk_indices}
    """
    if not pred_pairs:
        return []

    hop_time = hop_length / float(sr)
    ranges = []
    current_label = int(pred_pairs[0][0])
    current_conf = [float(pred_pairs[0][1])]
    start_idx = 0
    chunk_indices = [0]

    for i in range(1, len(pred_pairs)):
        lab = int(pred_pairs[i][0])
        conf = float(pred_pairs[i][1])
        if lab == current_label:
            current_conf.append(conf)
            chunk_indices.append(i)
        else:
            start_time = min(start_idx * hop_time, audio_duration)
            end_time = min((start_idx * hop_time) + chunk_duration, audio_duration)
            avg_conf = float(sum(current_conf) / len(current_conf)) if current_conf else 0.0
            label_str = "Stammered" if current_label == 1 else "Fluent"
            ranges.append({
                "start": float(start_time),
                "end": float(end_time),
                "label": label_str,
                "avg_conf": avg_conf,
                "chunks": list(chunk_indices),
            })
            # reset
            current_label = lab
            current_conf = [conf]
            start_idx = i
            chunk_indices = [i]

    # close last
    start_time = min(start_idx * hop_time, audio_duration)
    end_time = min((start_idx * hop_time) + chunk_duration, audio_duration)
    avg_conf = float(sum(current_conf) / len(current_conf)) if current_conf else 0.0
    label_str = "Stammered" if current_label == 1 else "Fluent"
    ranges.append({
        "start": float(start_time),
        "end": float(end_time),
        "label": label_str,
        "avg_conf": avg_conf,
        "chunks": list(chunk_indices),
    })
    return ranges

def _generate_report_from_ranges(ranges, total_chunks, audio_duration, transcription):
    stammered_chunks = 0
    fluent_chunks = 0
    stammered_periods = []

    for r in ranges:
        n_chunks = len(r.get("chunks", []))
        if r["label"] == "Stammered":
            stammered_periods.append(f"{r['start']:.1f}-{r['end']:.1f}s")
            stammered_chunks += n_chunks
        else:
            fluent_chunks += n_chunks

    total_chunks = int(total_chunks) if total_chunks else (stammered_chunks + fluent_chunks)
    stammer_rate = (stammered_chunks / total_chunks) * 100.0 if total_chunks > 0 else 0.0
    stammer_rate_val = float(round(stammer_rate, 2))
    if stammer_rate_val < 30:
        severity = "Low"
    elif stammer_rate_val <= 65:
        severity = "Moderate"
    else:
        severity = "High"

    recs_map = {
        "Low": ["Continue practicing fluent speech patterns.", "Engage in regular reading aloud exercises."],
        "Moderate": ["Practice slow and deliberate speech exercises.", "Use breathing techniques to reduce stammering.", "Consider consulting a speech therapist."],
        "High": ["Work with a speech therapist for personalized guidance.", "Practice pausing techniques during speech.", "Use mindfulness exercises to reduce anxiety."]
    }

    return {
        "date": time.strftime("%Y-%m-%d"),
        "stammeredPeriods": stammered_periods if stammered_periods else ["None"],
        "stammeredChunks": int(stammered_chunks),
        "fluentChunks": int(fluent_chunks),
        "stammerRate": f"{stammer_rate_val:.2f}%",
        "stammer_rate": stammer_rate_val,
        "audioDuration": f"{float(audio_duration):.1f}s" if audio_duration else None,
        "severity": severity,
        "recommendations": recs_map[severity],
        "transcription": str(transcription),
    }

def analyze_audio(path):
    """
    New analyze_audio compatible with your Flask behavior:
    - returns stammeredPeriods, stammeredChunks, fluentChunks etc.
    - uses keras_model when available, otherwise does a heuristic fallback
    - keeps raw_output for debugging
    """
    logger.info("analyze_audio called for %s", path)

    transcription = ""
    if whisper_model is not None:
        try:
            transcription = transcribe(path)
        except Exception:
            logger.exception("whisper transcribe failed")
            transcription = ""

    y = None
    sr = None
    duration = None
    raw_output = {}

    if librosa is not None:
        try:
            y, sr = librosa.load(path, sr=16000)
            duration = float(librosa.get_duration(y=y, sr=sr))
            raw_output["duration"] = duration
        except Exception:
            logger.exception("librosa.load failed")

    # Early exit if no audio
    if y is None or sr is None:
        # return minimal report (fallback)
        report = {
            "date": time.strftime("%Y-%m-%d"),
            "stammeredPeriods": ["None"],
            "stammeredChunks": 0,
            "fluentChunks": 0,
            "stammerRate": "0.00%",
            "stammer_rate": 0.0,
            "audioDuration": None,
            "severity": "Low",
            "recommendations": ["Continue practicing fluent speech patterns."],
            "transcription": transcription or ""
        }
        return report

    # Build features in the same way as your frontend/training pipeline
    try:
        # Reuse the same preprocess logic from file: compute features (MFCC, delta, energy, etc.)
        CHUNK_DURATION = 1.0
        HOP_LENGTH = 128
        FRAME_LENGTH = 512
        N_MFCC = 13
        TARGET_FRAMES = 86

        chunk_size = int(CHUNK_DURATION * sr)
        hop_size = HOP_LENGTH
        num_chunks = max(0, (len(y) - chunk_size) // hop_size + 1)
        features_list = []
        chunk_energies = []

        for i in range(num_chunks):
            s = i * hop_size
            e = s + chunk_size
            chunk = y[s:e]
            if len(chunk) != chunk_size:
                continue
            mfcc = librosa.feature.mfcc(y=chunk, sr=sr, n_mfcc=N_MFCC, n_fft=FRAME_LENGTH, hop_length=HOP_LENGTH).T
            if mfcc.shape[0] < TARGET_FRAMES:
                mfcc = np.pad(mfcc, ((0, TARGET_FRAMES - mfcc.shape[0]), (0, 0)), mode='constant')
            elif mfcc.shape[0] > TARGET_FRAMES:
                mfcc = mfcc[:TARGET_FRAMES]

            delta_mfcc = librosa.feature.delta(mfcc, axis=0)
            energy = librosa.feature.rms(y=chunk, frame_length=FRAME_LENGTH, hop_length=HOP_LENGTH).T
            if energy.shape[0] < TARGET_FRAMES:
                energy = np.pad(energy, ((0, TARGET_FRAMES - energy.shape[0]), (0, 0)), mode='constant')
            elif energy.shape[0] > TARGET_FRAMES:
                energy = energy[:TARGET_FRAMES]

            pause_threshold = np.percentile(energy, 10) if len(energy) else 0.0
            pause_duration = (energy < pause_threshold).astype(float)
            energy_variance = np.var(energy, axis=0, keepdims=True)
            energy_variance = np.repeat(energy_variance, TARGET_FRAMES, axis=0)
            pitch_proxy = np.mean(mfcc[:, 1:3], axis=1, keepdims=True)
            combined = np.concatenate([mfcc, delta_mfcc, pause_duration, energy_variance, pitch_proxy], axis=1)
            features_list.append(combined)
            chunk_energies.append(float(np.mean(energy)))

        features = np.array(features_list, dtype=np.float32) if features_list else np.zeros((0,))
        raw_output["num_chunks"] = int(features.shape[0]) if features_list else 0
        raw_output["chunk_energies_sample"] = chunk_energies[:50]
    except Exception:
        logger.exception("feature extraction failed")
        features = np.zeros((0,))
        raw_output["num_chunks"] = 0

    # If we have a keras model, run prediction pipeline
    pred_bins = []
    confidences = []
    if keras_model is not None and features is not None and getattr(features, "shape", [0])[0] > 0:
        try:
            pred_bins, confidences = _predict_from_model(features, keras_model, threshold=0.7)
            raw_output["model_conf_mean"] = float(np.mean(confidences)) if confidences else None
        except Exception:
            logger.exception("model prediction failed")
            pred_bins, confidences = [], []
    else:
        # heuristic fallback using chunk energies (low-energy => stammer-like heuristic)
        try:
            if raw_output.get("chunk_energies_sample"):
                energies = raw_output["chunk_energies_sample"]
                thr = float(np.percentile(energies, 25)) if len(energies) else 0.0
                pred_bins = [1 if e < thr else 0 for e in energies]
                confidences = [float(abs(e - thr)) for e in energies]  # rough confidence proxy
                raw_output["heuristic_threshold"] = thr
            else:
                pred_bins = []
                confidences = []
        except Exception:
            logger.exception("heuristic fallback failed")
            pred_bins = []
            confidences = []

    # smooth and group
    smoothed = _smooth_preds(pred_bins, confidences, window_size=3) if pred_bins else []
    grouped = _group_consecutive(smoothed, sr=sr, hop_length=HOP_LENGTH, chunk_duration=CHUNK_DURATION, audio_duration=duration if duration else 0.0)
    report_core = _generate_report_from_ranges(grouped, total_chunks=raw_output.get("num_chunks", 0), audio_duration=duration, transcription=transcription)

    # attach raw_output for debugging and return
    report_core["raw_output"] = raw_output
    return report_core

