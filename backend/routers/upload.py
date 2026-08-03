import asyncio
import logging
import io
import httpx
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from config import Config
from fastapi.responses import Response
from pydantic import BaseModel, Field
from langdetect import detect, LangDetectException
from pypdf import PdfReader
import docx

from .auth import get_current_user
from database_models import User
from services.story_service import estimate_question_capacity

TTS_PREVIEW_MAX_CHARS = 1000

# Setup logging
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/upload",
    tags=["Upload"],
)

TTS_API_URL = Config.TTS_API_URL
TTS_API_KEY = Config.TTS_API_KEY

class TextExtractionResponse(BaseModel):
    text: str
    language_code: str
    suggested_engine: str
    # How many distinct quiz questions this document can plausibly support, or
    # None when there is too little native text to have an opinion (a scanned
    # PDF reads as empty here but is vision-read during generation). The confirm
    # screen uses this to warn BEFORE a credit is spent; see
    # story_service.estimate_question_capacity.
    estimated_questions: Optional[int] = None

class TTSPreviewRequest(BaseModel):
    text: str = Field(..., max_length=TTS_PREVIEW_MAX_CHARS)
    voice: str
    speed: float = 1.0

@router.post("/extract-text", response_model=TextExtractionResponse)
async def extract_text_from_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """
    Uploads a file, extracts text, detects language, and suggests a TTS engine.
    Supports .txt, .pdf, and .docx files.
    Requires authentication and enforces MAX_UPLOAD_SIZE to prevent abuse.
    """
    try:
        # Read file content
        content = await file.read()
        if len(content) > Config.MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size is {Config.MAX_UPLOAD_SIZE // (1024 * 1024)}MB."
            )
        filename = file.filename.lower() if file.filename else ""
        text = ""
        
        if filename.endswith(".pdf"):
            try:
                pdf_reader = PdfReader(io.BytesIO(content))
                for page in pdf_reader.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\n"
            except Exception as e:
                logger.error(f"Error parsing PDF: {e}")
                raise HTTPException(status_code=400, detail="Failed to extract text from PDF.")
        
        elif filename.endswith(".docx"):
            try:
                doc = docx.Document(io.BytesIO(content))
                text = "\n".join([para.text for para in doc.paragraphs])
            except Exception as e:
                logger.error(f"Error parsing DOCX: {e}")
                raise HTTPException(status_code=400, detail="Failed to extract text from DOCX.")
        
        else:
            # Default to text decoding for .txt or others
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="Invalid file encoding. Please upload a UTF-8 text file, PDF, or DOCX.")

        if not text.strip():
            raise HTTPException(status_code=400, detail="File is empty or contains no readable text.")

        # Detect Language
        try:
            lang_code = detect(text)
        except LangDetectException:
            lang_code = "unknown"

        # Determine Priority
        if lang_code == "ar":
            suggested_engine = "piper" # Prioritize Piper for Arabic
        else:
            suggested_engine = "kokoro" # Prioritize Kokoro for En/Hi/Others

        return {
            "text": text,
            "language_code": lang_code,
            "suggested_engine": suggested_engine,
            "estimated_questions": estimate_question_capacity(text),
        }

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.error(f"Error processing file upload: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tts-preview")
async def tts_preview(
    request: TTSPreviewRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Proxy endpoint for TTS preview to avoid CORS issues.
    Routes to appropriate TTS service based on voice.
    Requires authentication; text length capped to prevent API cost abuse.
    """
    try:
        # Determine endpoint based on voice
        if request.voice.startswith('ar_'):
            # Arabic - use Piper endpoint
            endpoint = f"{TTS_API_URL}/tts"
            payload = {
                "text": request.text,
                "speaker_id": request.voice
            }
            outgoing_headers = {
                'Content-Type': 'application/json',
                'TTS_API_KEY': TTS_API_KEY
            }
        else:
            # English and others - prefer local Kokoro first to avoid external auth/cors issues
            try:
                from services.kokoro_client import generate_tts
                from services.concurrency import tts_governor
                # generate_tts is a blocking requests.post with a 90 second
                # timeout. Called bare from an async endpoint it stalls the
                # event loop for the entire process - one user previewing a
                # voice froze every other user's request until it returned.
                # Every other call site already offloads it the same way.
                #
                # It also shares the story pipeline's TTS governor: previews
                # hit the same CPU-bound Kokoro container, and a few users
                # clicking through voices should queue behind narration rather
                # than compete with it.
                async with tts_governor.slot():
                    audio_bytes = await asyncio.to_thread(
                        generate_tts,
                        text=request.text,
                        voice=request.voice,
                        speed=float(request.speed or 1.0)
                    )
                if audio_bytes:
                    return Response(
                        content=audio_bytes,
                        media_type="audio/mpeg",
                        headers={
                            "Content-Disposition": "inline; filename=preview.mp3"
                        }
                    )
            except Exception as local_exc:
                logger.warning(f"Local Kokoro TTS preview failed: {local_exc}")

            # Fallback to hosted OpenAI-compatible endpoint
            endpoint = f"{TTS_API_URL}/v1/audio/speech"
            payload = {
                "model": "kokoro",
                "input": request.text,
                "voice": request.voice,
                "response_format": "mp3",
                "speed": request.speed
            }
            outgoing_headers = {
                'Content-Type': 'application/json',
                'TTS_API_KEY': TTS_API_KEY
            }

        # Make request to external TTS API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers=outgoing_headers
            )

            if response.status_code != 200:
                logger.error(f"TTS API error: {response.status_code} - {response.text}")
                raise HTTPException(status_code=response.status_code, detail="TTS service error")

            # Return audio response
            return Response(
                content=response.content,
                media_type="audio/mpeg",
                headers={
                    "Content-Disposition": "inline; filename=preview.mp3"
                }
            )

    except httpx.TimeoutException:
        logger.error("TTS API timeout")
        raise HTTPException(status_code=504, detail="TTS service timeout")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in TTS preview: {e}")
        raise HTTPException(status_code=500, detail=str(e))