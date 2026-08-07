"""Local voice or typed-input conversation using Azure OpenAI Realtime."""

from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass
import os
import queue
import threading
from typing import Any

import cv2
import sounddevice as sd
from dotenv import load_dotenv
from openai import AsyncAzureOpenAI

try:
    from .prototype_config import ConfigurationError, load_azure_realtime_settings
except ImportError:  # Support direct execution: python prototype/app.py
    from prototype_config import ConfigurationError, load_azure_realtime_settings

load_dotenv()

SAMPLE_RATE = 24_000
CHANNELS = 1
DTYPE = "int16"
BLOCK_DURATION_MS = 20
BLOCK_SIZE = SAMPLE_RATE * BLOCK_DURATION_MS // 1_000
WINDOW_NAME = "AI Voice Conversation MVP"
PLAYBACK_PREFILL_MS = 100
BYTES_PER_SAMPLE = 2
PLAYBACK_PREFILL_BYTES = (
    SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE * PLAYBACK_PREFILL_MS // 1_000
)


class AppError(RuntimeError):
    """An expected startup or runtime failure with a user-facing message."""


@dataclass
class RuntimeState:
    status: str = "CONNECTING"
    ai_speaking: bool = False
    stopping: bool = False
    error_message: str | None = None


class AudioPlayer:
    """Play streamed PCM16 with a small startup jitter buffer."""

    _END_RESPONSE = object()
    _SENTINEL = object()

    def __init__(self) -> None:
        self._queue: queue.Queue[bytes | object] = queue.Queue()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._startup_error: BaseException | None = None
        self._diagnostics_printed = False
        self.underflow_count = 0
        self.completed_response_count = 0
        self.runtime_error: BaseException | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._playback_worker,
            name="realtime-audio-player",
            daemon=True,
        )
        self._thread.start()
        if not self._ready_event.wait(timeout=5):
            self.stop()
            raise AppError("Timed out while opening the default speaker output.")
        if self._startup_error is not None:
            self.stop()
            raise AppError(
                "Could not open the default speaker at 24 kHz mono PCM16."
            ) from self._startup_error

    def enqueue(self, audio: bytes) -> None:
        if audio and not self._stop_event.is_set():
            self._queue.put_nowait(audio)

    def finish_response(self) -> None:
        """Flush a response tail and re-arm prefill for the next response."""
        if not self._stop_event.is_set():
            self._queue.put_nowait(self._END_RESPONSE)

    def stop(self) -> None:
        self._stop_event.set()
        self._queue.put_nowait(self._SENTINEL)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3)
        if not self._diagnostics_printed:
            print(
                "Playback diagnostics: "
                f"{self.underflow_count} underflow(s) across "
                f"{self.completed_response_count} completed response(s); "
                f"startup prefill {PLAYBACK_PREFILL_MS} ms."
            )
            self._diagnostics_printed = True

    def _write_audio(self, stream: sd.RawOutputStream, audio: bytes) -> None:
        if not audio:
            return
        if stream.write(audio):
            self.underflow_count += 1
            print(
                "Playback underflow "
                f"#{self.underflow_count}: PortAudio exhausted buffered audio. "
                "Check network jitter and output-device load."
            )

    def _playback_worker(self) -> None:
        playback_buffer = bytearray()
        prefilling = True
        block_bytes = BLOCK_SIZE * CHANNELS * BYTES_PER_SAMPLE
        stream: sd.RawOutputStream | None = None
        stream_started = False
        try:
            stream = sd.RawOutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCK_SIZE,
            )
            self._ready_event.set()
            while not self._stop_event.is_set():
                item = self._queue.get()
                if item is self._SENTINEL or self._stop_event.is_set():
                    break
                if item is self._END_RESPONSE:
                    if playback_buffer:
                        if not stream_started:
                            stream.start()
                            stream_started = True
                        self._write_audio(stream, bytes(playback_buffer))
                        playback_buffer.clear()
                    if stream_started:
                        stream.stop()
                        stream_started = False
                    self.completed_response_count += 1
                    prefilling = True
                    continue

                if not isinstance(item, bytes):
                    continue
                playback_buffer.extend(item)
                if prefilling and len(playback_buffer) < PLAYBACK_PREFILL_BYTES:
                    continue
                if not stream_started:
                    stream.start()
                    stream_started = True
                prefilling = False
                while len(playback_buffer) >= block_bytes:
                    block = bytes(playback_buffer[:block_bytes])
                    del playback_buffer[:block_bytes]
                    self._write_audio(stream, block)
        except BaseException as exc:
            if not self._ready_event.is_set():
                self._startup_error = exc
            else:
                self.runtime_error = exc
        finally:
            if stream is not None:
                if stream_started:
                    try:
                        stream.stop()
                    except Exception:
                        pass
                try:
                    stream.close()
                except Exception:
                    pass
            self._ready_event.set()


