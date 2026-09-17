"""Regression tests for concurrent executor access to one ADS connection."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Lock
import time
import unittest
from unittest.mock import MagicMock, patch

import pyads

from custom_components.ads_multi.coordinator import AdsPlcCoordinator


class ConnectionConcurrencyTests(unittest.TestCase):
    """Exercise the real coordinator with a connection that detects overlap."""

    @patch("homeassistant.helpers.frame.report_usage")
    def make_coordinator(self, _report_usage):
        async def create():
            hass = MagicMock()
            hass.loop = asyncio.get_running_loop()
            return AdsPlcCoordinator(
                hass,
                MagicMock(),
                "Test PLC",
                "1.2.3.4.5.6",
                [
                    {"name": "poll", "type": "BOOL"},
                    {"name": "notify", "type": "BOOL", "async_read": True},
                ],
                [],
                timedelta(seconds=10),
            )

        return asyncio.run(create())

    def run_together(self, jobs):
        barrier = Barrier(len(jobs))

        def run(job):
            barrier.wait(timeout=5)
            return job()

        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = [executor.submit(run, job) for job in jobs]
            return [future.result(timeout=10) for future in futures]

    def test_writes_polling_and_notification_operations_do_not_overlap(self):
        coordinator = self.make_coordinator()
        plc = coordinator.plc
        guard = Lock()
        overlaps = []
        calls = []

        def operation(kind, result=None):
            def execute(*args):
                acquired = guard.acquire(blocking=False)
                if not acquired:
                    overlaps.append(kind)
                    return result
                try:
                    calls.append((kind, args))
                    time.sleep(0.01)
                    return result
                finally:
                    guard.release()

            return execute

        plc.read_by_name.side_effect = operation("read", True)
        plc.write_by_name.side_effect = operation("write")
        plc.add_device_notification.side_effect = operation("subscribe", (1, 2))
        plc.del_device_notification.side_effect = operation("unsubscribe")
        plc.close.side_effect = operation("close")
        plc.notification.side_effect = lambda _: lambda callback: callback

        for value in (True, False):
            self.run_together(
                [
                    *(
                        lambda i=i: coordinator.write_variable(
                            f"light{i}", "BOOL", value
                        )
                        for i in range(4)
                    ),
                    coordinator._read_polled_variables,
                    coordinator._setup_notifications,
                ]
            )
            self.run_together(
                [
                    coordinator._release_notifications,
                    lambda: coordinator.write_variable("light0", "BOOL", value),
                    coordinator._read_polled_variables,
                ]
            )
        self.run_together(
            [
                coordinator.close_connection,
                lambda: coordinator.write_variable("light0", "BOOL", False),
                coordinator._read_polled_variables,
            ]
        )
        self.assertEqual(overlaps, [])
        self.assertEqual(sum(kind == "write" for kind, _ in calls), 11)
        self.assertEqual(sum(kind == "subscribe" for kind, _ in calls), 2)
        self.assertEqual(sum(kind == "unsubscribe" for kind, _ in calls), 2)
        self.assertEqual(sum(kind == "close" for kind, _ in calls), 1)

    def test_write_exception_releases_lock(self):
        coordinator = self.make_coordinator()
        coordinator.plc.write_by_name.side_effect = [pyads.ADSError(1), None]
        with self.assertRaises(pyads.ADSError):
            coordinator.write_variable("light", "BOOL", True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(coordinator.write_variable, "light", "BOOL", False).result(
                5
            )
        self.assertEqual(coordinator.plc.write_by_name.call_count, 2)

    def test_different_connections_can_operate_concurrently(self):
        first = self.make_coordinator()
        second = self.make_coordinator()
        inside_operation = Barrier(2)
        for coordinator in (first, second):
            coordinator.plc.write_by_name.side_effect = lambda *_: (
                inside_operation.wait(timeout=5)
            )
        self.run_together(
            [
                lambda: first.write_variable("light", "BOOL", True),
                lambda: second.write_variable("light", "BOOL", True),
            ]
        )
