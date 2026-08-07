from __future__ import annotations

import asyncio
import base64
from contextlib import ExitStack
import io
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from prototype import app
from prototype.prototype_config import (
    AzureRealtimeSettings,
    ConfigurationError,
    load_azure_realtime_settings,
    normalize_azure_endpoint,
    validate_api_version,
)


VALID_ENV = {
    "AZURE_OPENAI_API_KEY": "test-key",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/openai/v1/",
    "AZURE_OPENAI_API_VERSION": "2025-04-01-preview",
    "AZURE_OPENAI_REALTIME_DEPLOYMENT": "realtime-test",
}

VALID_SETTINGS = AzureRealtimeSettings(
    api_key="test-key",
    endpoint="https://example.openai.azure.com",
    api_version="2025-04-01-preview",
    deployment="realtime-test",
    voice="marin",
)


class ConfigurationTests(unittest.TestCase):
    def test_loads_and_normalizes_valid_configuration(self) -> None:
        settings = load_azure_realtime_settings(VALID_ENV)

        self.assertEqual(settings.endpoint, "https://example.openai.azure.com")
        self.assertEqual(settings.api_version, "2025-04-01-preview")
        self.assertEqual(settings.voice, "marin")

    def test_accepts_stable_and_preview_api_version_dates(self) -> None:
        self.assertEqual(validate_api_version("2025-04-01"), "2025-04-01")
        self.assertEqual(
            validate_api_version("2025-04-01-preview"),
            "2025-04-01-preview",
        )

    def test_rejects_invalid_api_version_formats_and_dates(self) -> None:
        for value in (
            "2025-4-1-preview",
            "2025-04-01-beta",
            "v1",
            "2025-02-30-preview",
        ):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                validate_api_version(value)

    def test_rejects_endpoint_paths_queries_and_non_https_urls(self) -> None:
        for value in (
            "http://example.openai.azure.com",
            "https://example.openai.azure.com/models",
            "https://example.openai.azure.com?api-version=example",
        ):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                normalize_azure_endpoint(value)

    def test_reports_all_missing_required_settings(self) -> None:
        with self.assertRaises(ConfigurationError) as caught:
            load_azure_realtime_settings({})

        message = str(caught.exception)
        self.assertIn("AZURE_OPENAI_API_KEY", message)
        self.assertIn("AZURE_OPENAI_API_VERSION", message)
        self.assertIn("AZURE_OPENAI_REALTIME_DEPLOYMENT", message)


class FakeOutputStream:
    instances: list["FakeOutputStream"] = []

    def __init__(self, **_: object) -> None:
        self.started = 0
        self.stopped = 0
        self.closed = 0
        self.writes: list[bytes] = []
        self.underflow_next_write = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def close(self) -> None:
        self.closed += 1

    def write(self, audio: bytes) -> bool:
        self.writes.append(audio)
        underflowed = self.underflow_next_write
        self.underflow_next_write = False
        return underflowed


def wait_until(predicate: object, timeout: float = 1.0) -> None:
    async def wait() -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():  # type: ignore[operator]
            if loop.time() >= deadline:
                raise AssertionError("condition was not reached before timeout")
            await asyncio.sleep(0.005)

    asyncio.run(wait())


class PlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeOutputStream.instances.clear()

    def test_prefills_each_response_and_reports_underflow(self) -> None:
        with patch.object(app.sd, "RawOutputStream", FakeOutputStream):
            player = app.AudioPlayer()
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                player.start()
                stream = FakeOutputStream.instances[0]
                block_bytes = app.BLOCK_SIZE * app.CHANNELS * app.BYTES_PER_SAMPLE

                player.enqueue(b"a" * (app.PLAYBACK_PREFILL_BYTES - block_bytes))
                self.assertEqual(stream.started, 0)
                stream.underflow_next_write = True
                player.enqueue(b"a" * block_bytes)
                player.finish_response()
                wait_until(lambda: player.completed_response_count == 1)

                player.enqueue(b"b" * app.PLAYBACK_PREFILL_BYTES)
                player.finish_response()
                wait_until(lambda: player.completed_response_count == 2)
                player.stop()

        self.assertEqual(stream.started, 2)
        self.assertEqual(stream.stopped, 2)
        self.assertEqual(stream.closed, 1)
        self.assertEqual(player.underflow_count, 1)
        self.assertIn("Playback underflow #1", output.getvalue())
        self.assertIn("2 completed response(s)", output.getvalue())


class FakeRealtimeConnection:
    def __init__(self) -> None:
        self.events: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.items: list[dict[str, object]] = []
        self.response_count = 0
        self.conversation = SimpleNamespace(
            item=SimpleNamespace(create=self.create_item)
        )
        self.response = SimpleNamespace(create=self.create_response)

    def __aiter__(self) -> "FakeRealtimeConnection":
        return self

    async def __anext__(self) -> dict[str, object]:
        return await self.events.get()

    async def create_item(self, *, item: dict[str, object]) -> None:
        self.items.append(item)

    async def create_response(self) -> None:
        self.response_count += 1
        encoded = base64.b64encode(
            f"response-{self.response_count}".encode()
        ).decode()
        for event in (
            {"type": "response.created"},
            {"type": "response.audio.delta", "delta": encoded},
            {"type": "response.audio.done"},
            {"type": "response.done"},
        ):
            await self.events.put(event)


