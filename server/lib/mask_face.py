from pathlib import Path

import cv2


def _detect_faces_mediapipe(img):
    try:
        import mediapipe as mp
        # mediapipe 버전에 따라 top-level mp.solutions가 없을 수 있음
        if hasattr(mp, "solutions"):
            face_detection_mod = mp.solutions.face_detection
        else:
            from mediapipe.python.solutions import face_detection as face_detection_mod
    except Exception:
        return None

    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    boxes = []
    with face_detection_mod.FaceDetection(model_selection=1, min_detection_confidence=0.5) as fd:
        res = fd.process(rgb)
        if not res.detections:
            return []
        for det in res.detections:
            bb = det.location_data.relative_bounding_box
            x = max(0, int(bb.xmin * w))
            y = max(0, int(bb.ymin * h))
            bw = int(bb.width * w)
            bh = int(bb.height * h)
            boxes.append((x, y, bw, bh))
    return boxes


def _detect_faces_haar(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    return list(faces)


def _expand_box(x, y, w, h, img_w, img_h, ratio=0.35):
    ex = int(w * ratio)
    ey = int(h * ratio)
    x0 = max(0, x - ex)
    y0 = max(0, y - ey)
    x1 = min(img_w, x + w + ex)
    y1 = min(img_h, y + h + ey)
    return x0, y0, x1 - x0, y1 - y0


def mask_faces(input_path: Path, output_path: Path, method: str = "blur", blur_kernel: int = 91):
    img = cv2.imread(str(input_path))
    if img is None:
        raise ValueError(f"Failed to read image: {input_path}")

    h, w = img.shape[:2]
    detector = "mediapipe"
    faces = _detect_faces_mediapipe(img)
    if faces is None:
        detector = "haar"
        faces = _detect_faces_haar(img)

    # fallback chain: mediapipe 결과가 0이면 haar도 한번 시도
    if detector == "mediapipe" and len(faces) == 0:
        alt = _detect_faces_haar(img)
        if len(alt) > 0:
            detector = "haar-fallback"
            faces = alt

    for (x, y, fw, fh) in faces:
        x, y, fw, fh = _expand_box(x, y, fw, fh, w, h)
        roi = img[y : y + fh, x : x + fw]
        if roi.size == 0:
            continue
        if method == "pixelate":
            small = cv2.resize(roi, (max(1, fw // 20), max(1, fh // 20)), interpolation=cv2.INTER_LINEAR)
            masked = cv2.resize(small, (fw, fh), interpolation=cv2.INTER_NEAREST)
        else:
            k = blur_kernel if blur_kernel % 2 == 1 else blur_kernel + 1
            masked = cv2.GaussianBlur(roi, (k, k), 0)
        img[y : y + fh, x : x + fw] = masked

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), img)
    return {"face_count": len(faces), "detector": detector}
