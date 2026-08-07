import asyncio
import base64
import os
from pathlib import Path

import certifi
from dotenv import load_dotenv
from openai import AsyncAzureOpenAI

try:
    from .prototype_config import load_azure_realtime_settings
except ImportError:  # Support direct execution from the prototype directory.
    from prototype_config import load_azure_realtime_settings


os.environ["SSL_CERT_FILE"] = certifi.where()

load_dotenv()


async def main():
    settings = load_azure_realtime_settings()

    async with AsyncAzureOpenAI(
        api_key=settings.api_key,
        azure_endpoint=settings.endpoint,
        api_version=settings.api_version,
        azure_deployment=settings.deployment,
    ) as client:
        async with client.realtime.connect(model=settings.deployment) as conn:
            await conn.session.update(
                session={
                    "instructions": "You are a terse assistant.",
                    "modalities": ["text", "audio"],
                    "voice": settings.voice,
                    "output_audio_format": "pcm16",
                }
            )

            audio = bytearray()
            await conn.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Read this back: order A7K-2941, ETA 14:35.",
                        }
                    ],
                }
            )
            await conn.response.create()

            async for ev in conn:
                if ev.type in {
                    "response.audio_transcript.delta",
                    "response.output_audio_transcript.delta",
                }:
                    print(ev.delta, end="", flush=True)
                elif ev.type in {
                    "response.audio.delta",
                    "response.output_audio.delta",
                }:
                    audio.extend(base64.b64decode(ev.delta))
                elif ev.type == "error":
                    raise RuntimeError(ev.error.message)
                elif ev.type == "response.done":
                    break

            Path("out.pcm").write_bytes(audio)
            print(f"\n{len(audio)} bytes → ffplay -f s16le -ar 24000 -ac 1 out.pcm")


if __name__ == "__main__":
    asyncio.run(main())
