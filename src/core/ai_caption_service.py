"""Gemini AI Caption Service for reel retitling and description generation."""

from __future__ import annotations

import json
import importlib
import logging
from typing import Callable, Optional

GENAI_MODULE = None
GENAI_TYPES_MODULE = None

# Default model to use (configurable, but defaults to a current stable fast model)
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"

# List of fallback models to try if the configured one is unavailable
FALLBACK_GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

try:
    GENAI_MODULE = importlib.import_module("google.genai")
    GENAI_TYPES_MODULE = importlib.import_module("google.genai.types")
    GENAI_AVAILABLE = True
    GENAI_IMPORT_ERROR = None
except Exception as e:
    GENAI_AVAILABLE = False
    GENAI_IMPORT_ERROR = str(e)


class AICaptionService:
    """Service for generating reel captions using Gemini API."""

    def __init__(
        self, 
        api_key: str, 
        log_fn: Callable[[str], None] | None = None,
        model_name: str | None = None
    ):
        """Initialize the AI caption service.

        Args:
            api_key: Gemini API key
            log_fn: Optional logging callback
            model_name: Gemini model name to use (if None, defaults to DEFAULT_GEMINI_MODEL)
        """
        self.api_key = (api_key or "").strip()
        self._log = log_fn or (lambda m: None)
        self.configured_model = model_name or DEFAULT_GEMINI_MODEL
        self._request_timeout = 30  # seconds
        
        # Initialization state
        self.client = None
        self.model = None
        self.resolved_model = None  # The actual model used after validation/fallback
        self.initialized = False
        self.init_error = None
        
        # Perform initialization
        self._initialize()

    def _resolve_working_gemini_model(self) -> str | None:
        """Resolve the best available Gemini model for text generation.
        
        Tries the configured model first, then falls back to known working models.
        
        Returns:
            Model name string if a working model is found, None otherwise
        """
        if not self.client:
            return None
        
        try:
            # Try to list available models
            self._log(f"Validating Gemini models...")
            available_models = list(GENAI_MODULE.models.list_models())
            
            # Extract model names that support generateContent
            valid_models = []
            for model in available_models:
                model_name = model.name.split("/")[-1]  # Extract name from "models/gemini-2.0-flash"
                # Check if this model supports generateContent
                if hasattr(model, "supported_generation_methods"):
                    if "generateContent" in model.supported_generation_methods:
                        valid_models.append(model_name)
            
            self._log(f"Available models supporting generateContent: {valid_models}")
            
            # First, try the configured model
            if self.configured_model in valid_models:
                self._log(f"✓ Configured model '{self.configured_model}' is available")
                return self.configured_model
            
            # Fall back to the first available model in the fallback list
            self._log(f"⚠ Configured model '{self.configured_model}' not available, trying fallback models...")
            for fallback_model in FALLBACK_GEMINI_MODELS:
                if fallback_model in valid_models:
                    self._log(f"✓ Falling back to model '{fallback_model}'")
                    return fallback_model
            
            # If no fallback in the list matches, use the first valid model
            if valid_models:
                best_model = valid_models[0]
                self._log(f"✓ Falling back to first available model '{best_model}'")
                return best_model
            
            self._log(f"✗ No models found that support generateContent")
            return None
            
        except Exception as exc:
            self._log(f"⚠ Could not list models: {type(exc).__name__}: {str(exc)}")
            self._log(f"⚠ Will attempt to use configured model directly: {self.configured_model}")
            return self.configured_model  # Fall back to using configured model directly

    def _initialize(self) -> None:
        """Initialize Gemini client and model with detailed error tracking."""
        # Step 1: Check if API key is provided
        if not self.api_key:
            self.initialized = False
            self.init_error = "Gemini API key is empty"
            self._log(f"✗ Gemini init failed: {self.init_error}")
            return
        
        self._log(f"Initializing Gemini service...")
        self._log(f"Configured Gemini model: {self.configured_model}")
        self._log(f"Gemini API key present: yes (masked: {self._mask_api_key()})")
        
        # Step 2: Check if SDK is available
        if not GENAI_AVAILABLE:
            self.initialized = False
            self.init_error = f"google.genai package not installed or import failed: {GENAI_IMPORT_ERROR}"
            self._log(f"✗ Gemini init failed: {self.init_error}")
            return
        
        # Step 3: Try to create client and configure model
        try:
            self.client = GENAI_MODULE.Client(api_key=self.api_key)
            
            # Resolve the best available model
            resolved = self._resolve_working_gemini_model()
            if not resolved:
                self.initialized = False
                self.init_error = "No suitable Gemini model found for generateContent"
                self._log(f"✗ Gemini init failed: {self.init_error}")
                self.client = None
                return
            
            self.resolved_model = resolved
            self.model = resolved
            self.initialized = True
            self.init_error = None
            self._log(f"✓ Gemini service initialized successfully")
            self._log(f"   Using model: {self.resolved_model}")
            if self.resolved_model != self.configured_model:
                self._log(f"   (configured model '{self.configured_model}' was not available)")
        except Exception as exc:
            self.initialized = False
            self.init_error = f"{type(exc).__name__}: {str(exc)}"
            self._log(f"✗ Gemini init failed: {self.init_error}")
            self.client = None
            self.model = None

    def _mask_api_key(self) -> str:
        """Return a masked version of the API key for logging."""
        if not self.api_key or len(self.api_key) < 8:
            return "***"
        return f"{self.api_key[:6]}***{self.api_key[-4:]}"

    def is_ready(self) -> tuple[bool, str | None]:
        """Check if the service is ready to generate captions.
        
        Returns:
            (True, None) if initialized and ready
            (False, error_message) if not ready
        """
        return self.initialized, self.init_error

    def test_connection(self) -> tuple[bool, str | None]:
        """Test the Gemini API connection.

        Returns:
            (True, None) if successful
            (False, error_message) if failed
        """
        # Check initialization status first
        if not self.initialized:
            return False, f"Gemini model not initialized: {self.init_error or 'unknown error'}"

        try:
            # Simple test request
            prompt = "Say 'OK' in exactly one word."
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=GENAI_TYPES_MODULE.GenerateContentConfig(temperature=0.3),
            )
            if response and response.text:
                self._log(f"✓ Gemini test successful: {self._mask_api_key()}")
                return True, None
            else:
                return False, "Empty response from Gemini"
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {str(exc)}"
            self._log(f"✗ Gemini test failed: {error_msg} (key: {self._mask_api_key()})")
            return False, error_msg

    def generate_reel_caption(
        self,
        source_text: str,
        target_language: str = "English",
    ) -> tuple[bool, dict | None, str | None]:
        """Generate reel title and description from source text using Gemini.

        Args:
            source_text: Source text (usually filename-based caption)
            target_language: Target language for output

        Returns:
            (True, {"title": "...", "description": "..."}, None) on success
            (False, None, error_message) on failure
        """
        # Check initialization status first
        if not self.initialized:
            detailed_error = f"Gemini model not initialized: {self.init_error or 'unknown error'}"
            return False, None, detailed_error

        if not source_text or not source_text.strip():
            return False, None, "Source text is empty"

        try:
            prompt = self._build_prompt(source_text, target_language)
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=GENAI_TYPES_MODULE.GenerateContentConfig(temperature=0.7),
            )

            if not response or not response.text:
                return False, None, "Empty response from Gemini"

            # Parse JSON response
            result = self._parse_response(response.text)
            if result:
                self._log(
                    f"✓ Gemini caption generated: "
                    f"title='{result.get('title', '')[:30]}...' "
                    f"lang={target_language}"
                )
                return True, result, None
            else:
                return False, None, "Failed to parse Gemini response as JSON"

        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {str(exc)}"
            self._log(f"✗ Gemini call failed: {error_msg} (key: {self._mask_api_key()})")
            return False, None, error_msg

    def _build_prompt(self, source_text: str, target_language: str) -> str:
        """Build the Gemini prompt for caption generation.

        Args:
            source_text: Source filename or caption text
            target_language: Target language for output

        Returns:
            Prompt string
        """
        return f"""You are a content creator helping to generate engaging reel titles and descriptions.

Given the following source text (usually a filename or caption):
"{source_text}"

Please:
1. Clean up the text by removing common filename suffixes like (01), (1), (001), (copy), _01, -1, etc.
2. Preserve any hashtags and meaningful context.
3. Generate a short, engaging reel title (5-10 words).
4. Generate a natural description that includes the title idea and any hashtags.
5. Output the result in the target language: {target_language}

Return ONLY a valid JSON object with exactly these two fields:
{{
  "title": "...",
  "description": "..."
}}

No markdown, no code blocks, no explanation, just the JSON object."""

    def _parse_response(self, response_text: str) -> dict | None:
        """Parse JSON response from Gemini.

        Args:
            response_text: Raw response text from Gemini

        Returns:
            Parsed dictionary with "title" and "description" keys, or None if parsing fails
        """
        try:
            # Try to extract JSON from response
            text = response_text.strip()

            # Remove markdown code blocks if present
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]

            text = text.strip()

            # Parse JSON
            data = json.loads(text)

            # Validate required fields
            if isinstance(data, dict) and "title" in data and "description" in data:
                return {
                    "title": str(data["title"]).strip(),
                    "description": str(data["description"]).strip(),
                }
            else:
                return None

        except json.JSONDecodeError:
            return None
        except Exception:
            return None
