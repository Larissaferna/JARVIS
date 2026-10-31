import os
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()

cliente = ElevenLabs(
    api_key=os.getenv("ELEVENLABS_API_KEY")
)

vozes = cliente.voices.get_all()

for voz in vozes.voices:
    print(f"{voz.name} | {voz.voice_id}")