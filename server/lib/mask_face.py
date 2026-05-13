from pathlib import Path
import os
import threading

import cv2
import numpy as np


os.environ.setdefault("GLOG_minloglevel", "2")
YUNET_MODEL = Path(__file__).resolve().parents[1] / "models" / "face_detection_yunet_2023mar.onnx"
SELFIE_SEGMENTER_MODEL = Path(__file__).resolve().parents[1] / "models" / "selfie_segmenter.tflite"
_MP_SEGMENTER = None
_MP_SEGMENTER_LOCK = threading.Lock()
_MP_MODULE = None


def _clip_box(x, y, w, h, img_w, img_h):
    x0 = max(0, int(round(x)))
    y0 = max(0, int(round(y)))
    x1 = min(img_w, int(round(x + w)))
    y1 = min(img_h, int(round(y + h)))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def _detect_faces_yunet(img):
    if not YUNET_MODEL.exists() or not hasattr(cv2, "FaceDetectorYN_create"):
        return None

    h, w = img.shape[:2]
    try:
        detector = cv2.FaceDetectorYN_create(
            str(YUNET_MODEL),
            "",
            (w, h),
            score_threshold=0.65,
            nms_threshold=0.3,
            top_k=5000,
        )
        _, faces = detector.detect(img)
    except Exception:
        return None

    if faces is None:
        return []

    boxes = []
    for face in faces:
        box = _clip_box(face[0], face[1], face[2], face[3], w, h)
        if box is not None:
            boxes.append(box)
    return boxes


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
            box = _clip_box(bb.xmin * w, bb.ymin * h, bb.width * w, bb.height * h, w, h)
            if box is not None:
                boxes.append(box)
    return boxes


def _detect_faces_haar(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    min_side = max(40, min(img.shape[:2]) // 18)
    cascades = [
        "haarcascade_frontalface_default.xml",
        "haarcascade_frontalface_alt2.xml",
        "haarcascade_profileface.xml",
    ]
    boxes = []
    seen = set()
    for cascade_name in cascades:
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + cascade_name)
        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.08,
            minNeighbors=6,
            minSize=(min_side, min_side),
        )
        for (x, y, w, h) in faces:
            key = (int(x / 10), int(y / 10), int(w / 10), int(h / 10))
            if key in seen:
                continue
            seen.add(key)
            boxes.append((int(x), int(y), int(w), int(h)))
    return boxes


def _detect_faces(img, preferred: str = "auto"):
    preferred = preferred.strip().lower()
    lookup = {
        "yunet": _detect_faces_yunet,
        "mediapipe": _detect_faces_mediapipe,
        "haar": _detect_faces_haar,
    }
    if preferred != "auto" and preferred not in lookup:
        preferred = "auto"

    if preferred == "auto":
        detectors = [
            ("yunet", _detect_faces_yunet),
            ("mediapipe", _detect_faces_mediapipe),
            ("haar", _detect_faces_haar),
        ]
    else:
        detectors = [(preferred, lookup.get(preferred))]

    last_detector = "unknown"
    for name, fn in detectors:
        if fn is None:
            continue
        faces = fn(img)
        if faces is None:
            continue
        last_detector = name
        if len(faces) > 0:
            return faces, name

    return [], last_detector


def _boxes_overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix0 = max(ax, bx)
    iy0 = max(ay, by)
    ix1 = min(ax + aw, bx + bw)
    iy1 = min(ay + ah, by + bh)
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    intersection = (ix1 - ix0) * (iy1 - iy0)
    smaller = min(aw * ah, bw * bh)
    return intersection / max(1, smaller) > 0.08


