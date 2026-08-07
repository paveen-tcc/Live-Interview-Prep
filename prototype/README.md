# Desktop prototype closure (M0)

The desktop prototype is preserved as a local learning and smoke-test tool. M0
is closed once the automated checks pass and the optional live-device smoke test
has been completed in the target developer environment. No production web code
should depend on these scripts.

M1 created the web skeleton, so the preserved entry points and tests now live
under `prototype/`. No production web code depends on these scripts.

## What the web product will reuse

- Azure endpoint, API-version, deployment, and voice configuration knowledge.
- Realtime event names and the session configuration lessons captured here.
- Typed `conversation.item.create` followed by `response.create`.
- User-facing categories for authentication, deployment, quota, connection, and
  device failures.
- The interview prompt experiments and the two-turn/manual smoke-test sequence.
- Explicit, idempotent cleanup expectations for normal and failed sessions.

These are design knowledge and test lessons, not a shared production runtime
library. Web configuration will live in the FastAPI application when M1 begins.

## What the web product will not reuse

- `sounddevice`, PortAudio, manual PCM queues, or this jitter buffer. Browser
  audio will use WebRTC.
- OpenCV windows, camera-index selection, or desktop camera lifecycle code.
- Terminal input and daemon input threads.
- Permanent Azure credentials in any browser process.
- The desktop half-duplex microphone policy as the browser transport design.
- Local PCM/WAV capture files as application storage.

## M0 behavior retained

- Configuration is fully validated before devices open. API versions must use a
  real calendar date in `YYYY-MM-DD` or `YYYY-MM-DD-preview` form.
- An Azure endpoint ending in `/openai/v1` is normalized to its resource root;
  unrelated paths, queries, fragments, embedded credentials, and non-HTTPS URLs
  are rejected.
- Audio playback prefills 100 ms for each response. The PortAudio stream is
  stopped between responses so idle time is not counted as an underflow.
- Every detected underflow is printed when it occurs. Shutdown prints the total
  underflow count, completed audio responses, and prefill duration.
- Camera, microphone, speaker, network client, and background tasks are released
  from `finally` paths after both normal shutdown and failures.

## Known limitations

- This is a desktop experiment, not the interview product. It has no resume/JD
  flow, scorecard, adaptive interview policy, persistence, evaluation, auth, or
  report.
- Voice mode requires a desktop camera window and default input/output devices
  supporting 24 kHz mono PCM16. Device selection is not available in the UI.
- Text input is single-line terminal input. It intentionally does not implement
  the browser developer mode's multiline editor, 20,000-character validation,
  draft recovery, or reconnect reconciliation.
- Playback uses a small diagnostic jitter buffer, not an adaptive network jitter
  algorithm. It exists only to make local experiments less fragile.
- The prototype has no automatic reconnect and does not persist a transcript.
- Camera frames remain local. Raw audio is sent directly to Azure Realtime in
  voice mode and is not stored by this app.
- `text-in-audio-out.py` is a one-prompt PCM capture utility; `app.py` is the
  maintained two-way desktop smoke-test path.

## Exit checks

Run the deterministic M0 suite:

```bash
python -m unittest discover -s tests -v
```

It verifies strict configuration handling, two consecutive typed/audio event
turns, per-response playback prefill, underflow reporting, and normal/error
camera-microphone-speaker cleanup with local fakes.

For a real Azure/device check, run `python prototype/app.py --text-input`, submit two
prompts, wait for both spoken responses, then enter `/quit`. Confirm the summary
reports two completed responses and that another run can open the output device.
Run `python prototype/app.py` to repeat the device check for camera and microphone, exiting
with `Q`; use an intentionally invalid deployment for the error-exit check, then
restore the valid value immediately.

Automated checks do not claim that a particular developer machine, Azure
deployment, quota, or network is healthy. Record the live smoke result when it
is run rather than treating a mocked test as proof of external availability.
