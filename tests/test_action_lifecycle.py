import threading
import time
import unittest
import http.client
import json
from unittest.mock import patch

from presence_guard.controller import Controller, GuardError
from presence_guard.server import LocalServer, make_handler


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class GateActions:
    demo = False
    allow_lock = True
    allow_sheet = False

    def __init__(self, fail=False):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.fail = fail

    def run(self, action):
        self.calls.append(action)
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError("Test gate timed out")
        if self.fail:
            raise OSError("PLANTED_PRIVATE_DRIVER_ERROR")
        return "PLANTED_PRIVATE_SUCCESS_DETAIL"


class ActionLifecycleTests(unittest.TestCase):
    def make_service(self, fail=False):
        clock, actions = Clock(), GateActions(fail)
        entered = threading.Event()

        def runner(service, session, stop):
            entered.set()
            stop.wait(3)
            return True

        service = Controller(camera=True, actions=actions, clock=clock, camera_runner=runner)
        service.start("privacy")
        self.assertTrue(entered.wait(1))
        self.addCleanup(actions.release.set)
        self.addCleanup(lambda: (actions.release.set(), service.close()))
        clock.now = 5.01
        return service, actions, clock

    def finish(self, service):
        worker = getattr(service, "action_thread", None)
        if worker:
            worker.join(1)
            self.assertFalse(worker.is_alive())

    def test_stop_and_status_remain_responsive_during_a_real_request(self):
        service, actions, _ = self.make_service()
        trigger = threading.Thread(target=service.pulse)
        trigger.start()
        self.assertTrue(actions.entered.wait(1))
        finished = threading.Event()
        result = []

        def stop():
            result.append(service.stop())
            result.append(service.snapshot())
            service.observe(service.session, 1)
            service.pulse()
            finished.set()

        stopper = threading.Thread(target=stop)
        stopper.start()
        try:
            self.assertTrue(finished.wait(0.5), "Stop/status blocked behind the desktop action")
            self.assertFalse(result[0]["active"])
            self.assertTrue(result[0]["action_running"])
            self.assertIn("cannot cancel", result[0]["last_action"]["message"])
        finally:
            actions.release.set()
            trigger.join(1)
            stopper.join(1)
            self.finish(service)
        self.assertEqual(service.snapshot()["last_action"]["status"], "requested")
        self.assertFalse(service.snapshot()["action_running"])

    def test_stop_before_worker_commit_cancels_without_calling_the_device(self):
        service, actions, _ = self.make_service()
        actions.release.set()
        with service.lock:
            service.pulse()
            service.stop()
        self.finish(service)
        self.assertEqual(actions.calls, [])
        self.assertEqual(service.snapshot()["last_action"]["status"], "cancelled")

    def test_inflight_action_prevents_restart_and_sanitizes_late_failure(self):
        service, actions, _ = self.make_service(fail=True)
        service.pulse()
        self.assertTrue(actions.entered.wait(1))
        service.stop()
        service.worker.join(1)
        with self.assertRaisesRegex(GuardError, "desktop request"):
            service.start("privacy")
        actions.release.set()
        self.finish(service)
        state = service.snapshot()
        self.assertEqual(state["last_action"]["status"], "failed")
        self.assertIn("may still be unlocked", state["last_action"]["message"])
        self.assertNotIn("PLANTED_PRIVATE", repr(state))
        self.assertIsNone(service.start("privacy")["last_action"])

    def test_reason_is_captured_before_new_frames_arrive(self):
        service, actions, _ = self.make_service()
        actions.release.set()
        with service.lock:
            service.pulse()
            service.observe(service.session, 1)
        self.finish(service)
        self.assertIn("first frame", service.snapshot()["last_action"]["reason"])

    def test_worker_start_failure_does_not_leave_a_permanent_busy_state(self):
        service, actions, _ = self.make_service()
        with patch("presence_guard.controller.threading.Thread.start", side_effect=RuntimeError("PLANTED_PRIVATE")):
            service.pulse()
        state = service.snapshot()
        self.assertFalse(state["action_running"])
        self.assertEqual(state["last_action"]["status"], "failed")
        self.assertEqual(actions.calls, [])
        self.assertNotIn("PLANTED_PRIVATE", repr(state))

    def test_close_is_bounded_and_permanently_disarms(self):
        service, actions, _ = self.make_service()
        service.pulse()
        self.assertTrue(actions.entered.wait(1))
        start = time.monotonic()
        service.close(timeout=0.05)
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertTrue(service.snapshot()["closed"])
        with self.assertRaisesRegex(GuardError, "closed"):
            service.start("observe")
        actions.release.set()
        self.finish(service)

    def test_repeated_triggers_never_grow_a_queue(self):
        service, actions, clock = self.make_service()
        service.pulse()
        self.assertTrue(actions.entered.wait(1))
        service.observe(service.session, 1)
        service.observe(service.session, 2)
        clock.now += 2.1
        service.observe(service.session, 2)
        for index in range(100):
            service.pulse()
        self.assertEqual(actions.calls, ["lock_camera_fault"])
        self.assertTrue(any("skipped" in event for event in service.snapshot()["events"]))
        self.assertLessEqual(len(service.snapshot()["events"]), 8)
        actions.release.set()
        self.finish(service)
        self.assertEqual(actions.calls, ["lock_camera_fault"])

    def test_http_stop_status_and_restart_are_responsive_with_an_outstanding_request(self):
        service, actions, _ = self.make_service()
        service.pulse()
        self.assertTrue(actions.entered.wait(1))
        token = "a" * 64
        server = LocalServer(("127.0.0.1", 0), make_handler(service, token))
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        try:
            for method, path, extra, expected in [("POST", "/api/stop", {}, 200),
                                                 ("GET", "/api/status", None, 200),
                                                 ("POST", "/api/start", {"mode": "privacy"}, 409)]:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=0.5)
                try:
                    body = None if extra is None else json.dumps({"token": token, **extra})
                    connection.request(method, path, body, {"Origin": f"http://127.0.0.1:{server.server_port}", "Content-Type": "application/json"})
                    response = connection.getresponse()
                    state = json.loads(response.read())
                    self.assertEqual(response.status, expected)
                    if expected == 200:
                        self.assertFalse(state["active"])
                        self.assertTrue(state["action_running"])
                    self.assertNotIn("PLANTED_PRIVATE", repr(state))
                finally:
                    connection.close()
        finally:
            actions.release.set()
            self.finish(service)
            server.shutdown()
            server.server_close()
            worker.join(1)

    def test_shutdown_has_one_deadline_for_blocked_camera_and_action_workers(self):
        clock, actions = Clock(), GateActions()
        camera_release = threading.Event()
        entered = threading.Event()

        def runner(service, session, stop):
            entered.set()
            camera_release.wait(3)
            return True

        service = Controller(camera=True, actions=actions, clock=clock, camera_runner=runner)
        try:
            service.start("privacy")
            self.assertTrue(entered.wait(1))
            clock.now = 5.01
            service.pulse()
            self.assertTrue(actions.entered.wait(1))
            start = time.monotonic()
            service.close(timeout=0.3)
            elapsed = time.monotonic() - start
            self.assertGreaterEqual(elapsed, 0.25)
            self.assertLess(elapsed, 0.5)
            self.assertTrue(service.snapshot()["closed"])
        finally:
            actions.release.set()
            camera_release.set()
            service.close()
            self.finish(service)


if __name__ == "__main__":
    unittest.main()
