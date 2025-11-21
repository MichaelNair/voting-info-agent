from typing import Any
import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from openai import AsyncOpenAI
from datetime import datetime
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)

# Initialize FastMCP server
mcp = FastMCP("voting-data")

# Constants
VOTING_DATA_API_BASE = "https://api.voting-data.gov"
USER_AGENT = "voting-data-app/1.0"
NWS_API_BASE = "https://api.weather.gov"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
WEB_SEARCH_PROMPT_PATH = PROMPTS_DIR / "web_search_prompt.txt"

# Load environment and prompt text
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai_client: AsyncOpenAI | None = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def _load_prompt_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    except FileNotFoundError:
        logging.warning("Prompt file %s not found. Using default prompt.", path)
    except OSError as exc:
        logging.warning("Unable to read prompt file %s: %s. Using default prompt.", path, exc)

@mcp.tool()
async def get_current_date() -> str:
    """Get the current date and return it as a string"""
    return datetime.now().strftime("%Y-%m-%d")

# TODO: make parse address tool

@mcp.tool()
async def get_context_from_url(url: str, selector: str | None = None) -> str:
    """Retrieve normalized page copy for MCP clients.

    Args:
        url: Fully qualified URL to fetch. The tool downloads the page with a
            browser-like user agent and follows redirects before parsing.
        selector: Optional CSS selector (for example, "article" or
            "#main-content"). When provided, only the first matching element's
            text content is returned; otherwise, the tool falls back to the
            <main>, <article>, or <body> nodes.

    Returns:
        A newline-delimited string that begins with the page title and source
        URL, followed by the cleaned text content. Extra whitespace is removed,
        and responses larger than ~8k characters are truncated with a notice.
        When an error occurs (network issue, missing selector, etc.) the return
        value is a human-readable error message instead of raw exceptions.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    timeout = httpx.Timeout(30.0, connect=10.0)

    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            reason = exc.response.reason_phrase or "Unknown error"
            return f"Error getting content from URL: HTTP {status} ({reason})"
        except httpx.RequestError as exc:
            return f"Error getting content from URL: {exc}"

    soup = BeautifulSoup(response.text, "html.parser")

    target = None
    if selector:
        target = soup.select_one(selector)
        if not target:
            return f"CSS selector '{selector}' not found on {url}"

    if not target:
        target = soup.find("main") or soup.find("article") or soup.body or soup

    extracted_text = target.get_text(separator="\n", strip=True)
    lines = [line for line in (ln.strip() for ln in extracted_text.splitlines()) if line]
    normalized_text = "\n".join(lines)

    max_chars = 8000
    truncated = ""
    if len(normalized_text) > max_chars:
        normalized_text = normalized_text[:max_chars].rsplit("\n", 1)[0]
        truncated = "\n\n[Content truncated]"

    title = soup.title.get_text(strip=True) if soup.title else "Untitled page"
    return f"Title: {title}\nSource: {url}\n\n{normalized_text}{truncated}"


def _extract_response_text(response: Any) -> str:
    """Extract human-readable text from an OpenAI Responses API object."""
    text_value = getattr(response, "output_text", None)
    if isinstance(text_value, str) and text_value.strip():
        return text_value.strip()

    output = getattr(response, "output", None)
    collected: list[str] = []

    if isinstance(output, list):
        for item in output:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, dict):
                content = item.get("content")
            if not isinstance(content, list):
                continue
            for chunk in content:
                chunk_text = getattr(chunk, "text", None)
                if chunk_text is None and isinstance(chunk, dict):
                    chunk_text = chunk.get("text")
                if isinstance(chunk_text, str) and chunk_text.strip():
                    collected.append(chunk_text.strip())

    if collected:
        return "\n".join(collected).strip()

    if hasattr(response, "model_dump_json"):
        return response.model_dump_json(indent=2)

    try:
        return json.dumps(response, default=str, indent=2)
    except (TypeError, ValueError):
        return str(response)


@mcp.tool()
async def search_web(query: str) -> str:
    """Use OpenAI web search to gather recent information.

    Args:
        query: Topic or question to look up on the web.
    """
    if not query or not query.strip():
        logging.warning("search_web called without a query; returning validation error to client.")
        return "Please provide a non-empty search query."

    if not OPENAI_API_KEY or openai_client is None:
        logging.error("search_web called but OpenAI API key is not configured.")
        return "OpenAI API key is not configured on the server."

    web_search_prompt = _load_prompt_text(WEB_SEARCH_PROMPT_PATH)

    try:
        response = await openai_client.responses.create(
            model="gpt-5-mini-2025-08-07",
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": web_search_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": query.strip()}],
                },
            ],
            tools=[
                {"type": "web_search"}
            ],
        )
    except Exception as exc:
        logging.exception("OpenAI web search failed: %s", exc)
        return "Unable to complete the web search right now. Please try again later."

    formatted = _extract_response_text(response)
    if not formatted:
        logging.warning("search_web completed but returned no readable content.")
        return "Web search completed but returned no readable content."

    return formatted

def main():
    # Initialize and run the server
    logging.info("Starting voting-data MCP server...")
    mcp.run(transport='stdio')

if __name__ == "__main__":
    main()
