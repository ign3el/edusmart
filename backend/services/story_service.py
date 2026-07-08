import os
import logging
logger = logging.getLogger(__name__)
import json
import base64
import time
import io
import wave
import asyncio
import aiohttp
import aiofiles
import requests
from urllib.parse import quote
from typing import Optional, Any, List, Dict
from google import genai
from google.genai import types
from models import StorySchema
from groq import Groq  # Groq API client

class StoryService:
    def __init__(self) -> None:
        # Groq client (primary for story generation)
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.groq_client = Groq(api_key=self.groq_api_key) if self.groq_api_key else None
        self.groq_model = "llama-3.3-70b-versatile"  # Best for long-form content
        self.use_groq = bool(self.groq_client)  # Use Groq if API key available
        
        # Gemini client (fallback)
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        # Recommended models for cost efficiency and high-volume usage
        self.text_model = "gemini-2.0-flash-exp"  # Primary text model
        self.text_model_fallback = "gemini-1.5-flash"  # Fallback when quota exceeded
        self.using_fallback = False  # Track if using fallback model
        self.image_model = "gemini-2.0-flash-exp"  # Best balance for mass users
        self.audio_model = "gemini-2.0-flash-exp-tts"  # Optimized TTS
        # Exponential backoff configuration
        self.base_delay = 1  # Start with 1 second
        self.max_retries = 5  # Maximum retry attempts
        # TPM (Tokens Per Minute) tracking
        self.tpm_limit = 1_000_000  # Gemini 2.0 Flash TPM limit
        self.last_request_tokens = 0  # Track last request size 

    def _exponential_backoff(self, attempt: int) -> int:
        """Calculate exponential backoff delay: base_delay * (2 ^ attempt)."""
        return self.base_delay * (2 ** attempt)

    def _extract_pdf_text(self, file_bytes: bytes) -> str:
        """Extract text from PDF for text-only models like Groq.
        
        Critical: Groq's Llama models are text-only and cannot read PDFs directly.
        We must extract the text first.
        """
        try:
            from pypdf import PdfReader  # Modern library
            import io
            
            pdf_reader = PdfReader(io.BytesIO(file_bytes))
            text_content = []
            
            for page_num, page in enumerate(pdf_reader.pages):
                page_text = page.extract_text()
                if page_text.strip():
                    text_content.append(f"--- Page {page_num + 1} ---\n{page_text}")
            
            full_text = "\n\n".join(text_content)
            
            # Truncate if too long (Groq has token limits)
            max_chars = 100000  # ~25k tokens
            if len(full_text) > max_chars:
                full_text = full_text[:max_chars] + "\n\n[Document truncated due to length]"
            
            return full_text
        except Exception as e:
            print(f"⚠️  PDF text extraction failed: {e}")
            return ""

    def _call_with_exponential_backoff(self, func, *args, **kwargs):
        """Execute API call with exponential backoff retry logic with automatic fallback."""
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e)
                
                # Detect quota exhaustion and switch to fallback model
                if "429" in error_str and "RESOURCE_EXHAUSTED" in error_str:
                    # Check if it's quota vs TPM limit
                    if "quota" in error_str.lower():
                        if not self.using_fallback:
                            # Switch to fallback model
                            print(f"🔄 Primary model quota exceeded. Switching to fallback: {self.text_model_fallback}")
                            self.text_model = self.text_model_fallback
                            self.using_fallback = True
                            # Retry immediately with fallback model
                            time.sleep(2)  # Brief pause before fallback attempt
                            continue
                        else:
                            # Fallback model also exhausted
                            if attempt >= 2:
                                print(f"❌ All models quota exhausted. Please upgrade API tier or wait for reset.")
                                raise Exception("AI Service quota exceeded. Please try again later or contact support.")
                    elif "tokens per minute" in error_str.lower() or "tpm" in error_str.lower():
                        if attempt >= 1:  # TPM limits usually resolve faster
                            print(f"⚠️  TPM limit hit. Consider shortening document or waiting 60 seconds.")
                            # Wait longer for TPM to reset (60 seconds)
                            time.sleep(60)
                
                if attempt < self.max_retries:
                    delay = self._exponential_backoff(attempt)
                    print(f"Attempt {attempt + 1} failed: {str(e)[:100]}... Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"All {self.max_retries + 1} attempts failed. Last error: {e}")
                    raise

    def _ensure_minimum_questions(self, story_json: dict, file_bytes: bytes, grade_level: str) -> dict:
        """Ensure story has minimum 10 quiz questions by generating additional ones if needed."""
        try:
            quiz = story_json.get("quiz", [])
            current_count = len(quiz)
            
            if current_count >= 10:
                print(f"✓ Quiz already has {current_count} questions (minimum met)")
                return story_json
            
            questions_needed = 10 - current_count
            print(f"⚠ Only {current_count} questions found. Generating {questions_needed} additional questions...")
            
            # Extract existing questions for context
            existing_questions_text = "\n".join([f"{i+1}. {q.get('question_text', '')}" for i, q in enumerate(quiz)])
            
            # Generate additional questions
            additional_prompt = f"""You are an expert educational content designer. Generate {questions_needed} additional quiz questions to reach a minimum of 10 questions total.

CONTEXT:
- Grade level: {grade_level}
- Existing questions ({current_count} total):
{existing_questions_text}

REQUIREMENTS:
1. Generate EXACTLY {questions_needed} new questions
2. Each question must test a different learning objective
3. Questions should be diverse and cover concepts NOT already tested
4. Use the same format as existing questions
5. Make questions progressively more challenging
6. Include questions that require critical thinking

OUTPUT: Valid JSON array of {questions_needed} question objects ONLY (no extra text).

{{
  "questions": [
    {{
      "question_number": {current_count + 1},
      "question_text": "Clear question testing a core learning objective",
      "options": ["A. Plausible distractor", "B. Correct answer", "C. Partial truth", "D. Incorrect"],
      "correct_answer": "B",
      "explanation": "Brief explanation connecting to learning concept",
      "source": "generated",
      "document_section": "Additional practice"
    }}
  ]
}}"""

            def _generate_additional_questions():
                return self.client.models.generate_content(
                    model=self.text_model,
                    contents=[
                        types.Part.from_bytes(data=file_bytes, mime_type="application/pdf"),
                        additional_prompt
                    ]
                )
            
            response = self._call_with_exponential_backoff(_generate_additional_questions)
            
            if response and response.text:
                try:
                    # Direct JSON parse (Gemini returns clean JSON)
                    json_obj = json.loads(response.text.strip())
                    if json_obj and "questions" in json_obj:
                        # Add new questions to existing quiz
                        new_questions = json_obj["questions"]
                        for i, q in enumerate(new_questions):
                            q["question_number"] = current_count + i + 1
                        quiz.extend(new_questions)
                        story_json["quiz"] = quiz
                        print(f"✓ Successfully added {len(new_questions)} questions. Total: {len(quiz)}")
                        return story_json
                except json.JSONDecodeError as e:
                    print(f"⚠ Failed to parse additional questions JSON: {e}")
            
            print("⚠ Failed to generate additional questions. Returning existing quiz.")
            return story_json
            
        except Exception as e:
            print(f"⚠ Error generating additional questions: {e}")
            return story_json

    def _validate_story_json(self, story_json: dict) -> tuple[bool, list[str]]:
        """Comprehensive validation of story JSON structure."""
        errors = []
        
        # Required top-level fields
        required_fields = ["title", "description", "grade_level", "subject", "learning_outcome", "scenes", "quiz"]
        for field in required_fields:
            if field not in story_json:
                errors.append(f"Missing required field: {field}")
        
        # Validate scenes
        if "scenes" in story_json:
            scenes = story_json["scenes"]
            if not isinstance(scenes, list):
                errors.append("'scenes' must be an array")
            elif len(scenes) == 0:
                errors.append("'scenes' array is empty")
            else:
                for i, scene in enumerate(scenes):
                    scene_num = i + 1
                    required_scene_fields = ["scene_number", "narrative_text", "image_prompt", "check_for_understanding"]
                    for field in required_scene_fields:
                        if field not in scene or not scene.get(field):
                            errors.append(f"Scene {scene_num}: Missing or empty '{field}'")
        
        # Validate quiz
        if "quiz" in story_json:
            quiz = story_json["quiz"]
            if not isinstance(quiz, list):
                errors.append("'quiz' must be an array")
            elif len(quiz) < 10:
                errors.append(f"'quiz' must have at least 10 questions (found {len(quiz)})")
            else:
                for i, question in enumerate(quiz):
                    q_num = i + 1
                    required_quiz_fields = ["question_number", "question_text", "options", "correct_answer", "explanation"]
                    for field in required_quiz_fields:
                        if field not in question:
                            errors.append(f"Quiz Q{q_num}: Missing '{field}'")
                    
                    if "options" in question and len(question["options"]) != 4:
                        errors.append(f"Quiz Q{q_num}: Must have exactly 4 options")
                    
                    if "correct_answer" in question and question["correct_answer"] not in ["A", "B", "C", "D"]:
                        errors.append(f"Quiz Q{q_num}: correct_answer must be A/B/C/D")
        
        return (len(errors) == 0, errors)

    def process_file_to_story(self, file_path: str, grade_level: str) -> Optional[dict]:
        """Generates story JSON using Groq (primary) or Gemini (fallback). Optimized prompt with validation."""
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()

            # Enhanced prompt - LLM decides scene count based on content complexity
            unified_prompt = f"""Analyze the uploaded document and create an educational story for {grade_level} students.

DOCUMENT ANALYSIS:
1. Extract learning objectives (explicit or inferred from content, topics, vocabulary)
2. List key concepts the document teaches
3. Extract ALL questions/exercises found in document
4. Assess content complexity and determine optimal scene count

STORY REQUIREMENTS:
- **SCENE COUNT**: Determine the ideal number of scenes (typically 4-15) based on:
  * Content complexity and depth
  * Number of distinct concepts to teach
  * Appropriate pacing for {grade_level}
  * Natural narrative flow
- Each scene teaches ONE focused concept from document
- **QUIZ**: Minimum 10 questions, but generate MORE if document has rich content (10-20 questions ideal)
- Use document's exact terminology, definitions, and facts
- Age-appropriate narrative for {grade_level}
- Present concepts in document's logical order

OUTPUT: Valid JSON object ONLY (no markdown, no extra text)

{{
  "title": "Engaging title hinting at learning goal",
  "description": "2-sentence summary: plot hook + educational value",
  "grade_level": "{grade_level}",
  "subject": "Primary subject (Science/Math/History/Language/etc.)",
  "learning_outcome": "After this story, students will be able to [specific skill]",
  "scenes": [
    {{
      "scene_number": 1,
      "narrative_text": "3-4 sentences teaching ONE concept. Active voice, character dialogue, storytelling elements. End with discovery reinforcing concept.",
      "image_prompt": "Detailed 3D Pixar-style scene: [Setting], [Character action], [Educational elements]. Vibrant colors, expressive characters, educational props visible.",
      "check_for_understanding": "Question testing THIS scene's concept"
    }}
  ],
  "quiz": [
    {{
      "question_number": 1,
      "question_text": "Clear question testing core learning objective",
      "options": ["A. Plausible distractor", "B. Correct answer", "C. Partial truth", "D. Incorrect"],
      "correct_answer": "B",
      "explanation": "Brief explanation connecting to story and concept",
      "source": "extracted" | "generated",
      "document_section": "Page/section if extracted"
    }}
  ]
}}

SCENE STRATEGY: Hook → Foundational concepts → Build complexity → Demonstrate mastery → Synthesis

NARRATIVE STYLE: Active voice, vivid verbs, character names/dialogue, vocabulary with context, show don't tell.

IMAGE PROMPTS: Character expressions showing emotion, visual metaphors, educational elements clearly visible.

**IMPORTANT**: 
- Generate the OPTIMAL number of scenes for the content (not a fixed count)
- Generate 10-20 quiz questions based on content richness
- Ensure comprehensive coverage of all document concepts

Output ONLY the JSON object."""

            # Try Groq first (if available)
            if self.use_groq:
                try:
                    print("🚀 Using Groq for story generation...")
                    # CRITICAL FIX: Extract text from PDF (Groq is text-only)
                    pdf_text = self._extract_pdf_text(file_bytes)
                    
                    if not pdf_text:
                        print("⚠️  PDF text extraction failed. Falling back to Gemini...")
                    else:
                        response = self.groq_client.chat.completions.create(
                            model=self.groq_model,
                            messages=[
                                {
                                    "role": "system",
                                    "content": "You are an expert educational content designer. Analyze documents and create engaging educational stories. Always respond with valid JSON only."
                                },
                                {
                                    "role": "user",
                                    "content": f"{unified_prompt}\n\nDOCUMENT TEXT:\n{pdf_text}"
                                }
                            ],
                            temperature=0.7,
                            max_tokens=8000,
                            response_format={"type": "json_object"}  # Native JSON mode
                        )
                        
                        if response.choices and response.choices[0].message.content:
                            # Native JSON mode guarantees valid JSON - just parse it
                            json_obj = json.loads(response.choices[0].message.content)
                            
                            # Validate JSON structure
                            is_valid, errors = self._validate_story_json(json_obj)
                            if is_valid:
                                # Ensure minimum 10 quiz questions
                                json_obj = self._ensure_minimum_questions(json_obj, file_bytes, grade_level)
                                print("✓ Groq generation successful")
                                return json_obj
                            else:
                                print(f"⚠️  Groq JSON validation failed: {errors}")
                                print("Falling back to Gemini...")
                        
                except Exception as e:
                    print(f"⚠️  Groq error: {str(e)[:100]}. Falling back to Gemini...")

            # Fallback to Gemini
            print("🔄 Using Gemini for story generation...")
            def _generate_story_unified():
                return self.client.models.generate_content(
                    model=self.text_model,
                    contents=[
                        types.Part.from_bytes(data=file_bytes, mime_type="application/pdf"),
                        unified_prompt
                    ]
                )
            
            response = self._call_with_exponential_backoff(_generate_story_unified)
            
            if response and response.text:
                try:
                    # Try direct JSON parse first (Gemini often returns clean JSON)
                    json_obj = json.loads(response.text.strip())
                except json.JSONDecodeError:
                    # Fallback: extract JSON from markdown/text
                    import re
                    json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
                    if json_match:
                        try:
                            json_obj = json.loads(json_match.group(0))
                        except json.JSONDecodeError:
                            print(f"STORY ERROR: Could not parse JSON from response.")
                            print(f"Received (first 500 chars): {response.text[:500]}")
                            return None
                    else:
                        print(f"STORY ERROR: No JSON found in response.")
                        return None
                
                # Validate JSON structure
                is_valid, errors = self._validate_story_json(json_obj)
                if not is_valid:
                    print(f"⚠️  JSON validation warnings: {errors}")
                
                # Ensure minimum 10 quiz questions
                json_obj = self._ensure_minimum_questions(json_obj, file_bytes, grade_level)
                print("✓ Gemini generation successful")
                return json_obj
            
            print("STORY ERROR: Received empty or invalid response from GenAI model.")
            return None
        except Exception as e:
            print(f"STORY ERROR: {e}")
            return None

    async def generate_image(self, prompt: str, scene_text: str = "", story_seed: Optional[int] = None, is_mobile: bool = False, scene_num: Optional[int] = None) -> Optional[bytes]:
        """Unified image generation via RunPod ComfyUI FLUX.1-dev with mobile optimization support. Uses story_seed for character consistency.
        
        Args:
            scene_num: Optional scene index for logging purposes.
        """
        # Import time at function start for timing measurements
        import time
        start_time = time.time()
        
        # Build comprehensive, high-quality prompt (adjust for mobile)
        if is_mobile:
            quality_keywords = "masterpiece, best quality, sharp focus, clean linework, vibrant colors"
            style_guide = "children's book illustration style, educational cartoon"
            safety_constraints = "[SAFETY] Family-friendly, age-appropriate"
        else:
            quality_keywords = "masterpiece, best quality, high resolution, sharp focus, detailed faces, clean linework, professional digital art, vibrant colors, clear features, well-proportioned anatomy"
            style_guide = "children's book illustration style, Disney/Pixar quality, educational cartoon, storybook art"
            safety_constraints = "[SAFETY] Family-friendly, age-appropriate, fully clothed characters, wholesome educational content"
        
        # Combine image description with scene narrative for better alignment
        # The prompt (image_description) should already be detailed, but we ensure it matches the scene
        combined_description = prompt
        
        # If scene_text is provided and prompt seems too short/vague, enhance it
        if scene_text and len(prompt.split()) < 15:
            print(f"⚠ Warning: Short image description detected. Enhancing with scene context.")
            combined_description = f"{prompt}. Story context: {scene_text[:200]}"
        
        # Build enhanced prompt with clear priority hierarchy
        enhanced_prompt = f"{quality_keywords}. {style_guide}. {safety_constraints}. MAIN VISUAL: {combined_description}"
        
        # Remove problematic terms
        enhanced_prompt = enhanced_prompt.replace("distorted", "clear")
        enhanced_prompt = enhanced_prompt.replace("blurry", "sharp")
        enhanced_prompt = enhanced_prompt.replace("ugly", "beautiful")

        # Use ComfyUI FLUX endpoint for high-quality image generation
        endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID_FLUX")
        api_key = os.getenv("RUNPOD_KEY")

        if not endpoint_id or not api_key:
            print("RUNPOD_ENDPOINT_ID_FLUX or RUNPOD_KEY not set; cannot generate image")
            return None

        # Simple spend guard (estimates). Reset monthly and block when over cap.
        cap_aed = float(os.getenv("RUNPOD_MONTHLY_CAP_AED", "25"))
        est_cost_per_image = float(os.getenv("RUNPOD_COST_AED_PER_IMAGE", "0.02"))  # FLUX is slightly more expensive
        usage_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runpod_usage.json")
        month_key = time.strftime("%Y-%m")

        def load_usage():
            if os.path.exists(usage_file):
                try:
                    with open(usage_file, "r") as f:
                        return json.load(f)
                except Exception:
                    return {"month": month_key, "images": 0}
            return {"month": month_key, "images": 0}

        def save_usage(data):
            try:
                with open(usage_file, "w") as f:
                    json.dump(data, f)
            except Exception as e:
                print(f"Usage file save failed: {e}")

        usage = load_usage()
        if usage.get("month") != month_key:
            usage = {"month": month_key, "images": 0}

        projected_cost = (usage.get("images", 0) + 1) * est_cost_per_image
        if cap_aed > 0 and projected_cost > cap_aed:
            print(f"⚠️  Monthly cap reached ({projected_cost:.2f} AED \u003e {cap_aed} AED). Skipping image generation.")
            return None
        
        # Start timing for image generation
        start_time = time.time()

        # Use story-specific seed for character consistency, fall back to environment variable
        seed_value = story_seed
        if not seed_value:
            seed_env = os.getenv("RUNPOD_SEED")
            if seed_env:
                try:
                    seed_value = int(seed_env)
                except ValueError:
                    pass
        
        # Mobile optimization: adjust dimensions and sampling
        width = 512 if is_mobile else 768
        height = 512 if is_mobile else 768
        steps = 15 if is_mobile else 20
        sampler = "euler_ancestral" if is_mobile else "euler"
        
        # Negative prompt to avoid common AI image issues
        negative_prompt = "blurry, distorted, ugly, bad anatomy, bad proportions, extra limbs, malformed hands, duplicate faces, low quality, worst quality, deformed, mutated, disfigured, poorly drawn, bad art, amateur"
        
        # ComfyUI workflow for FLUX.1-dev
        # This is a standard text-to-image workflow structure for FLUX
        workflow = {
            "6": {
                "inputs": {
                    "text": enhanced_prompt,
                    "clip": ["30", 1]  # Clip output from checkpoint loader
                },
                "class_type": "CLIPTextEncode"
            },
            "8": {
                "inputs": {
                    "samples": ["31", 0],
                    "vae": ["30", 2]
                },
                "class_type": "VAEDecode"
            },
            "9": {
                "inputs": {
                    "filename_prefix": "flux_mobile_output" if is_mobile else "flux_output",
                    "images": ["8", 0]
                },
                "class_type": "SaveImage"
            },
            "27": {
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1
                },
                "class_type": "EmptyLatentImage"
            },
            "30": {
                "inputs": {
                    "ckpt_name": "flux1-dev-fp8.safetensors"
                },
                "class_type": "CheckpointLoaderSimple"
            },
            "31": {
                "inputs": {
                    "seed": seed_value if seed_value else 42,
                    "steps": steps,
                    "cfg": 1.0,
                    "sampler_name": sampler,
                    "scheduler": "simple",
                    "denoise": 1.0,
                    "model": ["30", 0],
                    "positive": ["6", 0],
                    "negative": ["33", 0],
                    "latent_image": ["27", 0]
                },
                "class_type": "KSampler"
            },
            "33": {
                "inputs": {
                    "text": negative_prompt,
                    "clip": ["30", 1]  # Clip output from checkpoint loader
                },
                "class_type": "CLIPTextEncode"
            }
        }
        
        # FLUX.1-dev payload for ComfyUI with workflow
        payload_input: dict[str, Any] = {
            "workflow": workflow
        }

        url = f"https://api.runpod.ai/v2/{endpoint_id}/run"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            timeout_seconds = 90 if is_mobile else 120  # Mobile generates faster
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json={"input": payload_input}, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        print(f"RunPod FLUX returned {resp.status}: {text[:120]}")
                        return None
                    data = await resp.json()            # If synchronous output is returned directly
                
                if data.get("status") == "COMPLETED" and data.get("output"):
                    output = data.get("output")
                # Otherwise poll status endpoint
                elif data.get("id"):
                    request_id = data["id"]
                    status_url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{request_id}"
                    output = None
                    max_polls = 30 if is_mobile else 40  # Mobile should be faster
                    for _ in range(max_polls):
                        await asyncio.sleep(2)
                        async with session.get(status_url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as status_resp:
                                if status_resp.status != 200:
                                    continue
                                status_data = await status_resp.json()
                                if status_data.get("status") == "COMPLETED" and status_data.get("output"):
                                    output = status_data.get("output")
                                    break
                        
                        if status_data.get("status") in {"FAILED", "CANCELLED"}:
                            print(f"RunPod job failed: {status_data}")
                            return None
                else:
                    print("RunPod response missing output/id")
                    return None

            image_bytes = None

            def decode_b64(candidate: Any) -> Optional[bytes]:
                if isinstance(candidate, str):
                    try:
                        return base64.b64decode(candidate)
                    except Exception:
                        return None
                return None

            def find_b64_in_obj(obj: Any) -> Optional[bytes]:
                """Recursively search for the first plausible base64 string in nested dict/list structures."""
                if isinstance(obj, str):
                    if len(obj) > 100:  # heuristic: images are long strings
                        decoded = decode_b64(obj)
                        if decoded:
                            return decoded
                    return None
                if isinstance(obj, list):
                    for item in obj:
                        found = find_b64_in_obj(item)
                        if found:
                            return found
                    return None
                if isinstance(obj, dict):
                    # common keys first
                    for key in ["image", "image_base64", "images", "output", "data", "result"]:
                        if key in obj:
                            found = find_b64_in_obj(obj[key])
                            if found:
                                return found
                    # fallback: scan all values
                    for val in obj.values():
                        found = find_b64_in_obj(val)
                        if found:
                            return found
                return None

            if isinstance(output, str):
                image_bytes = decode_b64(output)

            elif isinstance(output, dict):
                # Common patterns: {"images": [{"image": b64}]}, {"image": b64}, {"image_base64": b64}, {"output": b64 or [b64]}
                if "images" in output and isinstance(output.get("images"), list) and output["images"]:
                    first_image = output["images"][0]
                    if isinstance(first_image, dict):
                        # Handle {"images": [{"image": "base64..."}]}
                        b64 = first_image.get("image") or first_image.get("image_base64")
                        image_bytes = decode_b64(b64)
                    else:
                        # Handle {"images": ["base64..."]}
                        image_bytes = decode_b64(first_image)
                if not image_bytes:
                    b64 = output.get("image") or output.get("image_base64")
                    image_bytes = decode_b64(b64)
                if not image_bytes:
                    inner_output = output.get("output")
                    if isinstance(inner_output, str):
                        image_bytes = decode_b64(inner_output)
                    elif isinstance(inner_output, list) and inner_output:
                        first = inner_output[0]
                        if isinstance(first, dict):
                            b64 = first.get("image") or first.get("image_base64")
                            image_bytes = decode_b64(b64)
                        else:
                            image_bytes = decode_b64(first)
                    elif isinstance(inner_output, dict):
                        b64 = inner_output.get("image") or inner_output.get("image_base64")
                        if not b64 and isinstance(inner_output.get("images"), list) and inner_output["images"]:
                            first = inner_output["images"][0]
                            if isinstance(first, dict):
                                b64 = first.get("image") or first.get("image_base64")
                            else:
                                b64 = first
                        image_bytes = decode_b64(b64)

            elif isinstance(output, list) and output:
                first = output[0]
                if isinstance(first, str):
                    image_bytes = decode_b64(first)
                elif isinstance(first, dict):
                    b64 = first.get("image") or first.get("image_base64") or first.get("output")
                    if not b64 and isinstance(first.get("images"), list) and first["images"]:
                        b64 = first["images"][0]
                    image_bytes = decode_b64(b64)

            # Final fallback: search recursively for any base64 string in the output
            if not image_bytes:
                image_bytes = find_b64_in_obj(output)

            if image_bytes:
                usage["images"] = usage.get("images", 0) + 1
                save_usage(usage)
                mode_str = "mobile" if is_mobile else "desktop"
                # Get image dimensions (assuming PNG header)
                resolution = "1024x1024" if not is_mobile else "512x512"
                elapsed = time.time() - start_time
                print(f"✓ Image generated in {elapsed:.2f}s via RunPod FLUX/ComfyUI ({mode_str}) | Size: {len(image_bytes):,} bytes | Resolution: {resolution}")
                return image_bytes

            print("RunPod FLUX/ComfyUI output could not be parsed")
            return None
        except Exception as e:
            print(f"RunPod FLUX/ComfyUI error: {str(e)[:120]}")
            return None
    
    async def generate_images_parallel(
        self,
        scenes: List[dict],
        story_seed: int,
        max_workers: int = 4,
        is_mobile: bool = False,
        start_index: int = 0
    ) -> Dict[int, bytes]:
        """Generate all scene images in parallel with bounded concurrency.
        
        Optimized for RunPod with 4 workers:
        - Uses asyncio.Semaphore to limit concurrent requests
        - Prevents cold boot overhead
        - Maximizes throughput while respecting worker limits
        
        Args:
            scenes: List of scene dictionaries with 'image_prompt' field
            story_seed: Seed for character consistency across all images
            max_workers: Max concurrent image generations
            is_mobile: Generate mobile-optimized images (512x512)
            start_index: Starting scene index (default 0)
        
        Returns:
            Dictionary mapping scene number to image bytes
        """
        semaphore = asyncio.Semaphore(max_workers)
        
        async def bounded_generate(i: int, scene: dict):
            """Generate single image with semaphore control"""
            # Calculate actual scene index
            scene_num = start_index + i
            async with semaphore:
                await asyncio.sleep(i * 0.5)
                logger.info(f"🎨 Starting image generation for scene {scene_num}...")
                img_bytes = await self.generate_image(
                    scene['image_prompt'], 
                    story_seed=story_seed, 
                    is_mobile=is_mobile,
                    scene_num=scene_num
                )
                return scene_num, img_bytes
        
        print(f"🎨 Starting parallel image generation for {len(scenes)} scenes (max_workers={max_workers}, mobile={is_mobile})")
        
        # Create tasks for all scenes
        tasks = [
            bounded_generate(i, scene)
            for i, scene in enumerate(scenes)
        ]
        
        # Run all tasks concurrently (semaphore limits actual parallelism)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Build result dictionary, filtering out failures
        images = {}
        for i, result in enumerate(results):
            if result and not isinstance(result, Exception):
                # Unpack tuple: bounded_generate returns (scene_num, img_bytes)
                if isinstance(result, tuple):
                    scene_num, img_bytes = result
                    if img_bytes:
                        images[scene_num] = img_bytes
                else:
                    images[i] = result
            else:
                error_msg = str(result) if isinstance(result, Exception) else "Unknown error"
                print(f"⚠️  Image generation failed for scene {i}: {error_msg[:100]}")
        
        success_rate = (len(images) / len(scenes)) * 100 if scenes else 0
        print(f"✓ Generated {len(images)}/{len(scenes)} images successfully ({success_rate:.1f}%)")
        
        return images



    def generate_scene_priority(self, file_path: str, grade_level: str, scene_number: int) -> Optional[dict]:
        """Generate a single scene with priority for immediate display."""
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()

            priority_prompt = f"""Generate ONLY Scene {scene_number} for immediate display. Focus on ONE key concept.

REQUIREMENTS:
1. Output: Valid JSON object ONLY
2. Scene must be self-contained and teach one concept
3. Include narrative_text, image_prompt, and check_for_understanding

{{
  "scene_number": {scene_number},
  "narrative_text": "3-4 sentences teaching ONE key concept. Use storytelling elements.",
  "image_prompt": "Detailed scene description with educational elements",
  "check_for_understanding": "Question testing THIS scene's concept"
}}"""

            def _generate_scene():
                return self.client.models.generate_content(
                    model=self.text_model,
                    contents=[
                        types.Part.from_bytes(data=file_bytes, mime_type="application/pdf"),
                        priority_prompt
                    ]
                )
            response = self._call_with_exponential_backoff(_generate_priority_scene)
        
            if response and response.text:
                try:
                    # Direct JSON parse
                    json_obj = json.loads(response.text.strip())
                    if json_obj and "scene" in json_obj:
                        scene = json_obj["scene"]
                        print(f"✓ Priority scene {scene_number} generated")
                        return scene
                except json.JSONDecodeError as e:
                    print(f"⚠ Failed to parse scene JSON: {e}")
            
            return None
        except Exception as e:
            print(f"Priority scene generation error: {e}")
            return None

    # TTS generation removed - now handled by external Chatterbox service via HTTP
    # See services/chatterbox_client.py for TTS implementation
    
    # ==================== PROGRESSIVE TTS GENERATION ====================
    
    async def generate_progressive_tts(
        self,
        story_id: str,
        scenes: List[dict],
        batch_size: int = 2,
        max_threads_per_tts: int = 1
    ) -> None:
        """Generate TTS for scenes in parallel batches with CPU management.
        
        Optimized for 3 OCPU VPS:
        - 2 parallel TTS (batch_size=2)
        - 1 thread per TTS (max_threads_per_tts=1)
        - Leaves 4 cores free for API requests
        
        Args:
            story_id: Story identifier for caching
            scenes: List of scenes (excluding Scene 0 which is already generated)
            batch_size: Number of parallel TTS requests (default: 2)
            max_threads_per_tts: Max CPU threads per TTS (default: 1 for CPU management)
        """
        total_scenes = len(scenes)
        print(f"🎤 Starting progressive TTS generation for {total_scenes} scenes (batch_size={batch_size})")
        
        for i in range(0, total_scenes, batch_size):
            batch = scenes[i:i+batch_size]
            batch_num = i // batch_size + 1
            
            # Generate batch in parallel
            tasks = [
                self._generate_and_cache_tts(
                    story_id,
                    i + idx + 1,  # Scene 0 already done, so scenes 1-N
                    scene['narrative_text'],
                    max_threads=max_threads_per_tts
                )
                for idx, scene in enumerate(batch)
            ]
            
            # Run batch concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Count successes
            successes = sum(1 for r in results if r is True)
            
            # Update progress
            completed = min(i + batch_size, total_scenes)
            await self._update_tts_status(story_id, completed, total_scenes)
            
            print(f"✓ TTS batch {batch_num}/{(total_scenes + batch_size - 1) // batch_size} complete: {successes}/{len(batch)} scenes successful")
    
    async def _generate_and_cache_tts(
        self,
        story_id: str,
        scene_num: int,
        text: str,
        max_threads: int = 1
    ) -> bool:
        """Generate TTS and cache the result.
        
        Args:
            story_id: Story identifier
            scene_num: Scene number (0-indexed)
            text: Narrative text to convert to speech
            max_threads: Max CPU threads for TTS generation
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Start timing
            import time
            start_time = time.time()
            
            # Use the original working kokoro_client
            from services.kokoro_client import generate_tts
            
            # Generate TTS using proven kokoro client
            audio_bytes = await asyncio.to_thread(
                generate_tts,
                text=text,
                voice="af_sarah",  # Default voice
                speed=1.0
            )
            
            if audio_bytes:
                # Cache audio
                await self._cache_audio(story_id, scene_num, audio_bytes)
                elapsed = time.time() - start_time
                duration_seconds = len(audio_bytes) / (176 * 1024)
                print(f"✓ Audio for Scene {scene_num} generated in {elapsed:.2f}s via Kokoro | Story: {story_id[:8]}... | Size: {len(audio_bytes):,} bytes | Duration: ~{duration_seconds:.1f}s")
                return True
            
            print(f"⚠️  TTS generation returned no audio for scene {scene_num}")
            return False
            
        except Exception as e:
            print(f"⚠️  TTS generation failed for scene {scene_num}: {e}")
            return False
    
    async def _cache_audio(self, story_id: str, scene_num: int, audio_bytes: bytes) -> None:
        """Cache audio bytes to file system.
        
        Args:
            story_id: Story identifier
            scene_num: Scene number
            audio_bytes: Audio data to cache
        """
        try:
            # Create cache directory
            cache_dir = "outputs/audio_cache"
            os.makedirs(cache_dir, exist_ok=True)
            
            # Save audio file
            file_path = os.path.join(cache_dir, f"audio_{story_id}_{scene_num}.mp3")
            
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(audio_bytes)
            
            print(f"✓ Cached audio for Scene {scene_num}: {file_path}")
            
        except Exception as e:
            print(f"⚠️  Failed to cache audio for scene {scene_num}: {e}")
    
    async def _update_tts_status(
        self,
        story_id: str,
        completed: int,
        total: int
    ) -> None:
        """Update TTS generation status for real-time progress tracking.
        
        Args:
            story_id: Story identifier
            completed: Number of scenes completed
            total: Total number of scenes
        """
        try:
            # Create status object
            status = {
                "tts_progress": f"{completed}/{total}",
                "scenes_ready": list(range(completed + 1)),  # +1 for Scene 0
                "timestamp": time.time(),
                "percentage": int((completed / total) * 100) if total > 0 else 0
            }
            
            # Save to file (can be replaced with Redis/database)
            status_dir = "outputs/status"
            os.makedirs(status_dir, exist_ok=True)
            
            status_file = os.path.join(status_dir, f"{story_id}.json")
            
            async with aiofiles.open(status_file, 'w') as f:
                await f.write(json.dumps(status, indent=2))
            
            print(f"✓ Updated TTS status: {completed}/{total} scenes ready ({status['percentage']}%)")
            
        except Exception as e:
            print(f"⚠️  Failed to update TTS status: {e}")
    
    async def get_tts_status(self, story_id: str) -> Dict:
        """Get current TTS generation status.
        
        Args:
            story_id: Story identifier
        
        Returns:
            Status dictionary with progress information
        """
        try:
            status_file = f"outputs/status/{story_id}.json"
            
            if not os.path.exists(status_file):
                return {
                    "tts_progress": "0/9",
                    "scenes_ready": [0],
                    "percentage": 0
                }
            
            async with aiofiles.open(status_file, 'r') as f:
                content = await f.read()
                return json.loads(content)
                
        except Exception as e:
            print(f"⚠️  Failed to get TTS status: {e}")
            return {
                "tts_progress": "unknown",
                "scenes_ready": [0],
                "percentage": 0,
                "error": str(e)
            }
