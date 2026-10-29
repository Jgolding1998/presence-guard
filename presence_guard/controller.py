from __future__ import annotations

import threading
import time
from collections import deque

from .actions import Actions
from .presence import Presence

MODES = ("observe", "privacy", "boss")
LABELS = {"observe": "Observe only", "privacy": "Privacy lock", "boss": "Boss Alert"}
ACTION_LABELS = {
    "lock_absence": "Absence exceeded 30 seconds",
    "lock_camera_fault": "Camera stopped reporting for over 5 seconds",
    "lock_crowd": "An additional person remained for 2 seconds",
    "boss_alert": "An additional person remained for 2 seconds",
}


class GuardError(ValueError):
    pass


class Controller:
    def __init__(self, *, camera=False, actions=None, clock=time.monotonic, camera_runner=None):
        self.camera_enabled = camera
        self.actions = actions or Actions(demo=True)
        if camera == self.actions.demo:
            raise GuardError("Camera and action settings do not match")
        self.clock, self.camera_runner = clock, camera_runner
        self.guard = Presence()
        self.lock = threading.RLock()
        self.mode = "observe"
        self.demo_people = 1
        self.events = deque(maxlen=8)
        self.last_action = None
        self.session = 0
        self.worker = None
        self.stop_event = threading.Event()
        self.camera_closing = False
        self.camera_running = False

    def allowed_modes(self):
        return [mode for mode in MODES if self.actions.demo or mode == "observe"
                or (mode == "privacy" and self.actions.allow_lock)
                or (mode == "boss" and self.actions.allow_sheet)]

    def _record(self, message):
        self.events.appendleft(message)

    def start(self, mode):
        if type(mode) is not str or mode not in MODES:
            raise GuardError("Choose an available mode")
        with self.lock:
            if mode not in self.allowed_modes():
                raise GuardError("This action was not enabled at launch")
            if self.guard.active:
                raise GuardError("Stop and disarm before changing modes")
            if self.camera_closing or self.camera_running:
                raise GuardError("Wait for the camera to finish closing")
            self.mode = mode
            self.guard = Presence()
            self.guard.arm(mode == "privacy", mode == "boss")
            self.guard.command("active", self.clock())
            self.last_action = None
            self.session += 1
            self.stop_event = threading.Event()
            self._record(LABELS[mode] + " started" + (" in simulation" if self.actions.demo else ""))
            if self.camera_enabled:
                if self.camera_runner is None:
                    self.guard.command("stop", self.clock())
                    self._record("Camera adapter unavailable. Session disarmed.")
                    raise GuardError("Camera adapter unavailable")
                session = self.session
                self.worker = threading.Thread(target=self._camera_run, args=(session, self.stop_event), daemon=True)
                self.camera_running = True
                try:
                    self.worker.start()
                except (RuntimeError, OSError):
                    self.camera_running = False
                    self.worker = None
                    self.stop_event.set()
                    self.guard.command("stop", self.clock())
                    self._record("Camera adapter unavailable. Session disarmed.")
                    raise GuardError("Camera adapter unavailable") from None
            else:
                self._pulse_locked()
            return self.snapshot()

    def _camera_run(self, session, stop):
        closed = False
        try:
            closed = self.camera_runner(self, session, stop) is True
        except Exception:
            self.camera_fault(session)
        finally:
            with self.lock:
                if session == self.session:
                    self.camera_running = False
                    self.camera_closing = not closed
                    if self.guard.active:
                        self._run(self.guard.fail("Camera unavailable", self.clock()))
                        self._record("Camera unavailable. Stop and check the local camera adapter.")

    def stop(self):
        with self.lock:
            self.guard.command("stop", self.clock())
            self.stop_event.set()
            self.camera_closing = self.camera_closing or self.camera_running
            self._record("Stopped and disarmed")
            return self.snapshot()

    def close(self, timeout=2):
        self.stop()
        worker = self.worker
        if worker is not None:
            worker.join(timeout)

    def set_demo(self, people):
        if people is not None and (type(people) is not int or people not in (0, 1, 2)):
            raise GuardError("Use zero, one, two, or a simulated camera fault")
        with self.lock:
            if self.camera_enabled:
                raise GuardError("Simulation controls are unavailable with a live camera")
            self.demo_people = people
            self._pulse_locked()
            return self.snapshot()

    def _run(self, actions):
        for action in actions:
            reason = ("Camera did not report its first frame within 5 seconds"
                      if action == "lock_camera_fault" and self.guard.last_frame is None else ACTION_LABELS[action])
            try:
                self.actions.run(action)
            except Exception:
                message = ("Workbook could not be opened. Check the workstation locally" if action == "boss_alert"
                           else "Lock request failed. Windows may still be unlocked; check locally")
                self.last_action = {"status": "failed", "reason": reason, "message": message}
                self._record(message + ".")
            else:
                message = ("Simulated: " if self.actions.demo else "Requested: ") + (
                    "synthetic Boss Alert workbook" if action == "boss_alert" else "Windows lock")
                self.last_action = {"status": "simulated" if self.actions.demo else "requested",
                                    "reason": reason, "message": message}
                self._record(message)

    def _pulse_locked(self):
        now = self.clock()
        if not self.camera_enabled and self.guard.active:
            if self.demo_people is None:
                self._run(self.guard.fail("Camera unavailable", now))
            else:
                self._run(self.guard.observe(self.demo_people, now))
        self._run(self.guard.tick(now))

    def pulse(self):
        with self.lock:
            self._pulse_locked()

    def observe(self, session, people):
        with self.lock:
            if session == self.session and self.guard.active and not self.stop_event.is_set():
                self._run(self.guard.observe(people, self.clock()))

    def camera_fault(self, session):
        with self.lock:
            if session == self.session and self.guard.active and not self.stop_event.is_set():
                self._run(self.guard.fail("Camera unavailable", self.clock()))

    def snapshot(self):
        with self.lock:
            state = self.guard.snapshot(self.clock())
            state.update({"mode": self.mode, "mode_label": LABELS[self.mode], "demo": self.actions.demo,
                          "allowed_modes": self.allowed_modes(), "demo_people": self.demo_people,
                          "camera_closing": self.camera_closing, "last_action": self.last_action,
                          "events": list(self.events)})
            return state