def _merge_overlapping_boxes(boxes):
    merged = []
    for box in boxes:
        x, y, w, h = box
        next_box = (int(x), int(y), int(w), int(h))
        did_merge = True
        while did_merge:
            did_merge = False
            remaining = []
            for existing in merged:
                if _boxes_overlap(next_box, existing):
                    x0 = min(next_box[0], existing[0])
                    y0 = min(next_box[1], existing[1])
                    x1 = max(next_box[0] + next_box[2], existing[0] + existing[2])
                    y1 = max(next_box[1] + next_box[3], existing[1] + existing[3])
                    next_box = (x0, y0, x1 - x0, y1 - y0)
                    did_merge = True
                else:
                    remaining.append(existing)
            merged = remaining
        merged.append(next_box)
    return merged


def _expand_box(x, y, w, h, img_w, img_h, x_ratio, top_ratio, bottom_ratio):
    ex = int(w * x_ratio)
    ey_top = int(h * top_ratio)
    ey_bottom = int(h * bottom_ratio)
    x0 = max(0, x - ex)
    y0 = max(0, y - ey_top)
    x1 = min(img_w, x + w + ex)
    y1 = min(img_h, y + h + ey_bottom)
    return x0, y0, x1 - x0, y1 - y0


def _env_ratio(name, default):
    raw = os.getenv(name, str(default))
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return default


def _mask_expansion_ratios():
    profile = os.getenv("FACE_MASK_PROFILE", "head").strip().lower()
    if profile in {"head", "privacy", "safe"}:
        defaults = (0.85, 0.70, 0.85)
    else:
        uniform = _env_ratio("FACE_MASK_PADDING", 0.35)
        defaults = (uniform, uniform, uniform)

    return (
        _env_ratio("FACE_MASK_PADDING_X", defaults[0]),
        _env_ratio("FACE_MASK_PADDING_TOP", defaults[1]),
        _env_ratio("FACE_MASK_PADDING_BOTTOM", defaults[2]),
    )


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default, min_value=None, max_value=None):
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _odd_kernel(size):
    size = max(3, int(size))
    return size if size % 2 == 1 else size + 1


def _mask_shape():
    shape = os.getenv("FACE_MASK_SHAPE", "segmentation").strip().lower()
    if shape in {"segment", "segmented", "segmentation", "mask", "person"}:
        return "segmentation"
    if shape in {"box", "bbox", "rectangle", "rect"}:
        return "box"
    return "segmentation"


def _draw_debug_boxes(img, raw_boxes, mask_boxes, output_path: Path, segment_masks=None):
    debug = img.copy()
    if segment_masks:
        overlay = debug.copy()
        for seg_mask in segment_masks:
            overlay[seg_mask > 0] = (255, 110, 30)
        debug = cv2.addWeighted(overlay, 0.35, debug, 0.65, 0)

    for (x, y, w, h) in raw_boxes:
        cv2.rectangle(debug, (x, y), (x + w, y + h), (80, 220, 80), thickness=6)
    for (x, y, w, h) in mask_boxes:
        cv2.rectangle(debug, (x, y), (x + w, y + h), (40, 40, 255), thickness=8)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), debug)


def _set_box(mask, box, value):
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return
    mask[y : y + h, x : x + w] = value


