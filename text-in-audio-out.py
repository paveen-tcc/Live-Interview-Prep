import os, asyncio, base64
from openai import AsyncOpenAI

async def main():
    ep = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    base = ep.replace("https://", "wss://") + "/openai/v1"
    client = AsyncOpenAI(websocket_base_url=base,
                         api_key=os.environ["AZURE_OPENAI_API_KEY"])

    async with client.realtime.connect(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]
    ) as conn:
        await conn.session.update(session={
            "type": "realtime",
            "instructions": "You are a terse assistant.",
            "output_modalities": ["audio"],
            "reasoning": {"effort": "minimal"},      # 2.x only
            "audio": {
                "input": {"format": {"type": "audio/pcm", "rate": 24000}},
                "output": {"voice": "alloy",
                           "format": {"type": "audio/pcm", "rate": 24000}},
            },
        })

        audio = bytearray()
        await conn.conversation.item.create(item={
            "type": "message", "role": "user",
            "content": [{"type": "input_text",
                         "text": "Read this back: order A7K-2941, ETA 14:35."}],
        })
        await conn.response.create()

        async for ev in conn:
            if ev.type == "response.output_audio_transcript.delta":
                print(ev.delta, end="", flush=True)
            elif ev.type == "response.output_audio.delta":
                audio.extend(base64.b64decode(ev.delta))
            elif ev.type == "error":
                raise RuntimeError(ev.error.message)
            elif ev.type == "response.done":
                break

        open("out.pcm", "wb").write(audio)
        print(f"\n{len(audio)} bytes → ffplay -f s16le -ar 24000 -ac 1 out.pcm")

asyncio.run(main())