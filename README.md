# AI Voice Conversation MVP

A minimal local Python app that shows a camera preview and holds a hands-free,
half-duplex voice conversation through Azure OpenAI Realtime. Camera frames
remain local and are never sent to Azure OpenAI.

## Setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install the dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then fill in every Azure value. Never commit
`.env`.

```dotenv
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION=your-resource-api-version
AZURE_OPENAI_REALTIME_DEPLOYMENT=your-realtime-deployment-name
AZURE_OPENAI_REALTIME_VOICE=alloy
CAMERA_INDEX=0
```

If Azure gives you an endpoint ending in `/openai/v1`, you may paste it as-is;
the app removes that service path before configuring `AsyncAzureOpenAI`.

The key alone is not enough to construct an Azure connection. Copy the endpoint,
API version, and deployment name from the deployment details in Azure AI Foundry
or the Azure portal. The deployment must use a Realtime-capable model and region.

Run:

```bash
python app.py
```

## Controls

```text
Speak normally -> AI responds in voice
Q or Esc      -> End session
Ctrl+C        -> End session
```

The AI does not greet automatically; wait for `LISTENING`, then speak first.

## Expected behavior

```text
Camera preview opens
Status becomes LISTENING
User speaks
Status becomes YOU ARE SPEAKING
User stops
Status becomes AI IS THINKING
AI voice plays
Status becomes AI IS SPEAKING
Status returns to LISTENING
```

Ask a second question to verify another turn, then press `Q`. The app should
exit and the camera indicator should turn off. Start it once more to confirm
that every device was released.

## Troubleshooting

- Use headphones to prevent speaker audio from feeding back into the microphone.
- Close Zoom, Teams, or other apps that may exclusively hold the microphone or camera.
- Check the operating system's camera and microphone permissions for your terminal.
- List audio devices with:

  ```bash
  python -c "import sounddevice as sd; print(sd.query_devices())"
  ```

- If PortAudio is unavailable, install it first. On macOS use
  `brew install portaudio`; on Debian/Ubuntu use
  `sudo apt-get install libportaudio2 portaudio19-dev`. Windows wheels normally
  include the required PortAudio library.
- A default input and output device must both support 24 kHz, mono, PCM16 audio.
- The script requires a desktop environment; an OpenCV window cannot open in a
  headless terminal.
- An invalid Azure key produces an authentication error. A valid key can still
  fail if the endpoint, API version, or deployment name belongs to another Azure
  resource or the deployment is not Realtime-compatible.

## Manual smoke test

1. Run `python app.py` in the activated virtual environment.
2. Confirm the mirrored camera preview opens and reaches `LISTENING`.
3. Say, "Hello, can you hear me?" and then stop speaking.
4. Confirm the AI answers through the speakers.
5. Ask one more question and confirm a second spoken response.
6. Press `Q` and confirm the app exits and the camera indicator turns off.
7. Run the app again to confirm the devices were released, then press `Esc`.
