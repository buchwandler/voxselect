from __future__ import annotations

import ast
import base64
import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from readio.api import EngineInfo, TargetInfo, VoiceInfo
from readio.api import WorkflowStageError

SERVICE_PATH = Path(__file__).resolve().parents[1] / "readio-service" / "service.py"
SPEC = importlib.util.spec_from_file_location("voxselect_readio_service", SERVICE_PATH)
assert SPEC is not None and SPEC.loader is not None
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


class FakeReadio:
    def __init__(self) -> None:
        self.synthesis_calls: list[tuple[Any, Any]] = []
        self.target_calls: list[tuple[str, dict[str, Any]]] = []
        self.voice_calls: list[tuple[str, str, dict[str, Any]]] = []
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
        self.voices = (
            VoiceInfo(
                "voice",
                "en-US",
                "en-US",
                "English (US)",
                "neutral",
                "synthesized",
                True,
                True,
            ),
        )
        self.capability_result = {
            "schema": "readio.capabilities.v1",
            "result": {"readio_version": "test"},
        }

    def list_engines(self) -> tuple[EngineInfo, ...]:
        return self.engines

    def list_targets(self, engine: str, **kwargs: Any) -> tuple[TargetInfo, ...]:
        self.target_calls.append((engine, kwargs))
        return self.targets

    def list_voices(
        self, *, engine: str, target: str, **kwargs: Any
    ) -> tuple[VoiceInfo, ...]:
        self.voice_calls.append((engine, target, kwargs))
        return self.voices

    def capabilities(self) -> dict[str, Any]:
        return self.capability_result

    def synthesis_capabilities(self) -> dict[str, Any]:
        return {
            "schema": "readio.synthesis-capabilities.v1",
            "result": {"state": "ready", "engines": []},
        }

    def synthesize(
        self,
        request: Any,
        *,
        cancellation: Any,
        on_progress: Any = None,
    ) -> Any:
        self.synthesis_calls.append((request, cancellation))
        cancellation.raise_if_cancelled()
        return self.result()

    @staticmethod
    def result(samples: Any = (0.25, -0.125)) -> Any:
        samples = np.asarray(samples, dtype=np.float32)
        frames = int(samples.shape[0]) if samples.ndim else 1
        audio = SimpleNamespace(
            audio=samples,
            frames=frames,
            duration_seconds=frames / 24000,
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

    ok, payload = service.handle(
        instance, {"protocol": service.PROTOCOL, "command": "engines", "params": {}}
    )

    assert ok
    normalized = service.jsonable(payload)
    assert normalized["engines"][0]["id"] == "kitten"
    assert normalized["targets_for_selected_engine"][0]["id"] == "cpu"
    assert fake_readio.target_calls == [
        ("kitten", {"language": "en-US", "offline": True})
    ]


def test_target_language_round_trips_through_service_catalog_commands(
    bridge: Any,
) -> None:
    instance, fake_readio, _, _ = bridge
    ok, target_payload = service.handle(
        instance,
        {
            "protocol": service.PROTOCOL,
            "command": "targets",
            "params": {"engine": "kitten", "offline": False},
        },
    )
    assert ok
    target = service.jsonable(target_payload)["result"]["items"][0]
    language = target["languages"][0]

    ok, _ = service.handle(
        instance,
        {
            "protocol": service.PROTOCOL,
            "command": "voices",
            "params": {
                "engine": "kitten",
                "target": target["id"],
                "language": language,
                "offline": False,
            },
        },
    )

    assert ok
    assert language == "en-US"
    assert fake_readio.voice_calls == [
        (
            "kitten",
            "cpu",
            {"language": "en-US", "offline": False, "refresh": False},
        )
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
    assert audio_event["wav_bytes"] == len(b"RIFF-test-wav")
    assert audio_event["frames"] == 2
    assert audio_event["duration_seconds"] == 2 / 24000
    assert audio_event["peak"] == 0.25


def test_synthesis_progress_is_generation_scoped_and_does_not_echo_text(
    bridge: Any,
) -> None:
    instance, fake_readio, events, event_received = bridge
    spoken_text = "private selected phrase"

    def synthesize(
        request: Any,
        *,
        cancellation: Any,
        on_progress: Any,
    ) -> Any:
        on_progress(
            SimpleNamespace(
                stage="compile",
                message=spoken_text,
                current=2,
                total=4,
            )
        )
        return FakeReadio.result()

    fake_readio.synthesize = synthesize
    generation = instance.queue_speech(spoken_text)

    assert event_received.wait(timeout=2)
    progress = next(event for event in events if event["event"] == "progress")
    assert progress == {
        "event": "progress",
        "generation": generation,
        "stage": "compile",
        "message": "Preparing speech",
        "current": 2,
        "total": 4,
    }
    assert spoken_text not in str(progress)


def test_stale_progress_is_suppressed_after_replacement(bridge: Any) -> None:
    instance, fake_readio, events, event_received = bridge
    first_entered = threading.Event()

    def synthesize(
        request: Any,
        *,
        cancellation: Any,
        on_progress: Any,
    ) -> Any:
        if request.text == "first":
            first_entered.set()
            while not cancellation.cancelled:
                time.sleep(0.001)
            on_progress(SimpleNamespace(stage="render", message="stale"))
            cancellation.raise_if_cancelled()
        on_progress(SimpleNamespace(stage="render", message="current"))
        return FakeReadio.result()

    fake_readio.synthesize = synthesize
    stale_generation = instance.queue_speech("first")
    assert first_entered.wait(timeout=2)
    current_generation = instance.queue_speech("second")

    assert event_received.wait(timeout=2)
    progress = [event for event in events if event["event"] == "progress"]
    assert len(progress) == 1
    assert progress[0]["generation"] == current_generation
    assert progress[0]["generation"] != stale_generation


@pytest.mark.parametrize(
    ("samples", "expected_message"),
    [
        ([], "empty audio buffer"),
        ([0.0, 0.0], "silent audio"),
        ([np.nan], "non-finite audio samples"),
    ],
)
def test_invalid_audio_becomes_speech_error(
    bridge: Any,
    samples: list[float],
    expected_message: str,
) -> None:
    instance, fake_readio, events, event_received = bridge
    fake_readio.synthesize = lambda *_args, **_kwargs: FakeReadio.result(samples)

    instance.queue_speech("hello")

    assert event_received.wait(timeout=2)
    assert events[-1]["event"] == "speech_error"
    assert expected_message in events[-1]["message"]
    assert not any(event["event"] == "audio" for event in events)


def test_new_speech_cancels_inflight_generation_without_emitting_stale_error(
    bridge: Any,
) -> None:
    instance, fake_readio, events, event_received = bridge
    first_entered = threading.Event()

    def synthesize(request: Any, *, cancellation: Any, on_progress: Any = None) -> Any:
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
    assert [event["event"] for event in events] == ["speech_canceled", "audio"]
    assert events[0]["generation"] == first_generation
    assert events[1]["generation"] == second_generation
    assert second_generation > first_generation
    assert fake_readio.synthesis_calls[-1][0].text == "second"


def test_stop_emits_one_terminal_cancellation_for_active_generation(
    bridge: Any,
) -> None:
    instance, fake_readio, events, _ = bridge
    first_entered = threading.Event()
    cancellation_observed = threading.Event()

    def synthesize(request: Any, *, cancellation: Any, on_progress: Any = None) -> Any:
        fake_readio.synthesis_calls.append((request, cancellation))
        first_entered.set()
        while not cancellation.cancelled:
            time.sleep(0.001)
        cancellation_observed.set()
        cancellation.raise_if_cancelled()

    fake_readio.synthesize = synthesize
    generation = instance.queue_speech("active")
    assert first_entered.wait(timeout=2)

    instance.stop()

    assert events == [{"event": "speech_canceled", "generation": generation}]
    assert cancellation_observed.wait(timeout=2)
    assert events == [{"event": "speech_canceled", "generation": generation}]


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

    def fail(request: Any, *, cancellation: Any, on_progress: Any = None) -> Any:
        raise error

    fake_readio.synthesize = fail
    instance.queue_speech("hello")

    assert event_received.wait(timeout=2)
    payload = events[-1]
    assert payload["event"] == "speech_error"
    assert payload["error"] == {
        "schema": "readio.error-info.v1",
        "result": {
            "type": "WorkflowStageError",
            "message": str(error),
            "code": "engine.failed",
            "stage": "render",
            "package": "voicerender",
            "selector": "kitten:cpu",
            "details": {"retryable": False},
            "recoverable": False,
            "suggested_action": None,
        },
    }


def test_handle_rejects_unsupported_protocol(bridge: Any) -> None:
    instance, _, _, _ = bridge
    with pytest.raises(ValueError, match="unsupported request protocol"):
        service.handle(
            instance,
            {"protocol": 1, "command": "ping", "params": {}},
        )


def test_speech_job_uses_a_settings_snapshot(
    bridge: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, fake_readio, _, event_received = bridge
    instance.configure({"engine": "first-engine", "target": "first-target"})
    original_token = service.CancellationToken

    def reconfigure_before_request() -> Any:
        instance.configure({"engine": "second-engine", "target": "second-target"})
        return original_token()

    monkeypatch.setattr(service, "CancellationToken", reconfigure_before_request)
    instance.queue_speech("hello")

    assert event_received.wait(timeout=2)
    request, _ = fake_readio.synthesis_calls[0]
    assert request.engine == "first-engine"
    assert request.target == "first-target"


def test_capability_commands_return_versioned_public_schemas(bridge: Any) -> None:
    instance, fake_readio, _, _ = bridge
    request = {"protocol": service.PROTOCOL, "params": {}}

    ok, aggregate = service.handle(instance, {**request, "command": "capabilities"})
    assert ok
    assert aggregate == fake_readio.capability_result

    ok, synthesis = service.handle(
        instance, {**request, "command": "synthesis_capabilities"}
    )
    assert ok
    assert synthesis["schema"] == "readio.synthesis-capabilities.v1"
    assert synthesis["result"]["state"] == "ready"


def test_target_and_voice_catalogs_use_cached_readio_public_methods(
    bridge: Any,
) -> None:
    instance, fake_readio, _, _ = bridge
    base = {"protocol": service.PROTOCOL, "params": {"engine": "kitten"}}

    ok, targets = service.handle(instance, {**base, "command": "targets"})
    assert ok
    assert targets["schema"] == "voxselect.target-catalog.v1"
    assert targets["result"]["items"][0]["id"] == "cpu"
    assert fake_readio.target_calls[-1] == (
        "kitten",
        {"language": None, "offline": True, "refresh": False},
    )

    ok, voices = service.handle(
        instance,
        {
            "protocol": service.PROTOCOL,
            "command": "voices",
            "params": {"engine": "kitten", "target": "cpu"},
        },
    )
    assert ok
    assert voices["schema"] == "voxselect.voice-catalog.v1"
    assert voices["result"]["items"][0]["id"] == "voice"
    assert fake_readio.voice_calls == [
        ("kitten", "cpu", {"language": None, "offline": True, "refresh": False})
    ]


def test_catalog_parameters_are_validated() -> None:
    with pytest.raises(ValueError, match="engine is required"):
        service._identifier({}, "engine")
    with pytest.raises(ValueError, match="offline must be a boolean"):
        service._boolean({"offline": "false"}, "offline", default=True)


def test_diagnostics_include_safe_metadata_without_speech_content(bridge: Any) -> None:
    instance, _, _, _ = bridge
    instance.configure({"engine": "kitten", "target": "cpu", "voice": "voice"})
    selected_text = "private selected text"

    ok, result = service.handle(
        instance,
        {
            "protocol": service.PROTOCOL,
            "command": "diagnostics",
            "params": {"text": selected_text},
        },
    )

    assert ok
    assert result["schema"] == "voxselect.diagnostics.v1"
    details = result["result"]
    assert details["service"]["protocol"] == service.PROTOCOL
    assert details["selection"] == {
        "engine": "kitten",
        "target": "cpu",
        "voice": "voice",
    }
    assert details["readio"]["schema"] == "readio.capabilities.v1"
    assert selected_text not in repr(result)
    assert "wav_base64" not in repr(result)


def test_service_imports_only_the_public_readio_api() -> None:
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    modules = [
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("readio")
    ]
    assert modules == ["readio.api"]
