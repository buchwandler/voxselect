from __future__ import annotations

import base64
import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from readio.api import EngineInfo, TargetInfo
from readio.errors import WorkflowStageError

SERVICE_PATH = Path(__file__).resolve().parents[1] / "readio-service" / "service.py"
SPEC = importlib.util.spec_from_file_location("voxselect_readio_service", SERVICE_PATH)
assert SPEC is not None and SPEC.loader is not None
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


class FakeReadio:
    def __init__(self) -> None:
        self.synthesis_calls: list[tuple[Any, Any]] = []
        self.target_calls: list[tuple[str, dict[str, Any]]] = []
        self.engines = (EngineInfo("kitten", "1", True, True, ("speech",)),)
        self.targets = (
            TargetInfo(
                id="cpu",
                languages=("en-US",),
                status="available",
                runtime_available=True,
                default_voice="voice",
                voice_count=1,
                qualities=(),
                voice_mode=None,
                sample_rate=24000,
                features=(),
                reference_only=False,
            ),
        )

    def list_engines(self) -> tuple[EngineInfo, ...]:
        return self.engines

    def list_targets(self, engine: str, **kwargs: Any) -> tuple[TargetInfo, ...]:
        self.target_calls.append((engine, kwargs))
        return self.targets

    def synthesize(self, request: Any, *, cancellation: Any) -> Any:
        self.synthesis_calls.append((request, cancellation))
        cancellation.raise_if_cancelled()
        return self.result()

    @staticmethod
    def result() -> Any:
        audio = SimpleNamespace(
            to_wav_bytes=lambda: b"RIFF-test-wav",
            sample_rate=24000,
            channels=1,
        )
        return SimpleNamespace(
            audio=audio,
            profile_id="test-profile",
            warnings=("test warning",),
            elapsed_seconds=0.25,
        )


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> Any:
    created: list[FakeReadio] = []
    events: list[dict[str, Any]] = []
    event_received = threading.Event()

    def create_readio() -> FakeReadio:
        instance = FakeReadio()
        created.append(instance)
        return instance

    def capture(payload: Any) -> None:
        events.append(dict(payload))
        if payload.get("event") in {"audio", "speech_error"}:
            event_received.set()

    monkeypatch.setattr(service, "Readio", create_readio)
    monkeypatch.setattr(service, "emit", capture)
    instance = service.Bridge()
    fake_readio = created[0]
    try:
        yield instance, fake_readio, events, event_received
    finally:
        instance.shutdown()
        instance._worker.join(timeout=2)
        assert len(created) == 1


def test_engines_command_uses_normalized_instance_catalog(bridge: Any) -> None:
    instance, fake_readio, _, _ = bridge

    ok, payload = service.handle(instance, {"command": "engines"})

    assert ok
    normalized = service.jsonable(payload)
    assert normalized["engines"][0]["id"] == "kitten"
    assert normalized["targets_for_selected_engine"][0]["id"] == "cpu"
    assert fake_readio.target_calls == [
        ("kitten", {"language": "en-US", "offline": True})
    ]


def test_speech_uses_in_memory_wav_and_passes_cancellation_token(bridge: Any) -> None:
    instance, fake_readio, events, event_received = bridge
    assert instance.readio is fake_readio

    generation = instance.queue_speech("hello")

    assert event_received.wait(timeout=2)
    audio_event = events[-1]
    request, token = fake_readio.synthesis_calls[0]
    assert request.text == "hello"
    assert isinstance(token, service.CancellationToken)
    assert token.cancelled is False
    assert audio_event["generation"] == generation
    assert base64.b64decode(audio_event["wav_base64"]) == b"RIFF-test-wav"
    assert audio_event["sample_rate"] == 24000


def test_new_speech_cancels_inflight_generation_without_emitting_stale_error(
    bridge: Any,
) -> None:
    instance, fake_readio, events, event_received = bridge
    first_entered = threading.Event()

    def synthesize(request: Any, *, cancellation: Any) -> Any:
        fake_readio.synthesis_calls.append((request, cancellation))
        if request.text == "first":
            first_entered.set()
            while not cancellation.cancelled:
                time.sleep(0.001)
            cancellation.raise_if_cancelled()
        return FakeReadio.result()

    fake_readio.synthesize = synthesize
    first_generation = instance.queue_speech("first")
    assert first_entered.wait(timeout=2)
    second_generation = instance.queue_speech("second")

    assert event_received.wait(timeout=2)
    assert fake_readio.synthesis_calls[0][1].cancelled is True
    assert [event["event"] for event in events] == ["audio"]
    assert events[0]["generation"] == second_generation
    assert second_generation > first_generation
    assert fake_readio.synthesis_calls[-1][0].text == "second"


def test_expected_readio_errors_include_structured_error_info(bridge: Any) -> None:
    instance, fake_readio, events, event_received = bridge
    error = WorkflowStageError(
        "render",
        "engine failed",
        package="voicerender",
        selector="kitten:cpu",
        code="engine.failed",
        cause=RuntimeError("engine failed"),
        details={"retryable": False},
    )

    def fail(request: Any, *, cancellation: Any) -> Any:
        raise error

    fake_readio.synthesize = fail
    instance.queue_speech("hello")

    assert event_received.wait(timeout=2)
    payload = events[-1]
    assert payload["event"] == "speech_error"
    assert payload["error"] == {
        "type": "WorkflowStageError",
        "message": str(error),
        "code": "engine.failed",
        "stage": "render",
        "package": "voicerender",
        "selector": "kitten:cpu",
        "details": {"retryable": False},
    }
