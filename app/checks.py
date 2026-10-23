"""
Checks on the voice clip and the photo, run the moment they arrive, so a bad
upload is caught at onboarding instead of as a silent failure later.

A photo cropped above the mouth produces no lip movement at all, with no error
(HANDOFF.md section 6); a clipped or very quiet recording clones badly. Each
check returns problems (the upload is refused, with the reason in plain words)
and warnings (accepted, but worth fixing).
"""
import math
import wave
from pathlib import Path

import numpy as np

MIN_SECONDS, GOOD_SECONDS, MAX_SECONDS = 15, 30, 180
QUIET_DBFS, LOW_DBFS = -42.0, -32.0
CLIP_LEVEL, CLIP_FRACTION = 0.985, 0.002


def read_wav(path):
    with wave.open(str(path)) as w:
        n, rate, ch, width = w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    x = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 1:
        x = (x - 128.0) / 128.0
    else:
        x /= float(2 ** (8 * width - 1))
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, rate


def _dbfs(v):
    return 20 * math.log10(max(float(v), 1e-9))


def check_voice(wav_path):
    """wav_path: the converted mono wav (build_twin.prepare_voice output)."""
    x, rate = read_wav(wav_path)
    secs = len(x) / float(rate) if rate else 0.0
    out = {"seconds": round(secs, 1), "problems": [], "warnings": []}
    if secs < 1:
        out["problems"].append("There is no audio in that file.")
        out["ok"] = False
        return out

    # loudness over the frames that hold speech (20 ms windows above a noise floor)
    win = max(1, int(0.02 * rate))
    frames = x[: len(x) // win * win].reshape(-1, win)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    floor = max(np.percentile(rms, 20) * 3, 10 ** (-50 / 20))
    voiced = rms > floor
    speech_secs = voiced.sum() * win / float(rate)
    level = _dbfs(np.sqrt((frames[voiced] ** 2).mean())) if voiced.any() else -120.0
    peak = float(np.abs(x).max())
    clipped = float((np.abs(x) >= CLIP_LEVEL).mean())
    out.update(speech_seconds=round(speech_secs, 1), level_dbfs=round(level, 1),
               peak_dbfs=round(_dbfs(peak), 1), clipped_fraction=round(clipped, 5))

    if secs < MIN_SECONDS:
        out["problems"].append(f"Too short: {secs:.0f} seconds. Read the whole paragraph, about 45 seconds.")
    elif speech_secs < MIN_SECONDS:
        out["problems"].append(f"Mostly silence: only about {speech_secs:.0f} seconds of speech. "
                               "Read the paragraph aloud from start to finish.")
    elif secs < GOOD_SECONDS:
        out["warnings"].append(f"{secs:.0f} seconds is enough, but 30 to 60 seconds clones the voice better.")
    if level < QUIET_DBFS:
        out["problems"].append("Too quiet: the voice is barely above the background. "
                               "Hold the microphone closer, about 20 cm away.")
    elif level < LOW_DBFS:
        out["warnings"].append("A little quiet. Speaking slightly closer to the microphone will help.")
    if clipped > CLIP_FRACTION:
        out["problems"].append("Clipped: the recording is too loud and distorts. "
                               "Move back from the microphone or speak a little softer.")
    if secs > MAX_SECONDS:
        out["warnings"].append("Over three minutes. Only the first minute or so is used.")
    out["ok"] = not out["problems"]
    return out


# ---------- photo ----------
def _yunet(path, model):
    import cv2
    img = cv2.imread(str(path))
    h, w = img.shape[:2]
    det = cv2.FaceDetectorYN.create(str(model), "", (w, h), score_threshold=0.75)
    _, faces = det.detect(img)
    found = []
    for f in (faces if faces is not None else []):
        x, y, fw, fh = (float(v) for v in f[:4])
        lm = [(float(f[4 + 2 * i]), float(f[5 + 2 * i])) for i in range(5)]   # eyes, nose, mouth corners
        found.append({"box": [x, y, fw, fh], "landmarks": lm, "score": float(f[14])})
    return found, w, h


def _haar(path):
    import cv2
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    h, w = img.shape[:2]
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = casc.detectMultiScale(img, scaleFactor=1.1, minNeighbors=6, minSize=(80, 80))
    return [{"box": [float(v) for v in f], "landmarks": None, "score": None} for f in faces], w, h


def check_photo(path, yunet_model=None):
    """Exactly one face, the whole face in frame, the mouth visible. Problems name what is wrong."""
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
    out = {"width": w, "height": h, "problems": [], "warnings": [], "face": None, "detector": None}
    if min(w, h) < 360:
        out["warnings"].append("The photo is small. Use one at least 512 pixels on each side.")
    try:
        if yunet_model and Path(yunet_model).exists():
            faces, w, h = _yunet(path, yunet_model)
            out["detector"] = "yunet"
        else:
            faces, w, h = _haar(path)
            out["detector"] = "haar"
    except Exception as e:                        # no OpenCV: guidance only, never a crash
        out["warnings"].append(f"The face check could not run ({type(e).__name__}).")
        out["ok"] = True
        return out

    if not faces:
        out["problems"].append("No face found. Face the camera with your whole face in the frame and good light.")
        out["ok"] = False
        return out
    big = max(faces, key=lambda f: f["box"][2] * f["box"][3])
    others = [f for f in faces if f is not big and f["box"][2] * f["box"][3] > 0.15 * big["box"][2] * big["box"][3]]
    if others:
        out["problems"].append("More than one face. Use a photo of just you.")
    x, y, fw, fh = big["box"]
    out["face"] = [int(x), int(y), int(fw), int(fh)]

    # Whole face in frame. YuNet's box runs from the forehead to the chin.
    if y + fh > h - 0.02 * h:
        out["problems"].append("Your chin is cut off. Step back or tilt the camera down so there is space below it.")
    elif y + fh > h - 0.08 * h:
        out["warnings"].append("Your chin is close to the bottom edge. A little more space below it is safer.")
    if y < 0.01 * h:
        out["problems"].append("The top of your head is cut off. Leave some space above it.")
    if x < 0.005 * w or x + fw > w - 0.005 * w:
        out["problems"].append("Part of your face is cut off at the side. Centre your face in the frame.")
    lm = big["landmarks"]
    if lm:
        (rex, rey), (lex, ley), (nx, ny), (rmx, rmy), (lmx, lmy) = lm
        mouth_y = max(rmy, lmy)
        if mouth_y >= h - 2 or rmy <= ny or lmy <= ny:
            out["problems"].append("Your mouth isn't visible. The whole mouth must be in the photo, uncovered.")
        roll = abs(math.degrees(math.atan2(ley - rey, lex - rex)))
        roll = min(roll, 180 - roll)
        if roll > 15:
            out["warnings"].append("Your head is tilted. Hold it level and look straight at the camera.")
        out["landmarks"] = [[round(a), round(b)] for a, b in lm]
    if fw < 0.15 * w:
        out["warnings"].append("Your face is small in the frame. Move closer.")
    out["ok"] = not out["problems"]
    return out
