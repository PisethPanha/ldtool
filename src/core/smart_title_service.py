"""Smart Title by Bot service for parsing media filenames into structured captions.

This service provides local filename parsing to generate title and description
without requiring external API calls. It extracts hashtags, cleans patterns,
and generates Instagram-ready caption format.
"""

import re
from typing import Callable


class SmartTitleService:
    """Service for parsing media filenames into structured captions locally."""
    
    # File extensions to remove
    VIDEO_EXTENSIONS = (
        '.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.wmv', '.m4v'
    )
    
    # Patterns to remove from end of filename (trailing numeric patterns)
    TRAILING_PATTERNS = [
        r'\(\d+\)$',           # (01), (1), (0001)
        r'\(\w+\)$',           # (copy), (final)
        r'[_-]\d+$',           # _01, -01
        r'\s+\(\w{3,}\)$',     # (copy), (final) with space
    ]
    
    # Hashtag pattern
    HASHTAG_PATTERN = r'#[A-Za-z0-9_]+'
    
    def __init__(self, log_fn: Callable[[str], None] | None = None):
        """Initialize the Smart Title service.
        
        Args:
            log_fn: Optional logging callback
        """
        self._log = log_fn or (lambda m: None)
    
    def parse_filename(self, filename: str) -> dict:
        """Parse a media filename into structured caption JSON.
        
        Args:
            filename: The media filename to parse (with or without extension)
            
        Returns:
            Dictionary with structure:
            {
                "title": str,        # Cleaned title without hashtags
                "description": str   # Title + hashtags
            }
            
        Example:
            Input: "Africa's $80 Billion Dam Proposal! #power #geography #africa (01).mp4"
            Output:
            {
                "title": "Africa's $80 Billion Dam Proposal!",
                "description": "Africa's $80 Billion Dam Proposal! #power #geography #africa"
            }
        """
        try:
            self._log(f"SmartTitle parsing filename: {filename}")
            
            # Step 1: Remove file extension
            cleaned = self._remove_extension(filename)

            # Step 2: Extract hashtags first so they are preserved
            hashtags = self._extract_hashtags(cleaned)

            # Step 3: Remove hashtags from title portion and clean the title text
            title = self._remove_hashtags(cleaned)
            title = self._normalize_whitespace(title)
            self._log(f"SmartTitle raw title before cleanup: {title}")

            # Step 4: Repeatedly strip trailing parenthesized metadata blocks
            # Example: "My Title (Official Video) (1080p)" -> "My Title"
            trailing_parentheses_pattern = r"\s*\([^()]*\)\s*$"
            while re.search(trailing_parentheses_pattern, title):
                title = re.sub(trailing_parentheses_pattern, "", title).rstrip()

            self._log(f"SmartTitle title after removing trailing parentheses: {title}")

            # Step 5: Keep existing trailing numeric cleanup (_01, -01, etc.)
            title = self._remove_trailing_patterns(title)

            # Step 6: Normalize whitespace
            title = self._normalize_whitespace(title)

            # Step 7: Build description (title + hashtags)
            if hashtags:
                description = f"{title} {' '.join(hashtags)}"
            else:
                description = title
            
            self._log(f"SmartTitle title: {title}")
            if hashtags:
                self._log(f"SmartTitle hashtags: {' '.join(hashtags)}")
            
            return {
                "title": title,
                "description": description
            }
            
        except Exception as exc:
            # Safety fallback: return cleaned filename
            self._log(f"⚠ SmartTitle parsing failed: {type(exc).__name__}: {str(exc)}")
            fallback_title = self._safe_fallback(filename)
            return {
                "title": fallback_title,
                "description": fallback_title
            }
    
    def _remove_extension(self, filename: str) -> str:
        """Remove video file extension if present."""
        lower_filename = filename.lower()
        for ext in self.VIDEO_EXTENSIONS:
            if lower_filename.endswith(ext):
                return filename[:-len(ext)]
        return filename
    
    def _remove_trailing_patterns(self, text: str) -> str:
        """Remove trailing numeric patterns like (01), _01, etc."""
        for pattern in self.TRAILING_PATTERNS:
            text = re.sub(pattern, '', text).rstrip()
        return text
    
    def _extract_hashtags(self, text: str) -> list[str]:
        """Extract all hashtags from text."""
        return re.findall(self.HASHTAG_PATTERN, text)
    
    def _remove_hashtags(self, text: str) -> str:
        """Remove all hashtags from text."""
        return re.sub(self.HASHTAG_PATTERN, '', text)
    
    def _normalize_whitespace(self, text: str) -> str:
        """Collapse multiple spaces and trim edges."""
        # Replace multiple spaces with single space
        text = re.sub(r'\s+', ' ', text)
        # Trim edges
        return text.strip()
    
    def _safe_fallback(self, filename: str) -> str:
        """Generate safe fallback title if parsing fails."""
        # Just remove extension and normalize
        cleaned = self._remove_extension(filename)
        return self._normalize_whitespace(cleaned)
