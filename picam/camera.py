"""Camera capture — picamera2 on Pi, OpenCV webcam fallback elsewhere."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# Prefer 4:3 so the viewfinder looks like a normal photo, not a thin strip.
_LANDSCAPE = (1296, 972)
_PORTRAIT = (972, 1296)


@dataclass
class CameraInfo:
    backend: str
    detail: str


class Camera:
    """Thread-safe preview + still/video helpers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._picam = None
        self._cv = None
        self._cv_index = 0
        self._recording = False
        self._writer = None
        self._record_path: Path | None = None
        self._info = CameraInfo("none", "Camera not opened")
        self._last_frame: Image.Image | None = None
        self._rotation = 0  # 0, 90, 180, 270

    @property
    def info(self) -> CameraInfo:
        return self._info

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def rotation(self) -> int:
        return self._rotation

    @rotation.setter
    def rotation(self, degrees: int) -> None:
        degrees = int(degrees) % 360
        if degrees not in {0, 90, 180, 270}:
            degrees = 0
        with self._lock:
            if degrees == self._rotation:
                return
            self._rotation = degrees
            # Soft-rotate every frame in software (Pi ISP cannot do 90°).

    def open(self) -> CameraInfo:
        with self._lock:
            if self._picam is not None or self._cv is not None:
                return self._info
            if self._try_picamera2():
                return self._info
            if self._try_opencv():
                return self._info
            self._info = CameraInfo("none", "No camera found (picamera2 / OpenCV)")
            return self._info

    def close(self) -> None:
        with self._lock:
            self._stop_writer_unlocked()
            if self._picam is not None:
                try:
                    self._picam.stop()
                    self._picam.close()
                except Exception:
                    pass
                self._picam = None
            if self._cv is not None:
                try:
                    self._cv.release()
                except Exception:
                    pass
                self._cv = None
            self._info = CameraInfo("none", "Camera closed")

    def grab(self) -> Image.Image | None:
        with self._lock:
            frame = self._grab_unlocked()
            if frame is not None:
                self._last_frame = frame
                if self._recording and self._writer is not None:
                    self._write_frame_unlocked(frame)
            return frame

    def last_frame(self) -> Image.Image | None:
        with self._lock:
            return self._last_frame

    def still(self) -> Image.Image | None:
        frame = self.grab()
        if frame is None:
            with self._lock:
                return self._last_frame
        return frame

    def start_video(self, path: Path, fps: int = 24) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._recording:
                return
            frame = self._grab_unlocked() or self._last_frame
            if frame is None:
                raise RuntimeError("No frame yet — wait for the preview.")
            w, h = frame.size
            self._open_writer_unlocked(path, w, h, fps)
            self._recording = True
            self._record_path = path

    def stop_video(self) -> Path | None:
        with self._lock:
            path = self._record_path
            self._stop_writer_unlocked()
            self._recording = False
            self._record_path = None
            return path

    def _try_picamera2(self) -> bool:
        try:
            from picamera2 import Picamera2
        except Exception:
            return False
        try:
            cam = Picamera2()
            config = cam.create_preview_configuration(
                main={"size": _LANDSCAPE, "format": "RGB888"}
            )
            cam.configure(config)
            cam.start()
            time.sleep(0.3)
            self._picam = cam
            self._info = CameraInfo("picamera2", f"Raspberry Pi camera {_LANDSCAPE[0]}x{_LANDSCAPE[1]}")
            return True
        except Exception as exc:
            self._info = CameraInfo("none", f"picamera2 failed: {exc}")
            return False

    def _try_opencv(self) -> bool:
        try:
            import cv2
        except Exception:
            return False
        for index in (0, 1, 2):
            cap = cv2.VideoCapture(index)
            if not cap.isOpened():
                cap.release()
                continue
            # Ask for 4:3 when the driver allows it.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, _LANDSCAPE[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, _LANDSCAPE[1])
            ok, _ = cap.read()
            if not ok:
                cap.release()
                continue
            self._cv = cap
            self._cv_index = index
            self._info = CameraInfo("opencv", f"Webcam index {index}")
            return True
        return False

    def _grab_unlocked(self) -> Image.Image | None:
        image = None
        if self._picam is not None:
            try:
                arr = self._picam.capture_array("main")
                image = self._array_to_image(arr)
            except Exception:
                return None
        elif self._cv is not None:
            try:
                import cv2

                ok, frame = self._cv.read()
                if not ok or frame is None:
                    return None
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = self._array_to_image(rgb)
            except Exception:
                return None
        if image is None:
            return None
        return self._apply_rotation(image)

    @staticmethod
    def _array_to_image(arr: np.ndarray) -> Image.Image | None:
        if arr is None or arr.ndim != 3 or arr.shape[2] < 3:
            return None
        # Picamera2 / OpenCV buffers are HxWxC. Build RGB without relying on
        # a possibly padded 4th channel.
        rgb = np.ascontiguousarray(arr[:, :, :3])
        height, width = rgb.shape[0], rgb.shape[1]
        image = Image.fromarray(rgb, mode="RGB")
        if image.size != (width, height):
            image = image.resize((width, height), Image.Resampling.NEAREST)
        return image

    def _apply_rotation(self, image: Image.Image) -> Image.Image:
        degrees = int(self._rotation) % 360
        if degrees == 0:
            return image
        # PIL rotate is CCW; expand keeps a true rectangle (no squash).
        return image.rotate(-degrees, expand=True, resample=Image.Resampling.BILINEAR)

    def _open_writer_unlocked(self, path: Path, width: int, height: int, fps: int) -> None:
        try:
            import cv2
        except Exception as exc:
            raise RuntimeError("OpenCV is required for video recording") from exc
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        writer = cv2.VideoWriter(str(path), fourcc, float(fps), (width, height))
        if not writer.isOpened():
            raise RuntimeError(f"Could not open video file {path}")
        self._writer = writer

    def _write_frame_unlocked(self, frame: Image.Image) -> None:
        if self._writer is None:
            return
        try:
            import cv2

            arr = np.asarray(frame.convert("RGB"))
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            self._writer.write(bgr)
        except Exception:
            pass

    def _stop_writer_unlocked(self) -> None:
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                pass
        self._writer = None


def stitch_panorama(frames: list[Image.Image]) -> Image.Image:
    """Stitch left-to-right stills into a panorama."""
    if not frames:
        raise RuntimeError("No panorama frames")
    if len(frames) == 1:
        return frames[0].convert("RGB")
    try:
        import cv2
    except Exception as exc:
        raise RuntimeError("OpenCV is required for panorama") from exc

    bgrs = []
    for im in frames:
        rgb = np.asarray(im.convert("RGB"))
        bgrs.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    stitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    status, out = stitcher.stitch(bgrs)
    if status != cv2.Stitcher_OK or out is None:
        heights = [im.size[1] for im in frames]
        h = min(heights)
        resized = [im.convert("RGB").resize((int(im.size[0] * h / im.size[1]), h)) for im in frames]
        total_w = sum(im.size[0] for im in resized)
        canvas = Image.new("RGB", (total_w, h))
        x = 0
        for im in resized:
            canvas.paste(im, (x, 0))
            x += im.size[0]
        return canvas
    rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)