def _event_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _event_error_message(event: Any) -> str:
    error = _event_value(event, "error", {})
    message = _event_value(error, "message")
    code = _event_value(error, "code")
    if message and code:
        return f"{message} (code: {code})"
    return str(message or code or "The Realtime API reported an unknown error.")


def _azure_error_detail(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) or getattr(
        exc, "status_code", None
    )
    parts: list[str] = []
    if status_code:
        parts.append(f"HTTP {status_code}")

    message = getattr(exc, "message", None)
    if not message:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict):
                message = error.get("message") or error.get("code")
    if message:
        safe_message = str(message).replace("\n", " ").strip()
        if safe_message:
            parts.append(safe_message[:300])
    return ": ".join(parts) or None


def _format_openai_exception(exc: BaseException) -> str:
    error_name = type(exc).__name__
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) or getattr(
        exc, "status_code", None
    )
    if status_code == 401:
        return (
            "Azure OpenAI rejected the API key. "
            "Check AZURE_OPENAI_API_KEY in .env."
        )
    if status_code in {403, 404}:
        detail = _azure_error_detail(exc)
        return (
            "Azure OpenAI could not access the configured Realtime deployment. "
            "Check the endpoint, deployment name, region, and permissions."
            + (f" Azure response: {detail}" if detail else "")
        )
    if status_code == 429:
        return "OpenAI rate limits or account quota prevented this Realtime session."
    if error_name == "AuthenticationError":
        return (
            "Azure OpenAI rejected the API key. "
            "Check AZURE_OPENAI_API_KEY in .env."
        )
    if error_name in {"PermissionDeniedError", "NotFoundError"}:
        return (
            "Azure OpenAI could not access the configured Realtime deployment. "
            "Check the endpoint, deployment name, region, and permissions."
        )
    if error_name in {"APIConnectionError", "APITimeoutError"}:
        return "Could not connect to OpenAI. Check the network and try again."
    if error_name == "BadRequestError":
        return (
            "Azure OpenAI rejected the Realtime session configuration. "
            "Check the deployment name, API version, and installed OpenAI SDK."
        )
    if error_name in {
        "ConnectionClosedError",
        "InvalidStatus",
        "WebSocketConnectionClosedError",
    }:
        return "The OpenAI Realtime network connection closed unexpectedly."
    if error_name == "OpenAIError":
        return (
            "The OpenAI Realtime client could not start. Install openai[realtime] "
            "and check the model configuration."
        )
    return f"OpenAI Realtime connection failed ({error_name})."


def _print_audio_devices() -> None:
    print("Available audio devices:")
    try:
        print(sd.query_devices())
    except Exception as exc:
        print(f"  Could not query audio devices ({type(exc).__name__}).")


def _validate_audio_settings() -> None:
    try:
        sd.check_input_settings(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE
        )
    except (sd.PortAudioError, ValueError) as exc:
        _print_audio_devices()
        raise AppError(
            "No usable default microphone was found, or it does not support "
            "24 kHz mono PCM16. Check the input device and OS permissions."
        ) from exc

    _validate_output_audio_settings()


def _validate_output_audio_settings() -> None:
    try:
        sd.check_output_settings(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE
        )
    except (sd.PortAudioError, ValueError) as exc:
        _print_audio_devices()
        raise AppError(
            "No usable default speaker was found, or it does not support "
            "24 kHz mono PCM16. Check the output device and PortAudio."
        ) from exc