def _set_ellipse(mask, box, value):
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return
    center = (x + w // 2, y + h // 2)
    axes = (max(1, w // 2), max(1, h // 2))
    cv2.ellipse(mask, center, axes, 0, 0, 360, value, thickness=-1)


def _scale_box(box, scale, max_w, max_h):
    x, y, w, h = box
    return _clip_box(x * scale, y * scale, w * scale, h * scale, max_w, max_h)


def _keep_components_touching_box(mask, box):
    x, y, w, h = box
    labels_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if labels_count <= 1:
        return mask

    touched = set(np.unique(labels[y : y + h, x : x + w]).tolist())
    touched.discard(0)
    if not touched:
        largest = max(range(1, labels_count), key=lambda idx: stats[idx, cv2.CC_STAT_AREA])
        touched = {largest}

    return np.where(np.isin(labels, list(touched)), 255, 0).astype("uint8")


def _grabcut_segmentation_mask(img, raw_box, mask_box):
    mx, my, mw, mh = mask_box
    crop = img[my : my + mh, mx : mx + mw]
    if crop.size == 0:
        return None

    crop_h, crop_w = crop.shape[:2]
    rx, ry, rw, rh = raw_box
    rel_raw = _clip_box(rx - mx, ry - my, rw, rh, crop_w, crop_h)
    if rel_raw is None:
        return None

    max_side = _env_int("FACE_SEGMENT_MAX_SIDE", 960, min_value=320, max_value=1600)
    scale = min(1.0, max_side / max(crop_w, crop_h))
    if scale < 1.0:
        work_w = max(1, int(round(crop_w * scale)))
        work_h = max(1, int(round(crop_h * scale)))
        work_crop = cv2.resize(crop, (work_w, work_h), interpolation=cv2.INTER_AREA)
        work_raw = _scale_box(rel_raw, scale, work_w, work_h)
    else:
        work_crop = crop
        work_h, work_w = crop_h, crop_w
        work_raw = rel_raw

    if work_raw is None:
        return None

    crop_mask = np.full((work_h, work_w), cv2.GC_PR_BGD, dtype=np.uint8)

    border = max(4, int(min(work_w, work_h) * 0.025))
    crop_mask[:border, :] = cv2.GC_BGD
    crop_mask[-border:, :] = cv2.GC_BGD
    crop_mask[:, :border] = cv2.GC_BGD
    crop_mask[:, -border:] = cv2.GC_BGD

    pr_fg = _expand_box(
        *work_raw,
        work_w,
        work_h,
        x_ratio=0.55,
        top_ratio=0.65,
        bottom_ratio=0.75,
    )
    _set_box(crop_mask, pr_fg, cv2.GC_PR_FGD)
    _set_box(crop_mask, work_raw, cv2.GC_FGD)

    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(
            work_crop,
            crop_mask,
            None,
            bgd_model,
            fgd_model,
            _env_int("FACE_SEGMENT_ITERATIONS", 3, min_value=1, max_value=8),
            cv2.GC_INIT_WITH_MASK,
        )
    except Exception:
        return None

    fg = np.where(
        (crop_mask == cv2.GC_FGD) | (crop_mask == cv2.GC_PR_FGD),
        255,
        0,
    ).astype("uint8")

    fg = _keep_components_touching_box(fg, work_raw)

    raw_area = max(1, work_raw[2] * work_raw[3])
    if int(np.count_nonzero(fg)) < int(raw_area * _env_ratio("FACE_SEGMENT_MIN_RAW_RATIO", 0.70)):
        return None

    kernel_base = max(work_raw[2], work_raw[3])
    close_k = _odd_kernel(min(181, max(21, kernel_base * 0.16)))
    dilate_k = _odd_kernel(min(151, max(17, kernel_base * _env_ratio("FACE_SEGMENT_DILATE", 0.18))))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k)))
    fg = cv2.dilate(fg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_k, dilate_k)), iterations=1)

    # Always include the detected face core. Segmentation shapes the outside,
    # but this guarantees the privacy-critical facial features are covered.
    core = _expand_box(
        *work_raw,
        work_w,
        work_h,
        x_ratio=0.18,
        top_ratio=0.25,
        bottom_ratio=0.35,
    )
    _set_ellipse(fg, core, 255)

    if scale < 1.0:
        fg = cv2.resize(fg, (crop_w, crop_h), interpolation=cv2.INTER_NEAREST)

    full_mask = np.zeros(img.shape[:2], dtype="uint8")
    full_mask[my : my + mh, mx : mx + mw] = fg
    return full_mask


