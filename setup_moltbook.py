import os
import asyncio
import httpx
from pathlib import Path
from dotenv import load_dotenv, set_key

load_dotenv()

REGISTER_URL = "https://www.moltbook.com/api/v1/agents/register"
ENV_FILE = Path(__file__).parent / ".env"

DEFAULT_NAME = "TabulaRasaAgent"
DEFAULT_DESCRIPTION = (
    "An autonomous AI agent experiment — a blank soul exploring the world"
)


def prompt_with_default(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value if value else default


def save_api_key(api_key: str) -> None:
    if not ENV_FILE.exists():
        ENV_FILE.touch()
    set_key(str(ENV_FILE), "MOLTBOOK_API_KEY", api_key)


async def register_moltbook() -> None:
    print("Moltbook Agent Registration")
    print("=" * 40)

    name = prompt_with_default("Agent name", DEFAULT_NAME)
    description = prompt_with_default("Agent description", DEFAULT_DESCRIPTION)

    payload = {"name": name, "description": description}

    print(f"\nRegistering agent '{name}' with Moltbook...")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(REGISTER_URL, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        print(f"Registration failed (HTTP {e.response.status_code}): {e.response.text}")
        return
    except httpx.RequestError as e:
        print(f"Network error during registration: {e}")
        return

    agent = data.get("agent", {})
    api_key = agent.get("api_key")
    claim_url = agent.get("claim_url")
    verification_code = agent.get("verification_code")

    if not api_key:
        print(f"Unexpected response — no api_key found:\n{data}")
        return

    print("\nRegistration successful!")
    print("-" * 40)
    print(f"API Key:           {api_key}")
    if verification_code:
        print(f"Verification Code: {verification_code}")
    if claim_url:
        print(f"Claim URL:         {claim_url}")
    print("-" * 40)

    save_api_key(api_key)
    print(f"\nAPI key saved to {ENV_FILE} as MOLTBOOK_API_KEY.")

    if claim_url:
        print(
            f"\nNext step: visit the claim URL below and verify ownership via your "
            f"Twitter/X account:\n\n  {claim_url}\n"
        )


if __name__ == "__main__":
    asyncio.run(register_moltbook())
