import asyncio
import pyaudio
from google import genai
import os
from dotenv import load_dotenv
import warnings 

# 2. Silence the red "Deprecation" text
warnings.filterwarnings("ignore", category=DeprecationWarning)

load_dotenv()
API_KEY = os.environ.get("GOOGLE_API_KEY")

if not API_KEY:
    raise ValueError("API Key not found! Check your .env file.")

# --- CONFIGURATION ---
# The reliable "Native Audio" model
MODEL_ID = "gemini-2.5-flash-native-audio-preview-09-2025"

# INSTRUCTIONS: Load the language/personality from an external file.
try:
    with open("system_instruction.txt", "r", encoding="utf-8") as _f:
        SYSTEM_INSTRUCTIONS = _f.read().strip()
except FileNotFoundError:
    # Fallback sanitized default if the file is missing.
    SYSTEM_INSTRUCTIONS = "You are a helpful assistant with a Turkish regional accent. Speak in Turkish and answer in a calm, introspective tone."

# Audio Settings
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

client = genai.Client(api_key=API_KEY, http_options={'api_version': 'v1alpha'})

async def main():
    audio = pyaudio.PyAudio()
    
    # Auto-find microphone
    mic_index = None
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if info['maxInputChannels'] > 0 and mic_index is None:
            mic_index = i

    print(f"--> Using Microphone Index: {mic_index}")

    mic_stream = audio.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=SEND_SAMPLE_RATE,
        input=True,
        input_device_index=mic_index,
        frames_per_buffer=CHUNK_SIZE
    )
    
    spk_stream = audio.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RECEIVE_SAMPLE_RATE,
        output=True
    )

    print(f"--> Connecting to Gemini Live ({MODEL_ID})...")
    
    config = {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            # voice models: Puck, Fenrir, Kore
            "voice_config": {"prebuilt_voice_config": {"voice_name": "Charon"}}
        },
        "system_instruction": SYSTEM_INSTRUCTIONS
    }

    async with client.aio.live.connect(model=MODEL_ID, config=config) as session:
        print("--> Connected! Speak now. (Ctrl+C to stop)")

        async def send_mic_audio():
            while True:
                try:
                    data = mic_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                    
                    
                    await session.send(
                        input={"data": data, "mime_type": "audio/pcm;rate=16000"}, 
                        end_of_turn=False
                    )
                    
                    await asyncio.sleep(0.01)
                except Exception as e:
                    print(f"Mic Error: {e}")
                    break

        async def receive_model_audio():
            while True:
                try:
                    async for response in session.receive():
                        if response.data:
                            spk_stream.write(response.data)
                except Exception as e:
                    print(f"Speaker Error: {e}")
                    break

        await asyncio.gather(send_mic_audio(), receive_model_audio())

    # Cleanup
    mic_stream.stop_stream()
    mic_stream.close()
    spk_stream.stop_stream()
    spk_stream.close()
    audio.terminate()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n--> Session ended.")