def _get_mediapipe_segmenter():
    global _MP_MODULE, _MP_SEGMENTER
    if not SELFIE_SEGMENTER_MODEL.exists():
        return None, None

    if _MP_SEGMENTER is not None and _MP_MODULE is not None:
        return _MP_MODULE, _MP_SEGMENTER

    try:
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
    except Exception:
        return None, None

    options = vision.ImageSegmenterOptions(
        base_options=python.BaseOptions(model_asset_path=str(SELFIE_SEGMENTER_MODEL)),
        running_mode=vision.RunningMode.IMAGE,
        output_category_mask=True,
        output_confidence_masks=False,
    )

    try:
        _MP_SEGMENTER = vision.ImageSegmenter.create_from_options(options)
    except Exception:
        _MP_SEGMENTER = None
        return None, None

    _MP_MODULE = mp
    return _MP_MODULE, _MP_SEGMENTER


def _mediapipe_segmentation_mask(img, raw_box, mask_box):
    mp, segmenter = _get_mediapipe_segmenter()
    if mp is None or segmenter is None:
        return None

    mx, my, mw, mh = mask_box
    crop = img[my : my + mh, mx : mx + mw]
    if crop.size == 0:
        return None

    crop_h, crop_w = crop.shape[:2]
    rx, ry, rw, rh = raw_box
    rel_raw = _clip_box(rx - mx, ry - my, rw, rh, crop_w, crop_h)
    if rel_raw is None:
        return None

    max_side = _env_int("FACE_SEGMENT_MAX_SIDE", 960, min_value=320, max_value=1600)
    scale = min(1.0, max_side / max(crop_w, crop_h))
    if scale < 1.0:
        work_w = max(1, int(round(crop_w * scale)))
        work_h = max(1, int(round(crop_h * scale)))
        work_crop = cv2.resize(crop, (work_w, work_h), interpolation=cv2.INTER_AREA)
        work_raw = _scale_box(rel_raw, scale, work_w, work_h)
    else:
        work_crop = crop
        work_h, work_w = crop_h, crop_w
        work_raw = rel_raw

    if work_raw is None:
        return None

    rgb = cv2.cvtColor(work_crop, cv2.COLOR_BGR2RGB)
    try:
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        with _MP_SEGMENTER_LOCK:
            result = segmenter.segment(mp_image)
    except Exception:
        return None

    if result is None or result.category_mask is None:
        return None

    category_mask = np.squeeze(result.category_mask.numpy_view())
    # The binary selfie model exposes the labeled person region as category 0
    # and background as 255 in its category mask.
    fg = np.where(category_mask == 0, 255, 0).astype("uint8")
    fg = _keep_components_touching_box(fg, work_raw)

    raw_area = max(1, work_raw[2] * work_raw[3])
    if int(np.count_nonzero(fg)) < int(raw_area * _env_ratio("FACE_SEGMENT_MIN_RAW_RATIO", 0.70)):
        return None

    kernel_base = max(work_raw[2], work_raw[3])
    close_k = _odd_kernel(min(151, max(17, kernel_base * 0.12)))
    dilate_k = _odd_kernel(min(131, max(15, kernel_base * _env_ratio("FACE_SEGMENT_DILATE", 0.18))))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k)))
    fg = cv2.dilate(fg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_k, dilate_k)), iterations=1)

    core = _expand_box(
        *work_raw,
        work_w,
        work_h,
        x_ratio=0.18,
        top_ratio=0.25,
        bottom_ratio=0.35,
    )
    _set_ellipse(fg, core, 255)

    if scale < 1.0:
        fg = cv2.resize(fg, (crop_w, crop_h), interpolation=cv2.INTER_NEAREST)

    full_mask = np.zeros(img.shape[:2], dtype="uint8")
    full_mask[my : my + mh, mx : mx + mw] = fg
    return full_mask