class RecordingAudioPlayer:
    def __init__(self) -> None:
        self.audio: list[bytes] = []
        self.finished_responses = 0

    def enqueue(self, audio: bytes) -> None:
        self.audio.append(audio)

    def finish_response(self) -> None:
        self.finished_responses += 1


class TurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_consecutive_text_audio_turns_succeed(self) -> None:
        connection = FakeRealtimeConnection()
        player = RecordingAudioPlayer()
        state = app.RuntimeState()
        stop_event = asyncio.Event()
        response_complete_event = asyncio.Event()
        receiver = asyncio.create_task(
            app.receive_realtime_events(
                connection,
                player,  # type: ignore[arg-type]
                state,
                stop_event,
                text_input_mode=True,
                response_complete_event=response_complete_event,
            )
        )

        prompts = AsyncMock(side_effect=["first answer", "second answer", "/quit"])
        try:
            with patch.object(app, "_read_console_line", prompts):
                await app.text_prompt_sender(
                    connection,
                    state,
                    stop_event,
                    response_complete_event,
                )
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

        self.assertEqual(connection.response_count, 2)
        self.assertEqual(len(connection.items), 2)
        self.assertEqual(
            [item["content"][0]["text"] for item in connection.items],  # type: ignore[index]
            ["first answer", "second answer"],
        )
        self.assertEqual(player.finished_responses, 2)
        self.assertEqual(player.audio, [b"response-1", b"response-2"])


class FakeCamera:
    def __init__(self) -> None:
        self.released = 0

    def isOpened(self) -> bool:
        return True

    def release(self) -> None:
        self.released += 1


class FakeInputStream:
    def __init__(self, **_: object) -> None:
        self.started = 0
        self.stopped = 0
        self.closed = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def close(self) -> None:
        self.closed += 1


class FakeAudioPlayer:
    instances: list["FakeAudioPlayer"] = []

    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.runtime_error = None
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


class ConnectionContext:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error

    async def __aenter__(self) -> object:
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return SimpleNamespace()

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeClient:
    def __init__(self, connection: ConnectionContext) -> None:
        self.realtime = SimpleNamespace(connect=lambda **_: connection)

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeAudioPlayer.instances.clear()

    async def _run_with_fakes(self, connection_error: BaseException | None) -> tuple:
        camera = FakeCamera()
        microphone = FakeInputStream()
        connection = ConnectionContext(connection_error)
        client = FakeClient(connection)

        async def close_camera_normally(
            _camera: object,
            _state: app.RuntimeState,
            stop_event: asyncio.Event,
        ) -> None:
            if connection_error is None:
                stop_event.set()
            else:
                await stop_event.wait()

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(app, "load_azure_realtime_settings", return_value=VALID_SETTINGS)
            )
            stack.enter_context(patch.object(app, "_validate_audio_settings"))
            stack.enter_context(patch.object(app.cv2, "VideoCapture", return_value=camera))
            stack.enter_context(patch.object(app.cv2, "destroyAllWindows"))
            stack.enter_context(patch.object(app.sd, "RawInputStream", return_value=microphone))
            stack.enter_context(patch.object(app, "AudioPlayer", FakeAudioPlayer))
            stack.enter_context(patch.object(app, "AsyncAzureOpenAI", return_value=client))
            stack.enter_context(
                patch.object(app, "camera_preview_loop", close_camera_normally)
            )
            if connection_error is None:
                await app.run_app()
            else:
                with self.assertRaises(app.AppError):
                    await app.run_app()

        return camera, microphone, FakeAudioPlayer.instances[0]

    async def test_devices_are_released_after_normal_exit(self) -> None:
        camera, microphone, player = await self._run_with_fakes(None)

        self.assertEqual(camera.released, 1)
        self.assertGreaterEqual(microphone.stopped, 1)
        self.assertGreaterEqual(microphone.closed, 1)
        self.assertGreaterEqual(player.stopped, 1)

    async def test_devices_are_released_after_connection_error(self) -> None:
        camera, microphone, player = await self._run_with_fakes(
            RuntimeError("simulated connection failure")
        )

        self.assertEqual(camera.released, 1)
        self.assertGreaterEqual(microphone.stopped, 1)
        self.assertGreaterEqual(microphone.closed, 1)
        self.assertGreaterEqual(player.stopped, 1)

    async def test_invalid_configuration_opens_no_devices(self) -> None:
        configuration_error = ConfigurationError(
            "AZURE_OPENAI_API_VERSION must use YYYY-MM-DD format."
        )
        with patch.object(
            app,
            "load_azure_realtime_settings",
            side_effect=configuration_error,
        ), patch.object(app.cv2, "VideoCapture") as open_camera, patch.object(
            app.sd, "RawInputStream"
        ) as open_microphone, patch.object(app, "AudioPlayer") as create_player:
            with self.assertRaises(app.AppError):
                await app.run_app()

        open_camera.assert_not_called()
        open_microphone.assert_not_called()
        create_player.assert_not_called()


if __name__ == "__main__":
    unittest.main()
