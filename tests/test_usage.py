from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from eaaf import usage
from eaaf.policies import Application, ApplicationPolicy


def policy(quota, budget):
    return Application("hr", ApplicationPolicy(
        domain="HR", cost_center="DEMO-HR", key_env="TEST_KEY",
        allowed_models=["eaaf-mock"], requests_per_day=quota,
        monthly_budget_cents=budget, request_cost_cents=2,
    ))


def test_parallel_reservations_do_not_overspend(tmp_path):
    store = usage.UsageStore(tmp_path / "usage.sqlite3")
    store.initialize()
    application = policy(quota=20, budget=4)

    def reserve(_):
        return store.reserve(application, "eaaf-mock")[1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(reserve, range(12)))
    assert decisions.count("admitted") == 2
    assert decisions.count("budget_exceeded") == 10
    assert sum(row["simulated_cost_cents"] for row in store.report()) == 4


def test_daily_and_monthly_utc_rollover(tmp_path, monkeypatch):
    class TestClock(datetime):
        current = datetime(2026, 12, 29, 23, 59, tzinfo=UTC)

        @classmethod
        def now(cls, tz):
            return cls.current

    monkeypatch.setattr(usage, "datetime", TestClock)
    store = usage.UsageStore(tmp_path / "usage.sqlite3")
    store.initialize()
    application = policy(quota=1, budget=4)

    def reserve():
        return store.reserve(application, "eaaf-mock")[1]

    assert reserve() == "admitted"
    assert reserve() == "quota_exceeded"
    TestClock.current = datetime(2026, 12, 30, tzinfo=UTC)
    assert reserve() == "admitted"
    TestClock.current = datetime(2026, 12, 31, tzinfo=UTC)
    assert reserve() == "budget_exceeded"
    TestClock.current = datetime(2027, 1, 1, tzinfo=UTC)
    assert reserve() == "admitted"
