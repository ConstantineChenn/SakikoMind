import unittest

from api.main import _metric_path, _normalize_trace_id


class ApiTracingTests(unittest.TestCase):
    def test_keeps_safe_client_trace_id(self):
        self.assertEqual(_normalize_trace_id("demo-trace:001"), "demo-trace:001")

    def test_removes_unsafe_trace_characters(self):
        self.assertEqual(_normalize_trace_id(" demo / trace 中文 001 "), "demotrace001")

    def test_generates_trace_id_when_missing(self):
        trace_id = _normalize_trace_id(None)

        self.assertTrue(trace_id.startswith("trc-"))
        self.assertEqual(len(trace_id), 20)

    def test_uses_route_template_for_metric_path(self):
        request = type(
            "RequestStub",
            (),
            {
                "scope": {"route": type("RouteStub", (), {"path": "/handoffs/{ticket_id}"})()},
                "url": type("UrlStub", (), {"path": "/handoffs/EM-sensitive"})(),
            },
        )()

        self.assertEqual(_metric_path(request), "/handoffs/{ticket_id}")


if __name__ == "__main__":
    unittest.main()
