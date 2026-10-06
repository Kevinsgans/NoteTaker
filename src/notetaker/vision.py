import time

import cv2
import numpy as np

from .config import Config


def ordered(corners):
    points = np.array(corners, dtype=np.float32).reshape(4, 2)
    if not np.isfinite(points).all():
        raise ValueError("corners must be finite")
    center = points.mean(axis=0)
    points = points[np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))]
    points = np.roll(points, -np.argmin(points.sum(axis=1)), axis=0)
    if not cv2.isContourConvex(points.astype(np.int32)) or cv2.contourArea(points) < 100:
        raise ValueError("corners must describe a convex page")
    return points


def warp(image, corners):
    p = ordered(corners)
    h, w = image.shape[:2]
    if np.any(p < 0) or np.any(p[:, 0] >= w) or np.any(p[:, 1] >= h):
        raise ValueError("corners lie outside the image")
    width = int(max(np.linalg.norm(p[1] - p[0]), np.linalg.norm(p[2] - p[3])))
    height = int(max(np.linalg.norm(p[3] - p[0]), np.linalg.norm(p[2] - p[1])))
    target = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    return cv2.warpPerspective(image, cv2.getPerspectiveTransform(p, target), (width, height))


def fingerprint(image):
    gray = cv2.cvtColor(cv2.resize(image, (9, 8)), cv2.COLOR_BGR2GRAY)
    return "".join("1" if v else "0" for v in (gray[:, 1:] > gray[:, :-1]).flat)


class OpenCVProcessor:
    def __init__(self, config: Config):
        self.config = config

    def detect(self, image):
        h, w = image.shape[:2]
        scale = min(1, 1000 / max(h, w))
        small = cv2.resize(image, None, fx=scale, fy=scale)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True):
            area = cv2.contourArea(contour)
            if (
                not 0.15 * small.shape[0] * small.shape[1]
                < area
                < 0.98 * small.shape[0] * small.shape[1]
            ):
                continue
            approx = cv2.approxPolyDP(contour, 0.025 * cv2.arcLength(contour, True), True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                return ordered(approx.reshape(4, 2) / scale)
        return None

    def quality(self, image):
        # Normalize scale so preview and full-resolution blur scores are comparable.
        scale = min(1, 800 / max(image.shape[:2]))
        small = cv2.resize(image, None, fx=scale, fy=scale)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())
        reasons = []
        if blur < self.config.blur_min:
            reasons.append("blur")
        if not self.config.brightness_min <= brightness <= self.config.brightness_max:
            reasons.append("exposure")
        return {
            "blur": blur,
            "brightness": brightness,
            "clipped_fraction": float((gray >= 254).mean()),
            "acceptable": not reasons,
            "reasons": reasons,
        }

    def process(self, image, corners=None, force=False):
        start = time.monotonic()
        points = self.detect(image) if corners is None else ordered(corners)
        if points is None:
            if not force:
                raise ValueError("No page found; use manual capture or adjust the page")
            h, w = image.shape[:2]
            points = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
        corrected = warp(image, points)
        quality = self.quality(corrected)
        if not quality["acceptable"] and not force:
            raise ValueError("Capture rejected: " + ", ".join(quality["reasons"]))
        return {
            "corrected": corrected,
            "corners": points.tolist(),
            "quality": quality,
            "preprocess_ms": (time.monotonic() - start) * 1000,
        }


class StabilityGate:
    def __init__(self, seconds=0.75):
        self.seconds = seconds
        self.anchor = None
        self.since = None
        self.latched = False
        self.scene = None
        self.changed_since = None

    def update(self, corners, acceptable, image, now=None):
        now = time.monotonic() if now is None else now
        scene = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (32, 32)).astype(float)
        if self.latched:
            changed = corners is None or (
                self.scene is not None and np.mean(abs(scene - self.scene)) > 30
            )
            self.changed_since = (
                (self.changed_since if self.changed_since is not None else now) if changed else None
            )
            if self.changed_since is not None and now - self.changed_since >= 0.5:
                self.latched = False
                self.anchor = self.since = self.changed_since = None
            return False
        if corners is None or not acceptable:
            self.anchor = self.since = None
            return False
        normalized = np.array(corners) / np.array([image.shape[1], image.shape[0]])
        if self.anchor is None or np.max(np.linalg.norm(normalized - self.anchor, axis=1)) > 0.015:
            self.anchor, self.since = normalized, now
        return now - self.since >= self.seconds

    def captured(self, image):
        self.latched = True
        self.scene = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (32, 32)).astype(float)
        self.since = None
