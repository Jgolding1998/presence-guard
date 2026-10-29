import threading
import unittest
from unittest import mock

from presence_guard import camera
from presence_guard.camera import run_camera


class Frame:
    shape = (480, 640, 3)


class Capture:
    opened = True
    released = False
    ok = True
    release_error = False

    def isOpened(self):
        return self.opened

    def set(self, *args):
        return True

    def read(self):
        return self.ok, Frame()

    def release(self):
        if self.release_error:
            raise OSError("private driver")
        self.released = True


class CV:
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4

    def __init__(self):
        self.capture = Capture()
        self.calls = 0
        self.boxes = [(0, 0, 10, 10)] * 2

    def HOGDescriptor(self):
        return self

    def HOGDescriptor_getDefaultPeopleDetector(self):
        return "built-in"

    def setSVMDetector(self, value):
        pass

    def VideoCapture(self, device):
        self.calls += 1
        return self.capture

    def resize(self, frame, size):
        if size[0] > 640 or size[1] > 480:
            raise AssertionError("Unbounded detector work")
        return frame

    def detectMultiScale(self, frame, **kwargs):
        return self.boxes, []


class Sink:
    def __init__(self, stop):
        self.stop = stop
        self.observations = []
        self.faults = []

    def observe(self, session, people):
        self.observations.append((session, people))
        self.stop.set()

    def camera_fault(self, session):
        self.faults.append(session)


class CameraTests(unittest.TestCase):
    def test_count_only_adapter_closes_capture(self):
        stop, cv = threading.Event(), CV()
        sink = Sink(stop)
        self.assertTrue(run_camera(sink, 7, stop, cv=cv))
        self.assertEqual(sink.observations, [(7, 2)])
        self.assertTrue(cv.capture.released)

    def test_stop_before_start_opens_nothing(self):
        stop, cv = threading.Event(), CV()
        stop.set()
        self.assertTrue(run_camera(Sink(stop), 1, stop, cv=cv))
        self.assertEqual(cv.calls, 0)

    def test_unopened_camera_releases_and_reports_fault(self):
        stop, cv = threading.Event(), CV()
        cv.capture.opened = False
        sink = Sink(stop)
        self.assertTrue(run_camera(sink, 1, stop, cv=cv))
        self.assertEqual(sink.faults, [1])
        self.assertTrue(cv.capture.released)

    def test_read_failure_ends_session_without_fake_absence(self):
        stop, cv = threading.Event(), CV()
        cv.capture.ok = False
        sink = Sink(stop)
        with mock.patch.object(camera, "INTERVAL_SECONDS", 0.001):
            self.assertTrue(run_camera(sink, 1, stop, cv=cv))
        self.assertEqual(sink.observations, [])
        self.assertEqual(sink.faults, [1])
        self.assertTrue(cv.capture.released)

    def test_transient_read_failure_recovers_without_fault(self):
        stop, cv = threading.Event(), CV()
        reads = iter([(False, None), (False, None), (True, Frame())])
        cv.capture.read = lambda: next(reads)
        sink = Sink(stop)
        with mock.patch.object(camera, "INTERVAL_SECONDS", 0.001):
            self.assertTrue(run_camera(sink, 1, stop, cv=cv))
        self.assertEqual(sink.observations, [(1, 2)])
        self.assertEqual(sink.faults, [])

    def test_release_failure_is_not_reported_as_closed(self):
        stop, cv = threading.Event(), CV()
        cv.capture.release_error = True
        self.assertFalse(run_camera(Sink(stop), 1, stop, cv=cv))

    def test_oversized_frame_is_rejected_before_detector(self):
        stop, cv = threading.Event(), CV()
        cv.capture.read = lambda: (True, type("InvalidFrame", (), {"shape": (1_000_000, 640, 3)})())
        sink = Sink(stop)
        self.assertTrue(run_camera(sink, 1, stop, cv=cv))
        self.assertEqual(sink.observations, [])
        self.assertEqual(sink.faults, [1])

    def test_stop_during_read_never_emits_late_observation(self):
        stop, cv = threading.Event(), CV()

        def read():
            stop.set()
            return True, Frame()

        cv.capture.read = read
        sink = Sink(stop)
        self.assertTrue(run_camera(sink, 1, stop, cv=cv))
        self.assertEqual(sink.observations, [])
        self.assertTrue(cv.capture.released)
