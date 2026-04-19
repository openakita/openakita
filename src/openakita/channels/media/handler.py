"""
Media Handler

Processes various types of media content:
- Speech-to-Text
- Image understanding (Vision)
- File content extraction (PDF, Office, etc.)
"""

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Any

from ..types import MediaFile, MediaStatus

logger = logging.getLogger(__name__)


class MediaHandler:
    """
    Media Handler

    Provides a unified media processing interface, supporting:
    - Speech-to-text transcription
    - Image description / understanding
    - Document content extraction
    """

    # Whisper sizes that support a dedicated .en model (large has no .en variant)
    _EN_MODEL_SIZES = {"tiny", "base", "small", "medium"}

    def __init__(
        self,
        brain: Any | None = None,
        whisper_model: str = "medium",
        whisper_language: str = "zh",
        enable_ocr: bool = True,
    ):
        """
        Args:
            brain: Brain instance (used for image understanding).
            whisper_model: Whisper model size (tiny, base, small, medium, large).
            whisper_language: Speech recognition language (zh/en/auto/other language codes).
            enable_ocr: Whether to enable OCR.
        """
        self.brain = brain
        self.whisper_language = whisper_language.lower().strip()
        # For English, automatically switch to the smaller, faster .en model when available
        if self.whisper_language == "en" and whisper_model in self._EN_MODEL_SIZES:
            self.whisper_model = f"{whisper_model}.en"
        else:
            self.whisper_model = whisper_model
        self.enable_ocr = enable_ocr

        # Lazily loaded models
        self._whisper = None
        self._whisper_loaded = False
        self._whisper_unavailable = False  # ImportError -> no retry within this process
        self._ocr = None

    async def preload_whisper(self) -> bool:
        """
        Preload the Whisper model.

        Called at system startup to avoid delay on first use.

        Returns:
            Whether the model was loaded successfully.
        """
        if self._whisper_loaded or self._whisper_unavailable:
            return self._whisper_loaded

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_whisper_sync)
            return self._whisper is not None
        except Exception as e:
            logger.error(f"Failed to preload Whisper: {e}")
            return False

    def _load_whisper_sync(self) -> None:
        """Synchronously load the Whisper model."""
        if self._whisper_loaded or self._whisper_unavailable:
            return

        try:
            import whisper

            logger.info(f"Loading Whisper model '{self.whisper_model}'...")
            self._whisper = whisper.load_model(self.whisper_model)
            self._whisper_loaded = True
            logger.info(f"Whisper model '{self.whisper_model}' loaded successfully")
        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("whisper")
            logger.warning(f"Whisper unavailable (no retry within this process): {hint}")
            self._whisper_unavailable = True
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")

    async def process(self, media: MediaFile) -> MediaFile:
        """
        Process a media file.

        Automatically selects the processing method based on the file type.

        Args:
            media: The media file.

        Returns:
            The processed media file (with transcription/description/extracted_text).
        """
        if not media.local_path:
            logger.warning(f"Media {media.id} has no local path, skipping processing")
            return media

        try:
            if media.is_audio:
                await self.transcribe_audio(media)
            elif media.is_image:
                await self.describe_image(media)
            elif media.is_document:
                await self.extract_text(media)

            media.status = MediaStatus.PROCESSED

        except Exception as e:
            logger.error(f"Failed to process media {media.id}: {e}")

        return media

    async def transcribe_audio(self, media: MediaFile) -> str:
        """
        Speech-to-text transcription.

        Uses OpenAI Whisper or a cloud service.

        Args:
            media: The audio file.

        Returns:
            The transcribed text.
        """
        if not media.local_path:
            raise ValueError("Media has no local path")

        logger.info(f"Transcribing audio: {media.filename}")

        try:
            # Try local Whisper first
            transcription = await self._transcribe_with_whisper(media.local_path)
        except Exception as e:
            logger.warning(f"Local Whisper failed: {e}, trying fallback")
            # Fallback: use a simple description
            transcription = f"[Voice message, duration {media.duration or 'unknown'} seconds]"

        media.transcription = transcription
        return transcription

    async def _transcribe_with_whisper(self, audio_path: str) -> str:
        """Transcribe using local Whisper."""
        if not self._whisper_loaded and not self._whisper_unavailable:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_whisper_sync)

        if self._whisper is None:
            raise RuntimeError(
                "Whisper model not available. "
                "Make sure openai-whisper is installed: pip install openai-whisper"
            )

        from openakita.channels.media.audio_utils import (
            ensure_whisper_compatible,
            load_wav_as_numpy,
        )

        compatible_path = ensure_whisper_compatible(audio_path)

        kwargs: dict = {}
        if self.whisper_language and self.whisper_language != "auto":
            kwargs["language"] = self.whisper_language

        def _run_whisper():
            # For converted WAV files, try loading directly via numpy to bypass ffmpeg dependency
            if compatible_path.endswith(".wav"):
                audio_array = load_wav_as_numpy(compatible_path)
                if audio_array is not None:
                    return self._whisper.transcribe(audio_array, **kwargs)
            return self._whisper.transcribe(compatible_path, **kwargs)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_whisper)

        return result["text"]

    async def describe_image(self, media: MediaFile) -> str:
        """
        Image understanding / description.

        Uses Claude Vision or another multimodal model.

        Args:
            media: The image file.

        Returns:
            The image description.
        """
        if not media.local_path:
            raise ValueError("Media has no local path")

        logger.info(f"Describing image: {media.filename}")

        try:
            if self.brain:
                # Use Claude Vision
                description = await self._describe_with_vision(media.local_path)
            else:
                # Fallback: use OCR
                description = await self._ocr_image(media.local_path)
        except Exception as e:
            logger.warning(f"Image description failed: {e}")
            description = f"[Image: {media.filename}]"

        media.description = description
        return description

    async def _describe_with_vision(self, image_path: str) -> str:
        """Describe an image using Claude Vision."""
        import base64

        # Read the image and convert to base64
        with open(image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode()

        # Determine MIME type
        mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"

        # Call Claude
        response = await self.brain.client.messages.create(
            model=self.brain.model,
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Briefly describe the content of this image.",
                        },
                    ],
                }
            ],
        )

        return response.content[0].text

    async def _ocr_image(self, image_path: str) -> str:
        """Extract text from an image using OCR."""
        if not self.enable_ocr:
            return ""

        try:
            # Try using pytesseract
            import pytesseract
            from PIL import Image

            image = Image.open(image_path)
            text = pytesseract.image_to_string(image, lang="chi_sim+eng")

            return text.strip() if text.strip() else "[Image has no recognizable text]"

        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("pytesseract")
            logger.warning(f"OCR unavailable: {hint}")
            return ""
        except Exception as e:
            logger.warning(f"OCR failed: {e}")
            return ""

    async def extract_text(self, media: MediaFile) -> str:
        """
        Extract file content.

        Supports:
        - PDF
        - Office documents (docx, xlsx, pptx)
        - Text files

        Args:
            media: The file.

        Returns:
            The extracted text.
        """
        if not media.local_path:
            raise ValueError("Media has no local path")

        logger.info(f"Extracting text from: {media.filename}")

        path = Path(media.local_path)
        extension = path.suffix.lower()

        try:
            if extension == ".pdf":
                text = await self._extract_pdf(path)
            elif extension == ".docx":
                text = await self._extract_docx(path)
            elif extension == ".xlsx":
                text = await self._extract_xlsx(path)
            elif extension == ".pptx":
                text = await self._extract_pptx(path)
            elif extension in (".txt", ".md", ".json", ".py", ".js", ".html", ".css"):
                text = path.read_bytes().decode("utf-8", errors="ignore")
            else:
                text = f"[File: {media.filename}, content extraction not supported]"
        except Exception as e:
            logger.warning(f"Text extraction failed: {e}")
            text = f"[File: {media.filename}, extraction failed]"

        media.extracted_text = text
        return text

    async def _extract_pdf(self, path: Path) -> str:
        """Extract PDF content."""
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(str(path))
            text_parts = []

            for page in doc:
                text_parts.append(page.get_text())

            doc.close()
            return "\n".join(text_parts)

        except ImportError:
            # Fallback approach
            try:
                import pypdf

                reader = pypdf.PdfReader(str(path))
                text_parts = []

                for page in reader.pages:
                    text_parts.append(page.extract_text())

                return "\n".join(text_parts)

            except ImportError:
                from openakita.tools._import_helper import import_or_hint

                hint = import_or_hint("fitz")
                raise ImportError(f"PDF extraction unavailable: {hint}")

    async def _extract_docx(self, path: Path) -> str:
        """Extract Word document content."""
        try:
            from docx import Document

            doc = Document(str(path))
            text_parts = []

            for para in doc.paragraphs:
                text_parts.append(para.text)

            return "\n".join(text_parts)

        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("docx")
            raise ImportError(f"DOCX extraction unavailable: {hint}")

    async def _extract_xlsx(self, path: Path) -> str:
        """Extract Excel content."""
        try:
            import openpyxl

            wb = openpyxl.load_workbook(str(path), read_only=True)
            text_parts = []

            for sheet in wb.worksheets:
                text_parts.append(f"## Sheet: {sheet.title}")

                for row in sheet.iter_rows(max_row=100):  # Limit row count
                    cells = [str(cell.value) if cell.value else "" for cell in row]
                    text_parts.append(" | ".join(cells))

            wb.close()
            return "\n".join(text_parts)

        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("openpyxl")
            raise ImportError(f"XLSX extraction unavailable: {hint}")

    async def _extract_pptx(self, path: Path) -> str:
        """Extract PowerPoint content."""
        try:
            from pptx import Presentation

            prs = Presentation(str(path))
            text_parts = []

            for i, slide in enumerate(prs.slides):
                text_parts.append(f"## Slide {i + 1}")

                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        text_parts.append(shape.text)

            return "\n".join(text_parts)

        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("pptx")
            raise ImportError(f"PPTX extraction unavailable: {hint}")
