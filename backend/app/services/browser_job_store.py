"""PostgreSQL result journal and completion-notification outbox.

No payload or credentials are stored: only the immutable request fingerprint,
outcome and a lease for detecting interrupted execution.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.models.browser_job import BrowserJobResult

FINAL_STATES = ("succeeded", "failed", "uncertain")
KNOWN_STATES = ("succeeded", "failed")
JOB_STATES = ("running", "reconciling", *FINAL_STATES)


class BrowserJobStore:
    def __init__(self, factory):
        self.factory = factory

    async def get(self, namespace: str, job_id: str) -> BrowserJobResult | None:
        async with self.factory() as session:
            return await session.get(BrowserJobResult, (namespace, job_id))

    async def accept(
        self, namespace: str, job_id: str, admin_id: int | None, fingerprint: str,
        worker_id: str | None = None, lease_seconds: float | None = None,
    ) -> tuple[BrowserJobResult, bool]:
        """Insert once before execution. A duplicate id must have the same input."""
        existing = await self.get(namespace, job_id)
        if existing is not None:
            self._same_request(existing, admin_id, fingerprint)
            return existing, False
        now = datetime.now(UTC)
        row = BrowserJobResult(
            namespace=namespace, job_id=job_id, admin_id=admin_id,
            fingerprint=fingerprint, state="running", data={}, worker_id=worker_id,
            lease_expires_at=(now + timedelta(seconds=lease_seconds) if lease_seconds else now),
            started_at=now, created_at=now, updated_at=now,
        )
        try:
            async with self.factory() as session, session.begin():
                session.add(row)
                await session.flush()
                session.expunge(row)
            return row, True
        except IntegrityError:
            # Another worker can receive a retry before our POST response is
            # delivered. The database uniqueness constraint decides ownership.
            existing = await self.get(namespace, job_id)
            if existing is None:
                raise
            self._same_request(existing, admin_id, fingerprint)
            return existing, False

    @staticmethod
    def _same_request(row: BrowserJobResult, admin_id: int | None, fingerprint: str) -> None:
        if row.admin_id != admin_id or row.fingerprint != fingerprint:
            raise ValueError("Job id was already used with a different request")

    async def finish(
        self, namespace: str, job_id: str, state: str,
        data: dict | None = None, error: str | None = None,
        *, require_expired_lease: bool = False,
    ) -> BrowserJobResult | None:
        if state not in JOB_STATES:
            raise ValueError("Unsupported browser job state")
        async with self.factory() as session, session.begin():
            row = await session.get(BrowserJobResult, (namespace, job_id), with_for_update=True)
            if row is None:
                return None
            # A timeout/restart observer must never overwrite a verified final
            # outcome written by the real executor.
            if row.state in KNOWN_STATES:
                session.expunge(row)
                return row
            now = datetime.now(UTC)
            if require_expired_lease and row.state == "running":
                lease = row.lease_expires_at
                if lease is not None:
                    lease = lease.replace(tzinfo=UTC) if lease.tzinfo is None else lease
                    if lease > now:
                        session.expunge(row)
                        return row
            changed = row.state != state or row.data != (data or {}) or row.error != error
            row.state, row.data, row.error = state, data or {}, error
            row.updated_at = now
            row.completed_at = now if state in FINAL_STATES else None
            if state != "running":
                row.lease_expires_at = None
            if namespace == "xhs" and state in FINAL_STATES and changed:
                row.callback_attempts = 0
                row.next_callback_at = now
                row.callback_delivered_at = None
            await session.flush()
            session.expunge(row)
            return row

    async def renew(
        self, namespace: str, job_id: str, worker_id: str, lease_seconds: float,
    ) -> None:
        async with self.factory() as session, session.begin():
            await session.execute(
                update(BrowserJobResult)
                .where(
                    BrowserJobResult.namespace == namespace,
                    BrowserJobResult.job_id == job_id,
                    BrowserJobResult.worker_id == worker_id,
                    BrowserJobResult.state == "running",
                )
                .values(lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds))
            )

    async def recovery_jobs(self, namespace: str, limit: int = 100) -> list[BrowserJobResult]:
        now = datetime.now(UTC)
        expired_running = and_(
            BrowserJobResult.state == "running",
            or_(BrowserJobResult.lease_expires_at <= now,
                BrowserJobResult.lease_expires_at.is_(None)),
        )
        recoverable = (
            or_(expired_running, BrowserJobResult.state.in_(("reconciling", "uncertain")))
            if namespace == "xhs" else expired_running
        )
        async with self.factory() as session, session.begin():
            rows = list(await session.scalars(
                select(BrowserJobResult).where(
                    BrowserJobResult.namespace == namespace, recoverable,
                    or_(
                        BrowserJobResult.next_recovery_at.is_(None),
                        BrowserJobResult.next_recovery_at <= now,
                    ),
                ).order_by(
                    BrowserJobResult.next_recovery_at.asc().nullsfirst(),
                    BrowserJobResult.updated_at, BrowserJobResult.job_id,
                ).limit(limit).with_for_update(skip_locked=True)
            ))
            # Reserving a short check window makes progress fair across old
            # unresolved jobs even while the downstream worker is unavailable.
            # It also keeps multiple XHS replicas from polling the same batch.
            for row in rows:
                row.next_recovery_at = now + timedelta(seconds=30)
            await session.flush()
            for row in rows:
                session.expunge(row)
            return rows

    async def callback_jobs(self, limit: int = 100) -> list[BrowserJobResult]:
        async with self.factory() as session:
            return list(await session.scalars(
                select(BrowserJobResult).where(
                    BrowserJobResult.namespace == "xhs",
                    BrowserJobResult.state.in_(FINAL_STATES),
                    BrowserJobResult.callback_delivered_at.is_(None),
                    BrowserJobResult.next_callback_at <= datetime.now(UTC),
                ).order_by(BrowserJobResult.next_callback_at, BrowserJobResult.job_id).limit(limit)
            ))

    async def mark_callback(
        self, job_id: str, delivered: bool, *, state: str | None = None,
    ) -> None:
        async with self.factory() as session, session.begin():
            row = await session.get(BrowserJobResult, ("xhs", job_id), with_for_update=True)
            if row is None or row.state not in FINAL_STATES or (state and row.state != state):
                return
            now = datetime.now(UTC)
            row.callback_attempts += 1
            if delivered:
                row.callback_delivered_at = now
                row.next_callback_at = None
            else:
                row.next_callback_at = now + timedelta(
                    seconds=min(300, 2 ** min(row.callback_attempts, 8))
                )
