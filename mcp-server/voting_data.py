from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP

import logging
logging.basicConfig(level=logging.INFO)

from datetime import datetime
from bs4 import BeautifulSoup

# Initialize FastMCP server
mcp = FastMCP("voting-data")

# Constants
VOTING_DATA_API_BASE = "https://api.voting-data.gov"
USER_AGENT = "voting-data-app/1.0"
NWS_API_BASE = "https://api.weather.gov"


async def make_nws_request(url: str) -> dict[str, Any] | None:
    """Make a request to the NWS API with proper error handling."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json"
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

def format_alert(feature: dict) -> str:
    """Format an alert feature into a readable string."""
    props = feature["properties"]
    return f"""
Event: {props.get('event', 'Unknown')}
Area: {props.get('areaDesc', 'Unknown')}
Severity: {props.get('severity', 'Unknown')}
Description: {props.get('description', 'No description available')}
Instructions: {props.get('instruction', 'No specific instructions provided')}
"""

@mcp.tool()
async def get_alerts(state: str) -> str:
    """Get weather alerts for a US state.

    Args:
        state: Two-letter US state code (e.g. CA, NY)
    """
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)

    if not data or "features" not in data:
        return "Unable to fetch alerts or no alerts found."

    if not data["features"]:
        return "No active alerts for this state."

    alerts = [format_alert(feature) for feature in data["features"]]
    return "\n---\n".join(alerts)

@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get weather forecast for a location.

    Args:
        latitude: Latitude of the location
        longitude: Longitude of the location
    """
    # First get the forecast grid endpoint
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)

    if not points_data:
        return "Unable to fetch forecast data for this location."

    # Get the forecast URL from the points response
    forecast_url = points_data["properties"]["forecast"]
    forecast_data = await make_nws_request(forecast_url)

    if not forecast_data:
        return "Unable to fetch detailed forecast."

    # Format the periods into a readable forecast
    periods = forecast_data["properties"]["periods"]
    forecasts = []
    for period in periods[:5]:  # Only show next 5 periods
        forecast = f"""
{period['name']}:
Temperature: {period['temperature']}°{period['temperatureUnit']}
Wind: {period['windSpeed']} {period['windDirection']}
Forecast: {period['detailedForecast']}
"""
        forecasts.append(forecast)

    return "\n---\n".join(forecasts)

@mcp.tool()
async def get_current_date() -> str:
    """Get the current date and return it as a string"""
    return datetime.now().strftime("%Y-%m-%d")

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

def main():
    # Initialize and run the server
    logging.info("Starting voting-data MCP server...")
    mcp.run(transport='stdio')

if __name__ == "__main__":
    main()
