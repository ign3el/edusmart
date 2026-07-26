"""
Generic TTS HTTP Client Service
"""
import os
import requests
import asyncio
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

class TTSConnectionError(Exception):
    """Custom exception for when the TTS service cannot be reached."""
    pass

class KokoroTTSClient:
    def __init__(self):
        # Kokoro TTS endpoint (self-hosted)
        self.base_url = os.getenv("KOKORO_URL", "http://kokoro-tts:8880")
        self.api_key = os.getenv("KOKORO_API_KEY", None) # For future use if API is secured
        self.timeout = 90

    async def generate_audio(self, text: str, voice: str = "af_sarah", speed: float = 1.0) -> Optional[bytes]:
        """
        Generate TTS audio via Kokoro-82M HTTP API.
        
        Args:
            text: Text to synthesize.
            voice: Voice identifier (e.g., 'af_sarah').
            speed: Playback speed (e.g., 1.0).
        
        Returns:
            Audio bytes (WAV) or None on failure.
        
        Raises:
            TTSConnectionError: If the service cannot be reached.
        """
        endpoint = f"{self.base_url}/v1/audio/speech"
        
        from config import Config

        payload = {
            "model": "kokoro",
            "input": text,
            "voice": voice,
            # See kokoro_client: "wav" here produced RIFF bytes in .mp3 files.
            "response_format": Config.TTS_AUDIO_FORMAT,
            "speed": speed
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = await asyncio.to_thread(
                requests.post,
                endpoint,
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                audio_bytes = response.content
                print(f"✓ Kokoro TTS generated: {len(audio_bytes)} bytes")
                return audio_bytes
            else:
                print(f"✗ Kokoro TTS failed: {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"✗ Kokoro TTS connection failed: {e}")
            raise TTSConnectionError(f"Could not connect to Kokoro TTS at {self.base_url}. Please ensure the service is running.")
        except Exception as e:
            print(f"✗ Kokoro TTS generic error: {e}")
            return None

# Global instance
kokoro_tts = KokoroTTSClient()
