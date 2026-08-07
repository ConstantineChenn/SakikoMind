import unittest

from api.main import _update_profile_safely


class _MemoryManagerStub:
    def __init__(self, failures_before_success: int):
        self.failures_before_success = failures_before_success
        self.calls = 0

    async def update_profile(self, user_id: str, conv_id: str) -> None:
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise RuntimeError("temporary storage failure")


class ProfileUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_then_succeeds_without_raising(self):
        manager = _MemoryManagerStub(failures_before_success=1)

        updated = await _update_profile_safely(
            manager, "user-1", "conv-1", "trace-1", retry_delay_s=0,
        )

        self.assertTrue(updated)
        self.assertEqual(manager.calls, 2)

    async def test_returns_false_after_retry_budget_is_used(self):
        manager = _MemoryManagerStub(failures_before_success=3)

        updated = await _update_profile_safely(
            manager, "user-1", "conv-1", "trace-1", retry_delay_s=0,
        )

        self.assertFalse(updated)
        self.assertEqual(manager.calls, 2)


if __name__ == "__main__":
    unittest.main()
