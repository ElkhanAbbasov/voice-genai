import asyncio
import websockets
import json
import base64
import pyaudio

# --- CONFIGURATION ---
SERVER_URL = "ws://localhost:8080/media-stream"
CHUNK_SIZE = 512
FORMAT = pyaudio.paInt16
CHANNELS = 1
# IMPORTANT: Match Gemini's requirements directly
MIC_RATE = 16000  
SPK_RATE = 24000 

async def call_agent():
    print(f"📞 Connecting to {SERVER_URL}...")
    
    try:
        async with websockets.connect(SERVER_URL) as ws:
            print("✅ Connected! Speak now.")

            p = pyaudio.PyAudio()
            
            # Input: 16kHz (Gemini Native Input)
            mic_stream = p.open(format=FORMAT, channels=CHANNELS, rate=MIC_RATE, input=True, frames_per_buffer=CHUNK_SIZE)
            
            # Output: 24kHz (Gemini Native Output)
            spk_stream = p.open(format=FORMAT, channels=CHANNELS, rate=SPK_RATE, output=True)

            async def send_mic():
                while True:
                    try:
                        data = mic_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                        payload = base64.b64encode(data).decode("utf-8")
                        # Just wrap in JSON, no audio conversion
                        await ws.send(json.dumps({
                            "event": "media",
                            "media": {"payload": payload}
                        }))
                        await asyncio.sleep(0.001)
                    except Exception:
                        break

            async def receive_audio():
                while True:
                    try:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        if data.get("event") == "media":
                            # Just decode and play
                            audio_data = base64.b64decode(data["media"]["payload"])
                            spk_stream.write(audio_data)
                    except Exception:
                        break

            await asyncio.gather(send_mic(), receive_audio())

            # Cleanup
            mic_stream.stop_stream()
            mic_stream.close()
            spk_stream.stop_stream()
            spk_stream.close()
            p.terminate()

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(call_agent())
    except KeyboardInterrupt:
        print("\nEnded. (CTRL+C pressed)")