import unittest

from monitor.performance_monitor import PerformanceMonitor


class _StatsProvider:
    def get_stats(self):
        return {}


class MonitorAlertTests(unittest.TestCase):
    def setUp(self):
        self.monitor = PerformanceMonitor(_StatsProvider(), _StatsProvider())

    def test_alert_is_deduplicated_while_threshold_is_breached(self):
        self.monitor._check_threshold("agent_success_rate", 0.5, "billing")
        self.monitor._check_threshold("agent_success_rate", 0.4, "billing")

        self.assertEqual(len(self.monitor._alerts), 1)
        self.assertFalse(self.monitor._alerts[0].resolved)

    def test_alert_is_resolved_after_metric_recovers(self):
        self.monitor._check_threshold("agent_success_rate", 0.5, "billing")
        self.monitor._check_threshold("agent_success_rate", 0.95, "billing")

        alert = self.monitor._alerts[0]
        self.assertTrue(alert.resolved)
        self.assertIsNotNone(alert.resolved_at)
        self.assertEqual(self.monitor.summary()["active_alerts"], [])


if __name__ == "__main__":
    unittest.main()
