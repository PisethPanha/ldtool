"""Helper functions for building default reel captions from filenames."""

import re
from pathlib import Path


def clean_caption_text(text: str) -> str:
    """Remove common filename suffixes and clean up text.

    Args:
        text: Raw filename or caption text

    Returns:
        Cleaned text
    """
    if not text:
        return ""

    # Remove file extension
    text = Path(text).stem

    # Remove common numbering suffixes
    # Patterns: (01), (1), (001), (xxxx), _01, -1, etc.
    text = re.sub(r'\s*[\(\[]?\d{1,4}[\)\]]?\s*$', '', text)
    text = re.sub(r'\s*[_-]\d{1,4}\s*$', '', text)
    text = re.sub(r'\s*\(copy\)\s*$', '', text, flags=re.IGNORECASE)

    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def extract_hashtags(text: str) -> list[str]:
    """Extract hashtags from text.

    Args:
        text: Text containing hashtags

    Returns:
        List of hashtags (with # prefix)
    """
    matches = re.findall(r'#\w+', text)
    return matches


def build_default_reel_caption(filename_or_text: str) -> dict[str, str]:
    """Build default reel title and description from filename.

    Args:
        filename_or_text: Filename or caption text

    Returns:
        Dictionary with "title" and "description" keys
    """
    # Extract hashtags from original text
    hashtags = extract_hashtags(filename_or_text)
    hashtag_str = " ".join(hashtags)

    # Clean the text for title
    cleaned_title = clean_caption_text(filename_or_text)

    # Build description with hashtags
    if hashtag_str:
        description = f"{cleaned_title} {hashtag_str}".strip()
    else:
        description = cleaned_title

    return {
        "title": cleaned_title,
        "description": description,
    }
