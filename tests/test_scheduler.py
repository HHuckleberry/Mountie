import os
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5 import QtCore

from mountie.scheduler import DisconnectScheduler


class DisconnectSchedulerTests(unittest.TestCase):
    def test_zero_disables_timer(self):
        scheduler = DisconnectScheduler()
        scheduler.schedule("share", 0)
        self.assertIsNone(scheduler.remaining_seconds("share"))

    def test_reschedule_replaces_existing_timer(self):
        scheduler = DisconnectScheduler()
        scheduler.schedule("share", 30)
        first = scheduler._timers["share"]
        scheduler.schedule("share", 60)
        self.assertIsNot(first, scheduler._timers["share"])
        self.assertFalse(first.isActive())

    def test_due_signal_identifies_share(self):
        scheduler = DisconnectScheduler()
        received = []
        scheduler.due.connect(received.append)
        timer = mock.Mock()
        scheduler._timers["share"] = timer
        scheduler._fire("share")
        self.assertEqual(received, ["share"])
        self.assertNotIn("share", scheduler._timers)


if __name__ == "__main__":
    unittest.main()
