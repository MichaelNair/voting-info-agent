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

CIVIC_INFO_API_BASE = "https://www.googleapis.com/civicinfo/v2"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
WEB_SEARCH_PROMPT_PATH = PROMPTS_DIR / "web_search_prompt.txt"

# Load environment and prompt text
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CIVIC_INFO_API_KEY = os.getenv("CIVIC_INFO_API_KEY")
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
    """Get the current date and return it as a string
    Returns:
        A string containing the current date in the format "YYYY-MM-DD".
    """
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
    Will save certain government websites in a JSON database to make future searches faster and more consistent.

    Args:
        query: Topic or question to look up on the web.

    Returns:
        A string containing the search results.
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


### IMPLEMENTATION OF GOOGLE CIVIC INFO API ###

def _query_voter_info(address: str | None = None,
                      state: str | None = None,
                      *,
                      election_id: str | None = None,
                      official_only: bool = True) -> dict[str, Any]:
    """Call the voterInfoQuery endpoint with the bare-minimum parameters."""
    if not CIVIC_INFO_API_KEY:
        raise RuntimeError(
            "Set CIVIC_INFO_API_KEY in your environment or .env file before running."
        )
    
    # if address isn't provided but state is, generate a fake address for that state
    if address is None:
        if state is None:
            raise ValueError("Either address or state must be provided.")
        address = f"123 Main St, Anytown, {state.strip()} 12345"

    url = f"{CIVIC_INFO_API_BASE}/voterinfo"
    headers = {"User-Agent": USER_AGENT}
    timeout = httpx.Timeout(30.0, connect=10.0)

    params: dict[str, Any] = {
        "key": CIVIC_INFO_API_KEY,
        "address": address,
        "officialOnly": str(official_only).lower(),
    }
    if election_id:
        params["electionId"] = election_id

    # print request
    print(f"Request: {url} {params}")

    response = httpx.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()

@mcp.tool()
async def find_district_and_precinct(address: str) -> str:
    """Find the district and precinct for a given address using the Google Civic Info API, divisionsByAddress tool
    Args:
        address: The address to find the district and precinct for.
        Must be a complete address with street number, street name, city, state, and zip code.
        For example, "123 Main St, Anytown, NY 12345".
    Args:
        A dictionary following this format
        ```json
        {
        "kind": "civicinfo#divisionsByAddressResponse",
        "normalizedInput": {
            "locationName": string,
            "line1": string,
            "line2": string,
            "line3": string,
            "city": string,
            "state": string,
            "zip": string,
        },
        "divisions": {
            key: {
            "name": string,
                "alsoKnownAs": [
                string
            ],
            },
        }.
        ```
    """
    if not address or not address.strip():
        return "Please provide a complete address (street number, street name, city, state, zip code)."

    if not CIVIC_INFO_API_KEY:
        raise RuntimeError(
            "Set CIVIC_INFO_API_KEY in your environment or .env file before running."
        )

    url = f"{CIVIC_INFO_API_BASE}/divisionsByAddress"
    headers = {"User-Agent": USER_AGENT}
    timeout = httpx.Timeout(30.0, connect=10.0)

    params: dict[str, Any] = {
        "key": CIVIC_INFO_API_KEY,
        "address": address,
    }

    response = httpx.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    divisions = response.json().get("divisions", [])
    if not divisions:
        return "No divisions were returned by the Google Civic Info API."
    return json.dumps(divisions, indent=2)

@mcp.tool()
async def list_upcoming_elections() -> str:
    """Expose the Google Civic Information electionQuery endpoint via MCP.
    This will return all upcoming elections regardless of state or locality, look at the ocdDivisionId to determine the state and county
    Args:
        no arguments
    Returns:
        A string in JSON format containing the election information.
        ```json
        {
        "kind": "civicinfo#electionsQueryResponse",
        "elections": [
            {
            "id": long,
            "name": string,
            "electionDay": string,
            "ocdDivisionId": string
                }
            ]
        }
        ```
    """
    if not CIVIC_INFO_API_KEY:
        raise RuntimeError(
            "Set CIVIC_INFO_API_KEY in your environment or .env file before running."
        )

    url = f"{CIVIC_INFO_API_BASE}/elections"
    headers = {"User-Agent": USER_AGENT}
    timeout = httpx.Timeout(30.0, connect=10.0)
    response = httpx.get(url, params={"key": CIVIC_INFO_API_KEY}, headers=headers, timeout=timeout)
    response.raise_for_status()

    elections = response.json().get("elections", [])
    if not elections:
        return "No elections were returned by the Google Civic Info API."
    return elections

@mcp.tool()
async def get_election_info(election_id: str, state: str) -> str:
    """Get the information for a given election using the Google Civic Info API, voterinfo tool
    Args:
        election_id: The ID of the election to get information for.
    Returns:
        A string in JSON format containing the election information.
        ```json
        
    """
    try:
        payload = _query_voter_info(
            state=state,
            election_id=election_id,
        )
    except httpx.HTTPStatusError as exc:
        reason = exc.response.reason_phrase or "Unknown error"
        detail = exc.response.text
        print(
            f"❌ Civic Information API rejected voterInfoQuery "
            f"(HTTP {exc.response.status_code} {reason})."
        )
        if detail:
            print(detail)
        return (
            "Unable to complete the voter information lookup right now. "
            f"(HTTP {exc.response.status_code} {reason})"
        )
    except httpx.RequestError as exc:
        print(f"❌ Network error when contacting voterInfoQuery: {exc}")
        return "Unable to complete the voter information lookup right now. Please try again later."
    except RuntimeError as exc:
        print(f"❌ {exc}")
        return "Unable to complete the voter information lookup right now. Please try again later."

    return json.dumps(payload, indent=2)


@mcp.tool()
async def get_voter_info(
    address: str,
    election_id: str | None = None,
    official_only: bool = True,
) -> str:
    """Expose the voterInfoQuery endpoint for a caller-supplied address.
    
    Args:
        address: The address to get information for.
        Must be a complete address with street number, street name, city, state, and zip code.
        For example, "123 Main St, Anytown, NY 12345".
        election_id: The ID of the election to get information for.
        official_only: Whether to only return official information.
    Returns:
        A string in JSON format containing the voter information.
    """
    if not address or not address.strip():
        return "Please provide a complete address (street, city, state, and ZIP code)."

    try:
        payload = _query_voter_info(
            address=address.strip(),
            election_id=election_id if election_id else None,
            official_only=official_only,
        )
    except httpx.HTTPStatusError as exc:
        reason = exc.response.reason_phrase or "Unknown error"
        detail = exc.response.text
        print(
            f"❌ Civic Information API rejected voterInfoQuery "
            f"(HTTP {exc.response.status_code} {reason})."
        )
        if detail:
            print(detail)
        return (
            "Unable to complete the voter information lookup right now. "
            f"(HTTP {exc.response.status_code} {reason})"
        )
    except httpx.RequestError as exc:
        print(f"❌ Network error when contacting voterInfoQuery: {exc}")
        return "Unable to complete the voter information lookup right now. Please try again later."
    except RuntimeError as exc:
        print(f"❌ {exc}")
        return "Unable to complete the voter information lookup right now. Please try again later."

    return json.dumps(payload, indent=2)

# @mcp.tool()
# async def get_nearest_polling_location(address: str) -> str:
#     """Get the nearest polling location for a given address."""
#     return "Polling location lookup not implemented yet."

def main():
    # Initialize and run the server
    logging.info("Starting voting-data MCP server...")
    mcp.run(transport='stdio')

if __name__ == "__main__":
    main()
