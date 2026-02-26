"""
Groq metadata generator — uses Groq LLM to generate YouTube video metadata.
Generates title, description, and tags from a video filename.
"""
import json
import logging

from groq import Groq

import config

logger = logging.getLogger(__name__)


def generate_metadata(filename: str, extra_context: str = "") -> dict:
    """
    Generate YouTube metadata (title, description, tags) using Groq LLM.

    Args:
        filename: The video filename (used as context).
        extra_context: Optional additional context from the user.

    Returns:
        dict with keys: title, description, tags
    """
    client = Groq(api_key=config.GROQ_API_KEY)

    prompt = config.METADATA_PROMPT_TEMPLATE.format(filename=filename)

    if extra_context:
        prompt += f"\n\nAdditional context from user: {extra_context}"

    try:
        response = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a YouTube SEO expert. Always respond with valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=500,
        )

        raw = response.choices[0].message.content.strip()

        # Clean markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]  # remove first line
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        metadata = json.loads(raw)

        result = {
            "title": metadata.get("title", filename),
            "description": metadata.get("description", ""),
            "tags": metadata.get("tags", ""),
        }

        logger.info(f"Generated metadata for '{filename}': {result['title']}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Groq response as JSON: {e}")
        logger.error(f"Raw response: {raw}")
        return {
            "title": filename.rsplit(".", 1)[0].replace("_", " ").title(),
            "description": f"Video: {filename}",
            "tags": "video",
        }
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        return {
            "title": filename.rsplit(".", 1)[0].replace("_", " ").title(),
            "description": f"Video: {filename}",
            "tags": "video",
        }
