from __future__ import annotations

READ_RETRIES = 12
INTERVAL_SECONDS = 0.15


def run_camera(controller, session, stop, *, device=0, cv=None):
    capture = None
    closed = True
    try:
        if stop.is_set():
            return True
        if cv is None:
            import cv2 as cv
        detector = cv.HOGDescriptor()
        detector.setSVMDetector(cv.HOGDescriptor_getDefaultPeopleDetector())
        if stop.is_set():
            return True
        capture = cv.VideoCapture(device)
        closed = False
        if not capture.isOpened():
            raise RuntimeError("Camera unavailable")
        capture.set(cv.CAP_PROP_FRAME_WIDTH, 640)
        capture.set(cv.CAP_PROP_FRAME_HEIGHT, 480)
        failures = 0
        while not stop.is_set():
            ok, frame = capture.read()
            if stop.is_set():
                break
            if not ok:
                failures += 1
                if failures >= READ_RETRIES:
                    controller.camera_fault(session)
                    break
                stop.wait(INTERVAL_SECONDS)
                continue
            failures = 0
            height, width = frame.shape[:2]
            if not 1 <= height <= 8192 or not 1 <= width <= 8192:
                raise RuntimeError("Invalid frame dimensions")
            scale = min(640 / width, 480 / height)
            size = (max(1, int(width * scale)), max(1, int(height * scale)))
            if size[0] < 64 or size[1] < 128:
                raise RuntimeError("Frame is too small for the detector")
            frame = cv.resize(frame, size)
            boxes, _ = detector.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
            if not stop.is_set():
                controller.observe(session, min(20, len(boxes)))
            del frame
            stop.wait(INTERVAL_SECONDS)
    except Exception:
        controller.camera_fault(session)
    finally:
        if capture is not None:
            try:
                capture.release()
            except Exception:
                closed = False
            else:
                closed = True
    return closed
