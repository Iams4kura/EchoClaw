"""WebFetch tool: fetch a URL and return its content."""

import asyncio
import logging
from typing import Optional

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult

logger = logging.getLogger(__name__)


class WebFetchTool(BaseTool):
    name = "WebFetch"
    description = (
        "Fetch the contents of a URL and return the response body. "
        "Supports HTML pages, JSON APIs, RSS/XML feeds, and plain text. "
        "For HTML, extracts readable text; for other types, returns raw content."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch",
            },
            "headers": {
                "type": "object",
                "description": "Optional HTTP headers to send",
                "additionalProperties": {"type": "string"},
            },
            "max_length": {
                "type": "integer",
                "description": "Maximum response length in characters (default 20000)",
            },
        },
        "required": ["url"],
    }
    permission_category = PermissionCategory.EXTERNAL

    async def execute(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        url = params["url"]
        custom_headers = params.get("headers", {})
        max_length = params.get("max_length", 20000)

        try:
            content = await asyncio.to_thread(
                self._fetch, url, custom_headers, max_length
            )
            return ToolResult(content=content, is_error=False)
        except Exception as e:
            logger.warning("WebFetch failed for %s: %s", url, e)
            return ToolResult(content=f"Fetch error: {e}", is_error=True)

    @staticmethod
    def _fetch(url: str, headers: dict, max_length: int) -> str:
        import urllib.request
        import urllib.error

        req_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MiniClaude/1.0)",
            **headers,
        }
        req = urllib.request.Request(url, headers=req_headers)

        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()

            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()

            text = raw.decode(charset, errors="replace")

        if "text/html" in content_type:
            text = _extract_text_from_html(text)

        if len(text) > max_length:
            text = text[:max_length] + f"\n\n... (truncated, {len(text)} total chars)"

        return text


def _extract_text_from_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except ImportError:
        import re

        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
