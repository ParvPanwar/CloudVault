import json
import logging
import threading
import time
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from worker.processor import MAX_INPUT_BYTES, ProcessingTimeout, process_isolated
from worker.state import LEASE_SECONDS, MAX_ATTEMPTS, WorkerState

logger = logging.getLogger(__name__)


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=1, le=1)
    file_hash: str = Field(max_length=130)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    s3_key: str = Field(max_length=200)
    user_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")


class LeaseLost(RuntimeError):
    pass


class Heartbeat:
    def __init__(self, state, queue, master, owner, receipt):
        self.state, self.queue, self.master = state, queue, master
        self.owner, self.receipt = owner, receipt
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def renew(self):
        if not self.state.renew(self.master, self.owner):
            raise LeaseLost("Processing lease changed")
        self.queue.defer(self.receipt, LEASE_SECONDS)

    def run(self):
        while not self.stop.wait(LEASE_SECONDS // 3):
            try:
                self.renew()
            except (BotoCoreError, ClientError, LeaseLost):
                self.lost.set()
                return

    def check(self):
        if self.lost.is_set():
            raise LeaseLost("Processing lease could not be renewed")

    def __enter__(self):
        self.renew()
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()


class Worker:
    def __init__(self, services, settings):
        self.services, self.settings = services, settings
        self.state = WorkerState(services.database)

    def handle_message(self, message):
        receipt = message["ReceiptHandle"]
        try:
            if len(message.get("Body", "")) > 4096:
                raise ValueError("Oversized job")
            job = Job.model_validate(json.loads(message["Body"]))
            if job.file_hash != f"{job.user_id}:{job.sha256}":
                raise ValueError("Inconsistent job identity")
        except (ValueError, KeyError, ValidationError):
            logger.warning("Invalid job retained for dead-letter routing")
            self.services.queue.defer(receipt, 5)
            return "INVALID"
        master = self.services.database.get_hash(job.user_id, job.sha256)
        if not master or master.get("raw_key") != job.s3_key:
            self.services.queue.defer(receipt, 5)
            return "INVALID"
        if master["status"] == "READY":
            self.services.queue.acknowledge(receipt)
            return "READY"
        if master["status"] == "FAILED":
            self.services.queue.defer(receipt, 20)
            return "FAILED"
        if master.get("attempt_count", 0) >= MAX_ATTEMPTS and master.get("lease_until", 0) <= int(
            time.time()
        ):
            self.state.exhaust(master)
            self.services.queue.defer(receipt, 20)
            return "FAILED"
        if master.get("retry_at", 0) > int(time.time()):
            self.services.queue.defer(receipt, int(master["retry_at"]) - int(time.time()))
            return "RETRY"
        owner = uuid4().hex
        claimed = self.state.claim(master, owner)
        if not claimed:
            self.services.queue.defer(receipt, LEASE_SECONDS)
            return "BUSY"
        try:
            with Heartbeat(self.state, self.services.queue, claimed, owner, receipt) as heartbeat:
                data = self.services.storage.read_verified(
                    claimed,
                    min(self.settings.max_upload_bytes, MAX_INPUT_BYTES),
                )
                output = process_isolated(
                    data, claimed["content_type"], self.settings.processing_timeout_seconds
                )
                heartbeat.check()
                processed_key = self.services.storage.put_artifact(
                    claimed,
                    output.body,
                    output.extension,
                    output.content_type,
                    "processed",
                )
                artifacts = {
                    "processed_s3_key": processed_key,
                    "processed_size_bytes": len(output.body),
                    "processed_content_type": output.content_type,
                }
                if output.thumbnail:
                    artifacts["thumbnail_s3_key"] = self.services.storage.put_artifact(
                        claimed,
                        output.thumbnail,
                        "jpg",
                        "image/jpeg",
                        "thumbnail",
                    )
                    artifacts["thumbnail_size_bytes"] = len(output.thumbnail)
                heartbeat.check()
                if not self.state.complete(claimed, owner, artifacts):
                    raise LeaseLost("Processing lease changed before completion")
        except LeaseLost:
            logger.warning("Stale worker discarded its result")
            return "LEASE_LOST"
        except (BotoCoreError, ClientError, ValueError, ProcessingTimeout, OSError) as exc:
            error = (
                "Processing exceeded its time limit."
                if isinstance(exc, ProcessingTimeout)
                else "File is invalid, unsupported, or exceeds processing limits."
                if isinstance(exc, ValueError)
                else "Processing is temporarily unavailable."
            )
            failed = self.state.failed_attempt(claimed, owner, error)
            if not failed:
                return "LEASE_LOST"
            self.services.queue.defer(receipt, int(failed["retry_at"]) - int(time.time()))
            logger.warning("Processing attempt failed (%s)", type(exc).__name__)
            return "FAILED" if failed["status"] == "FAILED" else "RETRY"
        # A failed acknowledgement must not turn a committed READY result back into QUEUED.
        self.services.queue.acknowledge(receipt)
        return "READY"

    def run_once(self, wait_seconds=20):
        messages = self.services.queue.receive(wait_seconds)
        for message in messages:
            self.handle_message(message)
        return len(messages)
