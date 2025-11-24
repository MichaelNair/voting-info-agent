import argparse
import json
import os
import sys
from datetime import date
from typing import Any

import httpx
from dotenv import load_dotenv

CIVIC_INFO_API_BASE = "https://www.googleapis.com/civicinfo/v2"
USER_AGENT = "voting-data-app/1.0"
# DEFAULT_ADDRESS = "2100 Clarendon Blvd, Arlington, VA 22201"
DEFAULT_ADDRESS = "123 Main St, Anytown, RI 12345"
DEFAULT_STATE = "RI"

load_dotenv()
CIVIC_INFO_API_KEY = os.getenv("CIVIC_INFO_API_KEY")


def _fetch_elections_ids() -> list[str]:
    """Retrieve the next available electionId via electionQuery."""
    if not CIVIC_INFO_API_KEY:
        raise RuntimeError(
            "Set CIVIC_INFO_API_KEY in your environment or .env file before running."
        )

    url = f"{CIVIC_INFO_API_BASE}/elections"
    headers = {"User-Agent": USER_AGENT}
    timeout = httpx.Timeout(30.0, connect=10.0)
    response = httpx.get(url, params={"key": CIVIC_INFO_API_KEY}, headers=headers, timeout=timeout)
    response.raise_for_status()

    elections = response.json().get("elections") or []
    print(f" first 5 elections: {json.dumps(elections[:5], indent=2)}")

    return elections

def _match_election_to_address(elections: list[str], state: str, return_all: bool = False) -> str:
    """Match an election to an address.
    
    Args:
        election_ids: A list of election ids to match the address to.
        state: The state to match the election to.
        return_all: Whether to return all elections that match the address or just the next one.
    Returns:
        A list of election ids that match the address.
    """
    # generate ocdDivisionId based on address
    # ex. "ocd-division/country:us/state:va"
    target_ocdDivisionId = f"ocd-division/country:us/state:{state.strip().lower()}"

    # filter elections by ocdDivisionId
    filtered_elections = [election for election in elections if election.get("ocdDivisionId").startswith(target_ocdDivisionId)]
    if not filtered_elections:
        return None

    # sort elections by electionDay
    filtered_elections.sort(key=lambda x: x.get("electionDay"))
    
    if return_all:
        return [election.get("id") for election in filtered_elections]
    else:
        return filtered_elections[0].get("id")


def _query_voter_info(address: str, *, election_id: str | None, official_only: bool) -> dict[str, Any]:
    """Call the voterInfoQuery endpoint with the bare-minimum parameters."""
    if not CIVIC_INFO_API_KEY:
        raise RuntimeError(
            "Set CIVIC_INFO_API_KEY in your environment or .env file before running."
        )

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


def _format_summary(payload: dict[str, Any]) -> str:
    # normalized = payload.get("normalizedInput", {})
    # election = payload.get("election", {}) or {}
    # contests = payload.get("contests") or []
    # polling_locations = payload.get("pollingLocations") or []

    # summary = [
    #     f"Election         : {election.get('name', 'Unavailable')}",
    #     f"Normalized address: {normalized or 'Unavailable'}",
    #     f"Contests returned : {len(contests)}",
    #     f"Polling locations : {len(polling_locations)}",
    # ]

    # if polling_locations:
    #     addr = polling_locations[0].get("address") or {}
    #     location = addr.get("locationName") or "Polling place"
    #     city_state = ", ".join(
    #         part
    #         for part in [addr.get("city"), addr.get("state"), addr.get("zip")]
    #         if part
    #     )
    #     summary.append(f"Sample location   : {location} ({city_state or 'address pending'})")

    # return "\n".join(summary)

    return json.dumps(payload, indent=2)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal voterInfoQuery call using the configured Civic Info API key."
    )
    parser.add_argument(
        "address",
        nargs="?",
        default=DEFAULT_ADDRESS,
        help="Complete civic address to query (default: %(default)s).",
    )
    parser.add_argument(
        "--state",
        default=DEFAULT_STATE,
        help="State to query for elections. (e.g. VA)",
    )
    parser.add_argument(
        "--election-id",
        help="Optional electionId parameter from the elections endpoint.",
    )
    parser.add_argument(
        "--official-only",
        action="store_true",
        help="Limit results to data from official state sources.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print the raw JSON response from the API.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    address = args.address.strip()

    election_id = args.election_id
    if not election_id:
        elections = _fetch_elections_ids()
        election_id = _match_election_to_address(elections, args.state)
        if not election_id:
            print("❌ No elections returned by electionQuery; May not have found any for state {args.state}.")
            sys.exit(1)
        print(f"ℹ️ Using next electionId from electionQuery: {election_id}")

    try:
        payload = _query_voter_info(
            address,
            election_id=election_id,
            official_only=args.official_only,
        )
    except httpx.HTTPStatusError as exc:
        reason = exc.response.reason_phrase or "Unknown error"
        detail = exc.response.text
        print(
            f"❌ Civic Information API rejected the request "
            f"(HTTP {exc.response.status_code} {reason})."
        )
        if detail:
            print(detail)
        sys.exit(1)
    except httpx.RequestError as exc:
        print(f"❌ Network error when contacting the Civic Information API: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        print(f"❌ {exc}")
        sys.exit(1)

    print("✅ voterInfoQuery succeeded.")
    print(_format_summary(payload))

    if args.raw:
        print("\nRaw response:")
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)

