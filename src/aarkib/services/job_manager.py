from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    """Execution status of an asynchronous background job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    _future: Future | None = None

    @property
    def completed_at(self) -> datetime | None:
        return self.finished_at

    def to_dict(self) -> dict[str, Any]:
        """Serializes the job to a JSON-compatible dictionary."""
        elapsed = None
        if self.started_at:
            end_time = self.finished_at or datetime.now(UTC)
            elapsed = round((end_time - self.started_at).total_seconds(), 2)
        return {
            "id": self.id,
            "job_type": self.job_type,
            "status": self.status.value,
            "progress": round(self.progress, 1),
            "progress_message": self.progress_message,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "completed_at": self.finished_at.isoformat() if self.finished_at else None,
            "elapsed_seconds": elapsed,
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    """In-process thread-pool supervisor for non-blocking media tasks."""

    def __init__(self, max_workers: int = 2, max_history: int = 100) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="aarkib-worker"
        )
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_history = max_history

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
            self._jobs[job_id] = job

        def progress_callback(percentage: float, message: str = "") -> None:
            with self._lock:
                job.progress = max(0.0, min(100.0, float(percentage)))
                if message:
                    job.progress_message = str(message)

        kwargs["progress_callback"] = progress_callback

        def _worker():
            with self._lock:
                job.status = JobStatus.RUNNING
                job.started_at = datetime.now(UTC)
                job.progress = 0.0
                job.progress_message = "Starting task..."
            try:
                if app is not None:
                    with app.app_context():
                        res = fn(app, *args, **kwargs)
                else:
                    res = fn(*args, **kwargs)
                with self._lock:
                    job.status = JobStatus.COMPLETED
                    job.progress = 100.0
                    job.finished_at = datetime.now(UTC)
                    job.progress_message = "Completed"
                    job.result = res if isinstance(res, dict) else {"result": res}
            except Exception as e:
                logger.error(
                    "Job %s (%s) failed: %s", job.id, job.job_type, e, exc_info=True
                )
                with self._lock:
                    job.status = JobStatus.FAILED
                    job.finished_at = datetime.now(UTC)
                    job.progress_message = f"Failed: {e}"
                    job.error = str(e)

        job._future = self._executor.submit(_worker)
        return job

    def get_job(self, job_id: str) -> Job | None:
        """Retrieves a job by its unique identifier."""
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 20) -> list[Job]:
        """Returns the most recent jobs ordered newest first."""
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return jobs[:limit]

    def cleanup_old_jobs(self, max_age_seconds: int = 3600) -> int:
        """Removes finished jobs older than max_age_seconds."""
        now = datetime.now(UTC)
        removed = 0
        with self._lock:
            to_delete = []
            for j_id, j in self._jobs.items():
                if j.status in (
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                ):
                    end_time = j.finished_at or j.created_at
                    if (now - end_time).total_seconds() >= max_age_seconds:
                        to_delete.append(j_id)
            for j_id in to_delete:
                self._jobs.pop(j_id, None)
                removed += 1
        return removed

    def _prune_history_locked(self) -> None:
        """Prunes oldest terminal jobs if history limit exceeded."""
        if len(self._jobs) >= self._max_history:
            terminal = [
                j
                for j in self._jobs.values()
                if j.status
                in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
            ]
            terminal.sort(key=lambda j: j.created_at)
            excess = len(self._jobs) - self._max_history + 1
            for j in terminal[:excess]:
                self._jobs.pop(j.id, None)

    def shutdown(self, wait: bool = False) -> None:
        """Shuts down the worker pool."""
        self._executor.shutdown(wait=wait)


job_manager = JobManager()