def _apply_mask(img, box, method, blur_kernel, segment_mask=None):
    x, y, fw, fh = box
    roi = img[y : y + fh, x : x + fw]
    if roi.size == 0:
        return

    if segment_mask is None:
        if method in {"solid", "blackout"}:
            cv2.rectangle(img, (x, y), (x + fw, y + fh), (24, 24, 24), thickness=-1)
        elif method == "pixelate":
            small = cv2.resize(roi, (max(1, fw // 20), max(1, fh // 20)), interpolation=cv2.INTER_LINEAR)
            masked = cv2.resize(small, (fw, fh), interpolation=cv2.INTER_NEAREST)
            masked = cv2.GaussianBlur(masked, (31, 31), 0)
            img[y : y + fh, x : x + fw] = masked
        else:
            k = blur_kernel if blur_kernel % 2 == 1 else blur_kernel + 1
            masked = cv2.GaussianBlur(roi, (k, k), 0)
            img[y : y + fh, x : x + fw] = masked
        return

    mask = segment_mask > 0
    if method in {"solid", "blackout"}:
        img[mask] = (24, 24, 24)
    elif method == "pixelate":
        small = cv2.resize(roi, (max(1, fw // 20), max(1, fh // 20)), interpolation=cv2.INTER_LINEAR)
        pixelated = cv2.resize(small, (fw, fh), interpolation=cv2.INTER_NEAREST)
        pixelated = cv2.GaussianBlur(pixelated, (31, 31), 0)
        scoped = segment_mask[y : y + fh, x : x + fw] > 0
        roi[scoped] = pixelated[scoped]
    else:
        k = blur_kernel if blur_kernel % 2 == 1 else blur_kernel + 1
        blurred = cv2.GaussianBlur(roi, (k, k), 0)
        scoped = segment_mask[y : y + fh, x : x + fw] > 0
        roi[scoped] = blurred[scoped]


def mask_faces(input_path: Path, output_path: Path, method: str = "blur", blur_kernel: int = 91):
    img = cv2.imread(str(input_path))
    if img is None:
        raise ValueError(f"Failed to read image: {input_path}")

    h, w = img.shape[:2]
    faces, detector = _detect_faces(img, os.getenv("FACE_DETECTOR", "auto").lower())
    faces = _merge_overlapping_boxes(faces)
    x_ratio, top_ratio, bottom_ratio = _mask_expansion_ratios()
    mask_boxes = []
    segment_masks = []
    segment_sources = []
    segmented_count = 0
    fallback_count = 0
    shape = _mask_shape()

    for (x, y, fw, fh) in faces:
        raw_box = (x, y, fw, fh)
        mask_box = _expand_box(
            x,
            y,
            fw,
            fh,
            w,
            h,
            x_ratio=x_ratio,
            top_ratio=top_ratio,
            bottom_ratio=bottom_ratio,
        )
        mask_boxes.append(mask_box)

        segment_mask = None
        if shape == "segmentation":
            segment_mask = _mediapipe_segmentation_mask(img, raw_box, mask_box)
            segment_source = "mediapipe-tasks-selfie" if segment_mask is not None else ""
            if segment_mask is None:
                segment_mask = _grabcut_segmentation_mask(img, raw_box, mask_box)
                segment_source = "grabcut" if segment_mask is not None else ""
            if segment_mask is not None:
                segment_masks.append(segment_mask)
                segment_sources.append(segment_source)
                segmented_count += 1
            else:
                fallback_count += 1

        if shape == "segmentation" and segment_mask is not None:
            _apply_mask(img, mask_box, method, blur_kernel, segment_mask=segment_mask)
        else:
            _apply_mask(img, mask_box, method, blur_kernel)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), img)
    debug_path = os.getenv("FACE_MASK_DEBUG_PATH", "").strip()
    if debug_path:
        original = cv2.imread(str(input_path))
        if original is not None:
            _draw_debug_boxes(original, faces, mask_boxes, Path(debug_path), segment_masks=segment_masks)

    return {
        "face_count": len(faces),
        "detector": detector,
        "boxes": faces,
        "mask_boxes": mask_boxes,
        "profile": os.getenv("FACE_MASK_PROFILE", "head").strip().lower(),
        "shape": shape,
        "segment_sources": segment_sources,
        "segmented_count": segmented_count,
        "fallback_count": fallback_count,
    }
