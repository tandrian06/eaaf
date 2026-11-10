import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from eaaf.policies import Application


DEFAULT_DB = Path("usage.sqlite3")


class UsageStore:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    application TEXT,
                    domain TEXT,
                    cost_center TEXT,
                    model TEXT,
                    decision TEXT NOT NULL,
                    cost_cents INTEGER NOT NULL DEFAULT 0,
                    status_code INTEGER,
                    total_tokens INTEGER
                );
                CREATE INDEX IF NOT EXISTS events_application_time
                    ON events (application, occurred_at) WHERE cost_cents > 0;
            """)

    @staticmethod
    def _insert(db, now, application, model, decision, cost_cents, status_code) -> int:
        cursor = db.execute(
            """INSERT INTO events
               (occurred_at, application, domain, cost_center, model, decision, cost_cents, status_code)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now.isoformat(),
                application.name if application else None,
                application.policy.domain if application else None,
                application.policy.cost_center if application else None,
                model, decision, cost_cents, status_code,
            ),
        )
        return cursor.lastrowid

    def record_denial(self, decision: str, status_code: int, application: Application | None = None) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            self._insert(db, datetime.now(UTC), application, None, decision, 0, status_code)

    def reserve(self, application: Application, model: str) -> tuple[int, str]:
        policy = application.policy
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            now = datetime.now(UTC)
            day = now.date().isoformat()
            month = day[:7]
            next_month = f"{now.year + (now.month == 12):04d}-{now.month % 12 + 1:02d}"
            today_requests, monthly_spend = db.execute(
                """SELECT COUNT(CASE WHEN substr(occurred_at, 1, 10) = ? THEN 1 END),
                          COALESCE(SUM(cost_cents), 0)
                   FROM events
                   WHERE application = ? AND occurred_at >= ? AND occurred_at < ? AND cost_cents > 0""",
                (day, application.name, month, next_month),
            ).fetchone()
            if today_requests >= policy.requests_per_day:
                decision, cost_cents, status_code = "quota_exceeded", 0, 429
            elif monthly_spend + policy.request_cost_cents > policy.monthly_budget_cents:
                decision, cost_cents, status_code = "budget_exceeded", 0, 429
            else:
                decision, cost_cents, status_code = "admitted", policy.request_cost_cents, None
            event_id = self._insert(db, now, application, model, decision, cost_cents, status_code)
            return event_id, decision

    def finish(self, event_id: int, status_code: int, total_tokens: int | None = None) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            decision = {200: "completed", 404: "knowledge_no_match"}.get(status_code, "upstream_failure")
            db.execute(
                """UPDATE events SET decision = ?, status_code = ?, total_tokens = ?
                   WHERE id = ? AND decision = 'admitted'""",
                (decision, status_code, total_tokens, event_id),
            )

    def report(self) -> list[dict]:
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""
                SELECT substr(occurred_at, 1, 7) AS month_utc, application, domain, cost_center,
                       decision, COUNT(*) AS requests, SUM(cost_cents) AS simulated_cost_cents,
                       SUM(total_tokens) AS reported_total_tokens
                FROM events
                GROUP BY month_utc, application, domain, cost_center, decision
                ORDER BY month_utc, application, domain, cost_center, decision
            """)
            return [dict(row) for row in rows]


def usage_store() -> UsageStore:
    return UsageStore(Path(os.environ.get("EAAF_USAGE_DB", DEFAULT_DB)))


if __name__ == "__main__":
    print(json.dumps(usage_store().report(), indent=2))
