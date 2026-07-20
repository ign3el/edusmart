"""
Piper TTS HTTP Client
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

class PiperClient:
    def __init__(self):
        # Piper TTS endpoint (self-hosted)
        self.base_url = os.getenv("PIPER_URL", "http://piper-tts:5000")
        self.api_key = os.getenv("PIPER_API_KEY", None) # For future use if API is secured
        self.timeout = 90  # Increased timeout for potentially longer generations

    async def generate_audio(self, text: str, speed: float, silence: float) -> Optional[bytes]:
        """
        Generate TTS audio via Piper HTTP API.

        This service is a single-model, Arabic-only deployment - it has no
        language switch. Sending a "language" key in the payload makes the
        server error out (confirmed: 500 "Dimension out of range"), so it
        must not be included.

        Args:
            text: Text to synthesize.
            speed: Playback speed (e.g., 1.0).
            silence: Silence in seconds between sentences.

        Returns:
            Audio bytes (WAV) or None on failure.

        Raises:
            TTSConnectionError: If the service cannot be reached.
        """
        try:
            # Prepare headers
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["X-API-Key"] = self.api_key

            # Prepare payload for Piper API
            payload = {
                "text": text,
                "speed": speed,
                "silence": silence,
            }
            
            # Call Piper API
            response = await asyncio.to_thread(
                requests.post,
                f"{self.base_url}/tts",
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                audio_bytes = response.content
                print(f"✓ Piper TTS generated: {len(audio_bytes)} bytes")
                return audio_bytes
            else:
                print(f"✗ Piper TTS failed: {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.ConnectionError as e:
            print(f"✗ Piper TTS connection failed: {e}")
            raise TTSConnectionError(f"Could not connect to Piper TTS at {self.base_url}. Please ensure the service is running.")
        except requests.exceptions.Timeout:
            print("✗ Piper TTS timeout")
            return None
        except Exception as e:
            print(f"✗ Piper TTS error: {e}")
            return None

# Global instance
piper_tts = PiperClient()