def _put_microphone_chunk(
    audio_queue: asyncio.Queue[bytes], chunk: bytes, state: RuntimeState
) -> None:
    if state.stopping:
        return
    try:
        audio_queue.put_nowait(chunk)
    except asyncio.QueueFull:
        # Keep latency bounded: discard the oldest unsent 20 ms chunk.
        try:
            audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            audio_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass


async def microphone_sender(
    connection: Any,
    audio_queue: asyncio.Queue[bytes],
    state: RuntimeState,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        chunk = await audio_queue.get()
        if state.ai_speaking:
            continue
        encoded = base64.b64encode(chunk).decode("ascii")
        await connection.input_audio_buffer.append(audio=encoded)


async def receive_realtime_events(
    connection: Any,
    audio_player: AudioPlayer,
    state: RuntimeState,
    stop_event: asyncio.Event,
    *,
    text_input_mode: bool = False,
    response_complete_event: asyncio.Event | None = None,
) -> None:
    response_has_audio = False
    response_audio_finished = False
    try:
        async for event in connection:
            event_type = _event_value(event, "type", "")

            if event_type == "session.created":
                state.status = "CONNECTING"
            elif event_type == "session.updated":
                state.status = "READY FOR TEXT" if text_input_mode else "LISTENING"
            elif event_type == "input_audio_buffer.speech_started":
                state.status = "YOU ARE SPEAKING"
            elif event_type == "input_audio_buffer.speech_stopped":
                state.status = "AI IS THINKING"
            elif event_type == "response.created":
                state.status = "AI IS THINKING"
                response_has_audio = False
                response_audio_finished = False
            elif event_type in {
                "response.audio.delta",
                "response.output_audio.delta",
            }:
                delta = _event_value(event, "delta")
                if delta:
                    response_has_audio = True
                    state.ai_speaking = True
                    state.status = "AI IS SPEAKING"
                    audio_player.enqueue(base64.b64decode(delta, validate=True))
            elif event_type in {"response.audio.done", "response.output_audio.done"}:
                if response_has_audio and not response_audio_finished:
                    audio_player.finish_response()
                    response_audio_finished = True
                state.ai_speaking = False
                state.status = "READY FOR TEXT" if text_input_mode else "LISTENING"
            elif event_type == "response.done":
                if response_has_audio and not response_audio_finished:
                    audio_player.finish_response()
                    response_audio_finished = True
                state.ai_speaking = False
                state.status = "READY FOR TEXT" if text_input_mode else "LISTENING"
                if response_complete_event is not None:
                    response_complete_event.set()
            elif event_type == "error":
                message = _event_error_message(event)
                state.ai_speaking = False
                state.status = "ERROR"
                state.error_message = message
                print(f"Realtime API error: {message}")
                stop_event.set()
                return

        if not stop_event.is_set():
            raise AppError("The OpenAI Realtime connection closed unexpectedly.")
    except asyncio.CancelledError:
        raise
    except AppError:
        raise
    except Exception as exc:
        raise AppError(_format_openai_exception(exc)) from exc


def _complete_console_read(
    future: asyncio.Future[str | None], value: str | None
) -> None:
    if not future.done():
        future.set_result(value)


async def _read_console_line(prompt: str) -> str | None:
    """Read one line without blocking the Realtime event loop."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str | None] = loop.create_future()

    def read_worker() -> None:
        try:
            value: str | None = input(prompt)
        except EOFError:
            value = None
        try:
            loop.call_soon_threadsafe(_complete_console_read, future, value)
        except RuntimeError:
            pass

    threading.Thread(
        target=read_worker,
        name="console-text-input",
        daemon=True,
    ).start()
    return await future


async def text_prompt_sender(
    connection: Any,
    state: RuntimeState,
    stop_event: asyncio.Event,
    response_complete_event: asyncio.Event,
) -> None:
    print("Text input mode: camera and microphone are off.")
    print("Type a message and press Enter. Type /quit to end the session.")

    while not stop_event.is_set():
        state.status = "READY FOR TEXT"
        prompt = await _read_console_line("You: ")
        if prompt is None or prompt.strip().lower() in {"/quit", "/exit"}:
            stop_event.set()
            return
        prompt = prompt.strip()
        if not prompt:
            continue

        response_complete_event.clear()
        state.status = "AI IS THINKING"
        await connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        )
        await connection.response.create()

        response_waiter = asyncio.create_task(response_complete_event.wait())
        stop_waiter = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait(
            {response_waiter, stop_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if stop_waiter in done:
            return


async def camera_preview_loop(
    camera: cv2.VideoCapture,
    state: RuntimeState,
    stop_event: asyncio.Event,
) -> None:
    try:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        while not stop_event.is_set():
            ok, frame = camera.read()
            if not ok:
                raise AppError("Camera frame read failed. The camera may have disconnected.")

            frame = cv2.flip(frame, 1)
            cv2.putText(
                frame,
                "AI VOICE CONVERSATION MVP",
                (24, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            status_color = (
                (80, 220, 80) if state.status == "LISTENING" else (0, 210, 255)
            )
            if state.status == "ERROR":
                status_color = (60, 60, 255)
            cv2.putText(
                frame,
                f"Status: {state.status}",
                (24, 82),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.72,
                status_color,
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                "Speak naturally. Press Q or Esc to quit.",
                (24, frame.shape[0] - 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in {ord("q"), ord("Q"), 27}:
                stop_event.set()
                return
            await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        raise
    except cv2.error as exc:
        raise AppError(
            "Could not show the camera window. A desktop environment is required."
        ) from exc


async def monitor_runtime_tasks(
    tasks: list[asyncio.Task[Any]],
    audio_player: AudioPlayer,
    state: RuntimeState,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        if audio_player.runtime_error is not None:
            message = "Speaker playback failed while the AI was responding."
            state.status = "ERROR"
            state.error_message = message
            stop_event.set()
            raise AppError(message)
        for task in tasks:
            if task.done() and not task.cancelled():
                error = task.exception()
                if error is not None:
                    state.status = "ERROR"
                    state.error_message = str(error)
                    stop_event.set()
                    raise error
        await asyncio.sleep(0.05)


def _close_microphone(stream: sd.RawInputStream | None) -> None:
    if stream is None:
        return
    try:
        stream.stop()
    except Exception:
        pass
    try:
        stream.close()
    except Exception:
        pass


async def _cancel_tasks(tasks: list[asyncio.Task[Any]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_app(*, text_input: bool = False) -> None:
    load_dotenv()
    try:
        azure_settings = load_azure_realtime_settings()
    except ConfigurationError as exc:
        raise AppError(str(exc)) from exc
    camera_index = 0
    if not text_input:
        try:
            camera_index = int(os.getenv("CAMERA_INDEX", "0"))
        except ValueError as exc:
            raise AppError("CAMERA_INDEX must be an integer, such as 0.") from exc

    state = RuntimeState()
    stop_event = asyncio.Event()
    microphone_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
    camera: cv2.VideoCapture | None = None
    microphone_stream: sd.RawInputStream | None = None
    audio_player: AudioPlayer | None = None
    tasks: list[asyncio.Task[Any]] = []

    try:
        if not text_input:
            camera = cv2.VideoCapture(camera_index)
            if not camera.isOpened():
                raise AppError(
                    f"Could not open camera index {camera_index}. Check camera permissions "
                    "and close other camera applications."
                )
            tasks.append(
                asyncio.create_task(
                    camera_preview_loop(camera, state, stop_event),
                    name="camera-preview",
                )
            )

        if text_input:
            _validate_output_audio_settings()
        else:
            _validate_audio_settings()
            loop = asyncio.get_running_loop()

            def microphone_callback(
                indata: memoryview,
                frames: int,
                time_info: Any,
                status: sd.CallbackFlags,
            ) -> None:
                del frames, time_info
                if status:
                    print(f"Microphone warning: {status}")
                loop.call_soon_threadsafe(
                    _put_microphone_chunk,
                    microphone_queue,
                    bytes(indata),
                    state,
                )

            try:
                microphone_stream = sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    blocksize=BLOCK_SIZE,
                    callback=microphone_callback,
                )
                microphone_stream.start()
            except (sd.PortAudioError, ValueError) as exc:
                _print_audio_devices()
                raise AppError(
                    "The default microphone could not be opened. Check that another "
                    "application is not using it and that microphone access is allowed."
                ) from exc

        audio_player = AudioPlayer()
        try:
            audio_player.start()
        except AppError:
            _print_audio_devices()
            raise

        async with AsyncAzureOpenAI(
            api_key=azure_settings.api_key,
            azure_endpoint=azure_settings.endpoint,
            api_version=azure_settings.api_version,
            azure_deployment=azure_settings.deployment,
        ) as client:
            try:
                async with client.realtime.connect(
                    model=azure_settings.deployment
                ) as connection:
                    if stop_event.is_set():
                        return
                    session_config: dict[str, Any] = {
                        # Azure's 2025-04-01-preview Realtime API uses the
                        # original, flat session schema. The model/deployment is
                        # already selected in the WebSocket URL.
                        "modalities": ["text", "audio"],
                        "instructions": (
                            "You are a friendly conversational AI. "
                            "Wait for the user to provide a message first. "
                            "Respond naturally using one or two short spoken sentences. "
                            "Do not conduct an interview and do not evaluate the user."
                        ),
                        "voice": azure_settings.voice,
                        "output_audio_format": "pcm16",
                    }
                    if not text_input:
                        session_config.update(
                            {
                                "input_audio_format": "pcm16",
                                "turn_detection": {
                                    "type": "server_vad",
                                    "create_response": True,
                                    "interrupt_response": False,
                                },
                            }
                        )
                    await connection.session.update(session=session_config)

                    response_complete_event = asyncio.Event()
                    receiver_task = asyncio.create_task(
                        receive_realtime_events(
                            connection,
                            audio_player,
                            state,
                            stop_event,
                            text_input_mode=text_input,
                            response_complete_event=response_complete_event,
                        ),
                        name="realtime-receiver",
                    )
                    tasks.append(receiver_task)
                    if text_input:
                        tasks.append(
                            asyncio.create_task(
                                text_prompt_sender(
                                    connection,
                                    state,
                                    stop_event,
                                    response_complete_event,
                                ),
                                name="text-prompt-sender",
                            )
                        )
                    else:
                        tasks.append(
                            asyncio.create_task(
                                microphone_sender(
                                    connection,
                                    microphone_queue,
                                    state,
                                    stop_event,
                                ),
                                name="microphone-sender",
                            )
                        )
                    monitor_task = asyncio.create_task(
                        monitor_runtime_tasks(
                            list(tasks), audio_player, state, stop_event
                        ),
                        name="runtime-monitor",
                    )
                    tasks.append(monitor_task)

                    stop_waiter = asyncio.create_task(
                        stop_event.wait(), name="shutdown-waiter"
                    )
                    done, _ = await asyncio.wait(
                        {monitor_task, stop_waiter},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if monitor_task in done:
                        await monitor_task
                    stop_waiter.cancel()
                    await asyncio.gather(stop_waiter, return_exceptions=True)
                    if state.error_message:
                        raise AppError(state.error_message)
            except asyncio.CancelledError:
                raise
            except AppError:
                raise
            except Exception as exc:
                raise AppError(_format_openai_exception(exc)) from exc
            finally:
                state.stopping = True
                stop_event.set()
                _close_microphone(microphone_stream)
                microphone_stream = None
                audio_player.stop()
                await _cancel_tasks(tasks)
                tasks.clear()
    finally:
        state.stopping = True
        stop_event.set()
        await _cancel_tasks(tasks)
        try:
            _close_microphone(microphone_stream)
        except Exception:
            pass
        if audio_player is not None:
            audio_player.stop()
        if camera is not None:
            try:
                camera.release()
            except Exception:
                pass
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Talk to Azure OpenAI Realtime using voice or typed prompts."
    )
    parser.add_argument(
        "--text-input",
        action="store_true",
        help="type prompts and hear audio replies without opening camera or microphone",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        asyncio.run(run_app(text_input=args.text_input))
        if args.text_input:
            print("Session ended. The audio output device was released.")
        else:
            print("Session ended. Camera and audio devices were released.")
        return 0
    except KeyboardInterrupt:
        print("\nSession ended by Ctrl+C. Devices were released.")
        return 0
    except AppError as exc:
        print(f"Error: {exc}")
        return 1
    except Exception as exc:
        print(f"Unexpected error ({type(exc).__name__}). Devices were released.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
