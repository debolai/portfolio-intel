"""Audit sinks: append-only JSON lines locally, Kinesis Firehose (-> S3 Object Lock) in AWS."""

import json
import threading
from pathlib import Path
from typing import Any, Protocol

from portfolio_intel.config import Settings


class Sink(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...


def _line(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, default=str) + "\n"


class JsonlSink:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as f:  # append-only
            f.write(_line(record))


class FirehoseSink:
    def __init__(self, stream: str):
        import boto3

        self.stream = stream
        self.client = boto3.client("firehose")

    def write(self, record: dict[str, Any]) -> None:
        self.client.put_record(
            DeliveryStreamName=self.stream, Record={"Data": _line(record).encode()}
        )


def get_sink(cfg: Settings) -> Sink:
    if cfg.audit_sink == "firehose":
        if not cfg.audit_stream:
            raise ValueError("PI_AUDIT_SINK=firehose needs PI_AUDIT_STREAM")
        return FirehoseSink(cfg.audit_stream)
    return JsonlSink(cfg.audit_path)
