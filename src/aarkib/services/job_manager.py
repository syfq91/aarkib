from __future__ import annotations

import inspect
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select, update

from aarkib.extensions import db
from aarkib.models.job import JobRecord

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    """Execution status of an asynchronous background job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    COMPLETED = "succeeded"

    @classmethod
    def _missing_(cls, value: object) -> JobStatus | None:
        if isinstance(value, str) and value.lower() in (
            "completed",
            "complete",
            "done",
        ):
            return cls.SUCCEEDED
        return None

    def __eq__(self, other: object) -> bool:
        if (
            self is JobStatus.SUCCEEDED
            and isinstance(other, str)
            and other.lower()
            in (
                "completed",
                "complete",
                "done",
            )
        ):
            return True
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()


@dataclass
class Job:
    """Represents a background job tracked by JobManager."""

    id: str
    job_type: str
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0  # 0.0 to 100.0
    progress_message: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    retry_count: int = 0
    cancel_requested: bool = False
    _future: Future | None = None
    _cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def is_cancelled(self) -> bool:
        return (
            self.status == JobStatus.CANCELLED
            or self.cancel_requested
            or self._cancel_event.is_set()
        )

    @property
    def completed_at(self) -> datetime | None:
        return self.finished_at

    def to_dict(self) -> dict[str, Any]:
        """Serializes the job to a JSON-compatible dictionary."""
        elapsed = None
        if self.started_at:
            end_time = self.finished_at or datetime.now(UTC)
            start = self.started_at
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=UTC)
            elapsed = round((end_time - start).total_seconds(), 2)

        created_iso = self.created_at.isoformat() if self.created_at else None
        started_iso = self.started_at.isoformat() if self.started_at else None
        finished_iso = self.finished_at.isoformat() if self.finished_at else None

        return {
            "id": self.id,
            "job_type": self.job_type,
            "status": self.status.value,
            "progress": round(self.progress, 1),
            "progress_message": self.progress_message,
            "created_at": created_iso,
            "started_at": started_iso,
            "finished_at": finished_iso,
            "completed_at": finished_iso,
            "elapsed_seconds": elapsed,
            "result": self.result,
            "error": self.error,
            "retry_count": self.retry_count,
            "cancel_requested": self.cancel_requested,
        }


class JobManager:
    """In-process thread-pool supervisor for non-blocking media tasks with durable history."""

    def __init__(self, max_workers: int = 2, max_history: int = 100) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="aarkib-worker"
        )
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_history = max_history

    def _persist_job_created(self, job: Job, app: Flask | None) -> None:
        if app is None:
            return
        try:
            with app.app_context():
                lib_id = getattr(job, "_target_library_id", None)
                if isinstance(lib_id, str) and lib_id.isdigit():
                    lib_id = int(lib_id)
                elif not isinstance(lib_id, int):
                    lib_id = None
                rec = JobRecord(
                    id=job.id,
                    job_type=job.job_type,
                    library_id=lib_id,
                    status=job.status.value,
                    progress=job.progress,
                    progress_message=job.progress_message,
                    created_at=job.created_at,
                    retry_count=job.retry_count,
                    cancel_requested=job.cancel_requested,
                )
                db.session.add(rec)
                db.session.commit()
        except Exception as e:
            logger.debug("Failed to persist job creation for %s: %s", job.id, e)

    def _persist_job_started(self, job: Job, app: Flask | None) -> None:
        if app is None:
            return
        try:
            with app.app_context():
                rec = db.session.get(JobRecord, job.id)
                if rec:
                    rec.status = JobStatus.RUNNING.value
                    rec.started_at = job.started_at
                    rec.progress = job.progress
                    rec.progress_message = job.progress_message
                    rec.retry_count = job.retry_count
                    rec.cancel_requested = job.cancel_requested
                    db.session.commit()
        except Exception as e:
            logger.debug("Failed to persist job start for %s: %s", job.id, e)

    def _persist_job_progress(self, job: Job, app: Flask | None) -> None:
        """Persist job progress using an isolated DB connection."""
        if app is None:
            return
        try:
            with db.engine.begin() as conn:
                conn.execute(
                    update(JobRecord)
                    .where(
                        JobRecord.id == job.id,
                        JobRecord.status == JobStatus.RUNNING.value,
                    )
                    .values(
                        progress=job.progress,
                        progress_message=job.progress_message,
                    )
                )
        except Exception as e:
            logger.debug("Failed to persist job progress for %s: %s", job.id, e)

    def _persist_job_completed(self, job: Job, app: Flask | None) -> None:
        if app is None:
            return
        try:
            payload = None
            if job.result is not None:
                try:
                    payload = json.dumps(job.result, default=str)
                except Exception:
                    payload = str(job.result)
            with app.app_context():
                rec = db.session.get(JobRecord, job.id)
                if rec:
                    rec.status = JobStatus.SUCCEEDED.value
                    rec.progress = 100.0
                    rec.finished_at = job.finished_at
                    rec.progress_message = job.progress_message
                    rec.result_payload = payload
                    rec.retry_count = job.retry_count
                    rec.cancel_requested = job.cancel_requested
                    db.session.commit()
        except Exception as e:
            logger.debug("Failed to persist job completion for %s: %s", job.id, e)

    def _persist_job_failed(self, job: Job, app: Flask | None) -> None:
        if app is None:
            return
        try:
            with app.app_context():
                rec = db.session.get(JobRecord, job.id)
                if rec:
                    rec.status = JobStatus.FAILED.value
                    rec.finished_at = job.finished_at
                    rec.progress_message = job.progress_message
                    rec.error_message = job.error
                    rec.retry_count = job.retry_count
                    rec.cancel_requested = job.cancel_requested
                    db.session.commit()
        except Exception as e:
            logger.debug("Failed to persist job failure for %s: %s", job.id, e)

    def _persist_job_cancelled(self, job: Job, app: Flask | None) -> None:
        target_app = app
        if target_app is None:
            try:
                from flask import current_app, has_app_context

                if has_app_context():
                    target_app = current_app
            except Exception:
                target_app = None
        if target_app is None:
            return
        try:
            with target_app.app_context():
                rec = db.session.get(JobRecord, job.id)
                if rec:
                    rec.status = JobStatus.CANCELLED.value
                    rec.cancel_requested = True
                    rec.finished_at = job.finished_at or datetime.now(UTC)
                    rec.progress_message = job.progress_message
                    rec.retry_count = job.retry_count
                    db.session.commit()
        except Exception as e:
            logger.debug("Failed to persist job cancellation for %s: %s", job.id, e)

    def _dispatch_worker(
        self,
        job: Job,
        fn: Callable[..., Any],
        app: Flask | None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        last_sync_time = [time.time()]
        last_sync_progress = [-1.0]

        def progress_callback(percentage: float, message: str = "") -> None:
            val = max(0.0, min(100.0, float(percentage)))
            with self._lock:
                job.progress = val
                if message:
                    job.progress_message = str(message)

            now_ts = time.time()
            if app is not None and (
                now_ts - last_sync_time[0] >= 2.0
                or abs(val - last_sync_progress[0]) >= 10.0
            ):
                last_sync_time[0] = now_ts
                last_sync_progress[0] = val
                self._persist_job_progress(job, app)

        worker_kwargs = dict(kwargs)
        worker_kwargs["progress_callback"] = progress_callback

        accepts_cancel = False
        try:
            sig = inspect.signature(fn)
            for param in sig.parameters.values():
                if (
                    param.kind == inspect.Parameter.VAR_KEYWORD
                    or param.name == "cancel_event"
                ):
                    accepts_cancel = True
                    break
        except ValueError, TypeError:
            accepts_cancel = True

        if accepts_cancel:
            worker_kwargs["cancel_event"] = job._cancel_event

        pass_app = False
        if app is not None:
            try:
                sig = inspect.signature(fn)
                params = list(sig.parameters.values())
                if params and (
                    params[0].name in ("app", "flask_app")
                    or params[0].kind == inspect.Parameter.VAR_POSITIONAL
                ):
                    pass_app = True
            except ValueError, TypeError:
                pass_app = True

        def _worker() -> None:
            with self._lock:
                if job._cancel_event.is_set():
                    job.status = JobStatus.CANCELLED
                    job.cancel_requested = True
                    job.finished_at = datetime.now(UTC)
                    job.progress_message = "Job cancelled before execution"
                    self._persist_job_cancelled(job, app)
                    return
                job.status = JobStatus.RUNNING
                job.started_at = datetime.now(UTC)
                job.progress = 0.0
                job.progress_message = "Starting task..."
            self._persist_job_started(job, app)

            try:
                if app is not None:
                    with app.app_context():
                        if pass_app:
                            res = fn(app, *args, **worker_kwargs)
                        else:
                            res = fn(*args, **worker_kwargs)
                else:
                    res = fn(*args, **worker_kwargs)

                with self._lock:
                    if job._cancel_event.is_set():
                        job.status = JobStatus.CANCELLED
                        job.cancel_requested = True
                        job.finished_at = datetime.now(UTC)
                        job.progress_message = "Job cancelled by user"
                        job.result = res if isinstance(res, dict) else {"result": res}
                        self._persist_job_cancelled(job, app)
                        return
                    job.status = JobStatus.SUCCEEDED
                    job.progress = 100.0
                    job.finished_at = datetime.now(UTC)
                    job.progress_message = "Completed"
                    job.result = res if isinstance(res, dict) else {"result": res}
                self._persist_job_completed(job, app)
            except Exception as e:
                with self._lock:
                    if job._cancel_event.is_set():
                        job.status = JobStatus.CANCELLED
                        job.cancel_requested = True
                        job.finished_at = datetime.now(UTC)
                        job.progress_message = "Job cancelled"
                        self._persist_job_cancelled(job, app)
                        return
                logger.error(
                    "Job %s (%s) failed: %s", job.id, job.job_type, e, exc_info=True
                )
                with self._lock:
                    job.status = JobStatus.FAILED
                    job.finished_at = datetime.now(UTC)
                    job.progress_message = f"Failed: {e}"
                    job.error = str(e)
                self._persist_job_failed(job, app)
                try:
                    from aarkib.services.events import EVENT_JOB_FAILED, event_bus

                    event_bus.emit(
                        EVENT_JOB_FAILED,
                        {
                            "job_id": job.id,
                            "job_type": job.job_type,
                            "error": str(e),
                        },
                    )
                except Exception:
                    pass

        job._future = self._executor.submit(_worker)

    def submit_job(
        self,
        job_type: str,
        fn: Callable[..., Any],
        app: Flask | None = None,
        *args: Any,
        deduplicate: bool = True,
        **kwargs: Any,
    ) -> Job:
        """Submits a job for asynchronous execution inside a Flask application context if provided.

        If an identical task is already RUNNING or QUEUED and deduplicate=True, returns the existing
        active job to prevent race conditions or duplicate disk I/O.
        """
        with self._lock:
            if deduplicate:
                req_lib_id = kwargs.get("library_id")
                for existing in self._jobs.values():
                    if existing.job_type == job_type and existing.status in (
                        JobStatus.QUEUED,
                        JobStatus.RUNNING,
                    ):
                        job_lib = getattr(existing, "_target_library_id", None)
                        if req_lib_id == job_lib:
                            return existing

            self._prune_history_locked()
            job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            job = Job(id=job_id, job_type=job_type)
            job._target_library_id = kwargs.get("library_id")
            job._target_fn = fn
            job._target_args = args
            job._target_kwargs = kwargs.copy()
            job._target_app = app
            self._jobs[job_id] = job

        # Persist initial record in database if app is provided
        self._persist_job_created(job, app)

        self._dispatch_worker(job, fn, app, *args, **kwargs)
        return job

    def get_job(self, job_id: str, app: Flask | None = None) -> Job | None:
        """Retrieves a job by its unique identifier, checking memory first and falling back to database."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return job

        target_app = app
        if target_app is None:
            try:
                from flask import current_app, has_app_context

                if has_app_context():
                    target_app = current_app
            except Exception:
                target_app = None

        if target_app is not None:
            try:
                with target_app.app_context():
                    rec = db.session.get(JobRecord, job_id)
                    if rec is not None:
                        result_data = None
                        if rec.result_payload:
                            try:
                                result_data = json.loads(rec.result_payload)
                            except Exception:
                                result_data = {"raw": rec.result_payload}
                        try:
                            status = JobStatus(rec.status)
                        except ValueError:
                            status = JobStatus.FAILED

                        created_at = rec.created_at
                        if created_at and created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=UTC)
                        started_at = rec.started_at
                        if started_at and started_at.tzinfo is None:
                            started_at = started_at.replace(tzinfo=UTC)
                        finished_at = rec.finished_at
                        if finished_at and finished_at.tzinfo is None:
                            finished_at = finished_at.replace(tzinfo=UTC)

                        restored = Job(
                            id=rec.id,
                            job_type=rec.job_type,
                            status=status,
                            progress=rec.progress,
                            progress_message=rec.progress_message or "",
                            created_at=created_at,
                            started_at=started_at,
                            finished_at=finished_at,
                            result=result_data,
                            error=rec.error_message,
                            retry_count=getattr(rec, "retry_count", 0),
                            cancel_requested=getattr(rec, "cancel_requested", False),
                        )
                        restored._target_library_id = rec.library_id
                        return restored
            except Exception as e:
                logger.debug("Database get_job lookup error for %s: %s", job_id, e)

        return None

    def request_cancel(self, job_id: str, app: Flask | None = None) -> bool:
        """Requests cooperative cancellation for a job if it is queued or running.

        Sets cancel_requested = True, triggers _cancel_event, cancels pending Future
        if still queued, and persists status to the database.
        Returns True if cancellation was successfully initiated, False otherwise.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                if job.status in (
                    JobStatus.SUCCEEDED,
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                    JobStatus.INTERRUPTED,
                ):
                    return False

                job.cancel_requested = True
                job._cancel_event.set()
                if job._future and not job._future.running():
                    job._future.cancel()
                    job.status = JobStatus.CANCELLED
                    job.finished_at = datetime.now(UTC)
                    job.progress_message = "Job cancelled before execution"
                else:
                    job.status = JobStatus.CANCELLED
                    job.finished_at = datetime.now(UTC)
                    job.progress_message = "Cancellation requested"

                self._persist_job_cancelled(job, app)
                return True

        target_app = app
        if target_app is None:
            try:
                from flask import current_app, has_app_context

                if has_app_context():
                    target_app = current_app
            except Exception:
                target_app = None

        if target_app is not None:
            try:
                with target_app.app_context():
                    rec = db.session.get(JobRecord, job_id)
                    if rec and rec.status in (
                        JobStatus.QUEUED.value,
                        JobStatus.RUNNING.value,
                    ):
                        rec.status = JobStatus.CANCELLED.value
                        rec.cancel_requested = True
                        rec.finished_at = datetime.now(UTC)
                        rec.progress_message = "Job cancelled"
                        db.session.commit()
                        return True
            except Exception as e:
                logger.debug("Database request_cancel error for %s: %s", job_id, e)

        return False

    def cancel_job(
        self, job_id: str, app: Flask | None = None
    ) -> dict[str, Any] | None:
        """Signals cancellation for a job if it is queued or running."""
        job = self.get_job(job_id, app=app)
        if not job:
            return None

        if job.status in (
            JobStatus.SUCCEEDED,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
        ):
            return {
                "id": job.id,
                "status": job.status.value,
                "cancelled": False,
                "message": f"Job is already {job.status.value}",
            }

        success = self.request_cancel(job_id, app=app)
        return {
            "id": job.id,
            "status": JobStatus.CANCELLED.value if success else job.status.value,
            "cancelled": success,
            "message": "Job cancelled successfully"
            if success
            else "Failed to cancel job",
        }

    def retry_job(self, job_id: str, app: Flask | None = None) -> Job | None:
        """Retries a failed or cancelled background job.

        Resets cancellation, clears errors, increments retry_count, and re-enqueues execution.
        Returns the retried Job, or None if the job cannot be retried.
        """
        target_app = app
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                job = self.get_job(job_id, app=target_app)
                if not job:
                    return None
                self._jobs[job.id] = job

            if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                return job

            fn = getattr(job, "_target_fn", None)
            args = getattr(job, "_target_args", ())
            kwargs = getattr(job, "_target_kwargs", {}).copy()

            if fn is None:
                if job.job_type in ("library_scan", "scan_library"):
                    from aarkib.services.scanner import scan_library

                    fn = scan_library
                    lib_id = getattr(job, "_target_library_id", None)
                    if lib_id:
                        kwargs["library_id"] = lib_id
                elif job.job_type == "batch_enrich":
                    from aarkib.services.enricher import run_enrichment

                    fn = run_enrichment
                elif job.job_type in ("scheduled_backup", "backup"):
                    from aarkib.services.backup import create_backup

                    fn = create_backup

            if fn is None:
                logger.warning("Cannot retry job %s: target function unknown", job_id)
                return None

            if target_app is None:
                target_app = getattr(job, "_target_app", None)

            job.retry_count += 1
            job.cancel_requested = False
            job._cancel_event = threading.Event()
            job.status = JobStatus.QUEUED
            job.progress = 0.0
            job.progress_message = f"Retry #{job.retry_count} queued"
            job.result = None
            job.error = None
            job.started_at = None
            job.finished_at = None

        if target_app is not None:
            try:
                with target_app.app_context():
                    rec = db.session.get(JobRecord, job.id)
                    if rec:
                        rec.status = JobStatus.QUEUED.value
                        rec.retry_count = job.retry_count
                        rec.cancel_requested = False
                        rec.progress = 0.0
                        rec.progress_message = job.progress_message
                        rec.error_message = None
                        rec.result_payload = None
                        rec.started_at = None
                        rec.finished_at = None
                        db.session.commit()
            except Exception as e:
                logger.debug("Failed to persist job retry for %s: %s", job.id, e)

        self._dispatch_worker(job, fn, target_app, *args, **kwargs)
        return job

    def list_jobs(
        self,
        limit: int = 20,
        status: str | JobStatus | None = None,
        app: Flask | None = None,
    ) -> list[Job]:
        """Returns the most recent jobs ordered newest first, overlaying in-memory state on persisted history."""
        target_app = app
        if target_app is None:
            try:
                from flask import current_app, has_app_context

                if has_app_context():
                    target_app = current_app
            except Exception:
                target_app = None

        norm_status = None
        if status is not None:
            if isinstance(status, JobStatus):
                norm_status = status.value
            else:
                s_str = str(status).strip().lower()
                if s_str in ("completed", "succeeded"):
                    norm_status = "succeeded"
                else:
                    norm_status = s_str

        if target_app is not None:
            try:
                with target_app.app_context():
                    stmt = select(JobRecord).order_by(JobRecord.created_at.desc())
                    if norm_status == "succeeded":
                        stmt = stmt.where(
                            JobRecord.status.in_(["succeeded", "completed"])
                        )
                    elif norm_status:
                        stmt = stmt.where(JobRecord.status == norm_status)
                    stmt = stmt.limit(limit)

                    records = db.session.scalars(stmt).all()
                    if records:
                        results: list[Job] = []
                        with self._lock:
                            for rec in records:
                                if rec.id in self._jobs:
                                    mem_job = self._jobs[rec.id]
                                    if (
                                        norm_status is None
                                        or mem_job.status == norm_status
                                    ):
                                        results.append(mem_job)
                                else:
                                    result_data = None
                                    if rec.result_payload:
                                        try:
                                            result_data = json.loads(rec.result_payload)
                                        except Exception:
                                            result_data = {"raw": rec.result_payload}
                                    try:
                                        rec_status = JobStatus(rec.status)
                                    except ValueError:
                                        rec_status = JobStatus.FAILED

                                    created_at = rec.created_at
                                    if created_at and created_at.tzinfo is None:
                                        created_at = created_at.replace(tzinfo=UTC)
                                    started_at = rec.started_at
                                    if started_at and started_at.tzinfo is None:
                                        started_at = started_at.replace(tzinfo=UTC)
                                    finished_at = rec.finished_at
                                    if finished_at and finished_at.tzinfo is None:
                                        finished_at = finished_at.replace(tzinfo=UTC)

                                    restored = Job(
                                        id=rec.id,
                                        job_type=rec.job_type,
                                        status=rec_status,
                                        progress=rec.progress,
                                        progress_message=rec.progress_message or "",
                                        created_at=created_at,
                                        started_at=started_at,
                                        finished_at=finished_at,
                                        result=result_data,
                                        error=rec.error_message,
                                        retry_count=getattr(rec, "retry_count", 0),
                                        cancel_requested=getattr(
                                            rec, "cancel_requested", False
                                        ),
                                    )
                                    restored._target_library_id = rec.library_id
                                    results.append(restored)
                        return results
            except Exception as e:
                logger.debug("Database list_jobs lookup error: %s", e)

        # Fallback to in-memory list
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            if norm_status == "succeeded":
                jobs = [
                    j
                    for j in jobs
                    if j.status in (JobStatus.SUCCEEDED, JobStatus.COMPLETED)
                    or j.status == "completed"
                ]
            elif norm_status:
                jobs = [
                    j
                    for j in jobs
                    if j.status.value == norm_status or j.status == norm_status
                ]
            return jobs[:limit]

    def cleanup_old_jobs(
        self,
        max_age_seconds: int = 3600,
        app: Flask | None = None,
        delete_db: bool = False,
    ) -> int:
        """Removes finished jobs older than max_age_seconds from memory (and optionally from DB)."""
        now = datetime.now(UTC)
        removed = 0
        with self._lock:
            to_delete = []
            for j_id, j in self._jobs.items():
                if j.status in (
                    JobStatus.SUCCEEDED,
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                    JobStatus.INTERRUPTED,
                ):
                    end_time = j.finished_at or j.created_at
                    if end_time.tzinfo is None:
                        end_time = end_time.replace(tzinfo=UTC)
                    if (now - end_time).total_seconds() >= max_age_seconds:
                        to_delete.append(j_id)
            for j_id in to_delete:
                self._jobs.pop(j_id, None)
                removed += 1

        if delete_db:
            target_app = app
            if target_app is None:
                try:
                    from flask import current_app, has_app_context

                    if has_app_context():
                        target_app = current_app
                except Exception:
                    target_app = None

            if target_app is not None:
                try:
                    with target_app.app_context():
                        cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
                        stmt = delete(JobRecord).where(
                            JobRecord.status.in_(
                                [
                                    JobStatus.SUCCEEDED.value,
                                    JobStatus.COMPLETED.value,
                                    JobStatus.FAILED.value,
                                    JobStatus.CANCELLED.value,
                                    JobStatus.INTERRUPTED.value,
                                ]
                            ),
                            JobRecord.finished_at <= cutoff,
                        )
                        db.session.execute(stmt)
                        db.session.commit()
                except Exception as e:
                    logger.debug("Database cleanup_old_jobs error: %s", e)

        return removed

    def reconcile_on_startup(self, app: Flask) -> int:
        """Reconciles dangling queued or running jobs on application startup."""
        count = 0
        try:
            with app.app_context():
                stmt = (
                    update(JobRecord)
                    .where(
                        JobRecord.status.in_(
                            [JobStatus.QUEUED.value, JobStatus.RUNNING.value]
                        )
                    )
                    .values(
                        status=JobStatus.INTERRUPTED.value,
                        finished_at=datetime.now(UTC),
                        progress_message="Interrupted by server restart",
                        error_message="Job interrupted by server restart",
                    )
                )
                res = db.session.execute(stmt)
                db.session.commit()
                count = res.rowcount
                if count > 0:
                    logger.info("Reconciled %d interrupted jobs on startup", count)
        except Exception as e:
            logger.warning("Failed to reconcile jobs on startup: %s", e)
        return count

    def _prune_history_locked(self) -> None:
        """Prunes oldest terminal jobs if in-memory history limit exceeded."""
        if len(self._jobs) >= self._max_history:
            terminal = [
                j
                for j in self._jobs.values()
                if j.status
                in (
                    JobStatus.SUCCEEDED,
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                    JobStatus.INTERRUPTED,
                )
            ]
            terminal.sort(key=lambda j: j.created_at)
            excess = len(self._jobs) - self._max_history + 1
            for j in terminal[:excess]:
                self._jobs.pop(j.id, None)

    def shutdown(self, wait: bool = False) -> None:
        """Shuts down the worker pool."""
        self._executor.shutdown(wait=wait)


job_manager = JobManager()
