import argparse
import logging
import signal
import threading

from botocore.exceptions import BotoCoreError, ClientError

from backend.config import Settings
from backend.dependencies import build_services
from worker.runner import Worker


def main():
    parser = argparse.ArgumentParser(description="CloudVault SQS image and PDF worker")
    parser.add_argument("--once", action="store_true", help="Poll once and then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    if not all([settings.raw_bucket, settings.processed_bucket, settings.queue_url]):
        parser.error("Set RAW_BUCKET, PROCESSED_BUCKET and QUEUE_URL in .env")
    worker = Worker(build_services(settings), settings)
    stopped = threading.Event()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, lambda *_: stopped.set())
    logging.info("CloudVault worker started in %s", settings.aws_region)
    while not stopped.is_set():
        try:
            worker.run_once()
        except (BotoCoreError, ClientError, OSError):
            logging.exception("Worker operation failed; unacknowledged jobs remain retryable")
            if stopped.wait(5):
                break
        if args.once:
            break


if __name__ == "__main__":
    main()
