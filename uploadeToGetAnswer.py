# Load environment variables
import os
from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

import asyncio
import io
import wave
import librosa
import soundfile as sf

# Create a single authenticated client
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

# Native audio model
model = "gemini-2.5-flash-native-audio-preview-09-2025"

config = {
    "response_modalities": ["AUDIO"],
    "system_instruction": "You are a helpful assistant and answer in a very unfriendly tone.",
}

async def main():
    async with client.aio.live.connect(model=model, config=config) as session:

        buffer = io.BytesIO()
        y, sr = librosa.load("sample.wav", sr=16000)
        sf.write(buffer, y, sr, format='RAW', subtype='PCM_16')
        buffer.seek(0)
        audio_bytes = buffer.read()

        await session.send_realtime_input(
            audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
        )

        wf = wave.open("audio.wav", "wb")
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)

        async for response in session.receive():
            if response.data is not None:
                wf.writeframes(response.data)

        wf.close()

if __name__ == "__main__":
    asyncio.run(main())
