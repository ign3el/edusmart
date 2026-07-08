import logging
import io
import httpx
from fastapi import APIRouter, UploadFile, File, HTTPException
from config import Config
from fastapi.responses import Response
from pydantic import BaseModel
from langdetect import detect, LangDetectException
from pypdf import PdfReader
import docx

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

class TTSPreviewRequest(BaseModel):
    text: str
    voice: str
    speed: float = 1.0

@router.post("/extract-text", response_model=TextExtractionResponse)
async def extract_text_from_file(file: UploadFile = File(...)):
    """
    Uploads a file, extracts text, detects language, and suggests a TTS engine.
    Supports .txt, .pdf, and .docx files.
    """
    try:
        # Read file content
        content = await file.read()
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

        return {"text": text, "language_code": lang_code, "suggested_engine": suggested_engine}

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.error(f"Error processing file upload: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tts-preview")
async def tts_preview(request: TTSPreviewRequest):
    """
    Proxy endpoint for TTS preview to avoid CORS issues.
    Routes to appropriate TTS service based on voice.
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
        else:
            # English - use Kokoro endpoint
            endpoint = f"{TTS_API_URL}/v1/audio/speech"
            payload = {
                "model": "kokoro",
                "input": request.text,
                "voice": request.voice,
                "response_format": "mp3",
                "speed": request.speed
            }

        # Make request to TTS API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={
                    'Content-Type': 'application/json',
                    'TTS_API_KEY': TTS_API_KEY
                }
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
    except Exception as e:
        logger.error(f"Error in TTS preview: {e}")
        raise HTTPException(status_code=500, detail=str(e))