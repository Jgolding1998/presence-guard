import threading
import unittest

from presence_guard.controller import Controller, GuardError


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class FakeActions:
    def __init__(self, demo=True, allow_lock=False, allow_sheet=False, fail=False):
        self.demo, self.allow_lock, self.allow_sheet = demo, allow_lock, allow_sheet
        self.fail = fail
        self.calls = []

    def run(self, action):
        self.calls.append(action)
        if self.fail:
            raise OSError("PLANTED_PRIVATE_DRIVER_ERROR")
        return "PLANTED_PRIVATE_SUCCESS_DETAIL"


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.actions = FakeActions()
        self.service = Controller(clock=self.clock, actions=self.actions)

    def test_inactive_on_boot_even_with_action_permissions(self):
        service = Controller(camera=True, actions=FakeActions(False, True, True), clock=self.clock)
        state = service.snapshot()
        self.assertFalse(state["active"])
        self.assertFalse(state["lock_armed"])
        self.assertFalse(state["sheet_armed"])
        self.assertIsNone(service.worker)

    def test_mode_is_mutually_exclusive_and_requires_stop(self):
        state = self.service.start("boss")
        self.assertTrue(state["sheet_armed"])
        self.assertFalse(state["lock_armed"])
        with self.assertRaises(GuardError):
            self.service.start("privacy")
        state = self.service.stop()
        self.assertFalse(state["sheet_armed"])
        self.assertFalse(state["lock_armed"])
        self.assertTrue(self.service.start("privacy")["lock_armed"])

    def test_demo_action_never_opens_or_locks(self):
        self.service.start("privacy")
        self.service.set_demo(0)
        self.clock.now = 30
        self.service.pulse()
        self.assertEqual(self.actions.calls, [])
        self.clock.now = 30.01
        self.service.pulse()
        self.assertEqual(self.actions.calls, ["lock_absence"])
        self.assertEqual(self.service.snapshot()["last_action"]["status"], "simulated")

    def test_failure_and_success_details_are_sanitized(self):
        self.actions.fail = True
        self.service.start("privacy")
        self.service.set_demo(None)
        self.clock.now = 5.01
        self.service.pulse()
        state = self.service.snapshot()
        self.assertEqual(state["last_action"]["status"], "failed")
        self.assertIn("may still be unlocked", state["last_action"]["message"])
        self.assertNotIn("PLANTED_PRIVATE", repr(state))
        self.clock.now = 8
        self.service.pulse()
        self.assertEqual(len(self.actions.calls), 1)

    def test_boss_failure_message_does_not_mention_lock(self):
        self.actions.fail = True
        self.service.start("boss")
        self.service.set_demo(2)
        self.clock.now = 2
        self.service.pulse()
        message = self.service.snapshot()["last_action"]["message"]
        self.assertIn("Workbook could not be opened", message)
        self.assertNotIn("unlocked", message)

    def test_camera_exit_after_stale_frames_still_requests_lock(self):
        actions = FakeActions(False, True)

        def runner(service, session, stop):
            service.observe(session, 1)
            self.clock.now = 6
            return True

        service = Controller(camera=True, actions=actions, clock=self.clock, camera_runner=runner)
        service.start("privacy")
        service.worker.join(1)
        self.assertEqual(actions.calls, ["lock_camera_fault"])
        self.assertEqual(service.snapshot()["last_action"]["status"], "requested")

    def test_demo_fault_does_not_reset_last_good_frame(self):
        self.service.start("privacy")
        self.clock.now = 1
        self.service.set_demo(None)
        self.clock.now = 5
        self.service.pulse()
        self.assertEqual(self.actions.calls, [])
        self.clock.now = 5.01
        self.service.pulse()
        self.assertEqual(self.actions.calls, ["lock_camera_fault"])

    def test_stop_ignores_stale_observations_and_disarms(self):
        self.service.start("privacy")
        session = self.service.session
        self.service.stop()
        self.clock.now = 100
        self.service.observe(session, 2)
        self.service.pulse()
        self.assertEqual(self.actions.calls, [])
        self.assertIsNone(self.service.snapshot()["people"])

    def test_invalid_modes_and_counts_are_fixed_errors(self):
        for mode in ("shell", "privacy; whoami", None, 1, {}, ["privacy"]):
            with self.subTest(mode=mode), self.assertRaises(GuardError):
                self.service.start(mode)
        for people in (True, -1, 3, 1.1, "2", {}, 10 ** 400):
            with self.subTest(people=str(people)[:20]), self.assertRaises(GuardError):
                self.service.set_demo(people)

    def test_launch_permissions_gate_modes(self):
        service = Controller(camera=True, actions=FakeActions(False), clock=self.clock)
        self.assertEqual(service.allowed_modes(), ["observe"])
        for mode in ("privacy", "boss"):
            with self.assertRaises(GuardError):
                service.start(mode)
        with self.assertRaises(GuardError):
            service.set_demo(1)

    def test_no_camera_factory_does_not_leave_armed_session(self):
        service = Controller(camera=True, actions=FakeActions(False, True), clock=self.clock)
        with self.assertRaises(GuardError):
            service.start("privacy")
        state = service.snapshot()
        self.assertFalse(state["active"])
        self.assertFalse(state["lock_armed"])

    def test_release_must_finish_before_restart(self):
        entered, closing, released = threading.Event(), threading.Event(), threading.Event()

        def runner(service, session, stop):
            entered.set()
            stop.wait(2)
            closing.set()
            released.wait(2)
            return True

        service = Controller(camera=True, actions=FakeActions(False), clock=self.clock, camera_runner=runner)
        try:
            service.start("observe")
            self.assertTrue(entered.wait(1))
            state = service.stop()
            self.assertTrue(state["camera_closing"])
            self.assertTrue(closing.wait(1))
            with self.assertRaises(GuardError):
                service.start("observe")
            released.set()
            service.worker.join(1)
            self.assertFalse(service.snapshot()["camera_closing"])
        finally:
            released.set()
            service.close()

    def test_failed_release_prevents_restart(self):
        service = Controller(camera=True, actions=FakeActions(False), clock=self.clock,
                             camera_runner=lambda *args: False)
        service.start("observe")
        service.worker.join(1)
        service.stop()
        self.assertTrue(service.snapshot()["camera_closing"])
        with self.assertRaises(GuardError):
            service.start("observe")

    def test_events_are_bounded_and_tokens_not_in_snapshot(self):
        for _ in range(100):
            self.service.start("observe")
            self.service.stop()
        self.assertEqual(len(self.service.snapshot()["events"]), 8)
        self.assertNotIn("token", self.service.snapshot())
