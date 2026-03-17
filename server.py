#!/usr/bin/env python3
"""
WhatsApp MCP Server
Sends WhatsApp messages via Twilio API using the MCP protocol.
"""

import json
import os
import sys
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from dotenv import load_dotenv

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("whatsapp-mcp")

# ── Config ─────────────────────────────────────────────────────────────────────
load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")   # e.g. +14155238886
ALLOWED_NUMBERS_RAW  = os.getenv("ALLOWED_NUMBERS", "")         # comma-separated

# Parse allowed numbers into a set (empty = allow all)
ALLOWED_NUMBERS: set[str] = {
    n.strip() for n in ALLOWED_NUMBERS_RAW.split(",") if n.strip()
}

# ── Twilio client (lazy-init so missing creds surface at call time) ─────────────
_twilio_client: Client | None = None

def get_twilio_client() -> Client:
    global _twilio_client
    if _twilio_client is None:
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            raise ValueError(
                "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set "
                "in the environment or .env file."
            )
        _twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _twilio_client


# ── Helpers ────────────────────────────────────────────────────────────────────
def normalise_number(number: str) -> str:
    """Ensure number starts with + and contains only digits after that."""
    number = number.strip()
    if not number.startswith("+"):
        number = "+" + number
    return number


def whatsapp_uri(number: str) -> str:
    return f"whatsapp:{number}"


def send_whatsapp_message(to: str, body: str) -> dict[str, Any]:
    """Core send function. Returns a result dict."""
    to = normalise_number(to)

    # Allowed-list guard
    if ALLOWED_NUMBERS and to not in ALLOWED_NUMBERS:
        return {
            "success": False,
            "error": f"Number {to} is not in the ALLOWED_NUMBERS list.",
        }

    if not TWILIO_WHATSAPP_FROM:
        return {
            "success": False,
            "error": "TWILIO_WHATSAPP_FROM is not configured.",
        }

    try:
        client = get_twilio_client()
        message = client.messages.create(
            from_=whatsapp_uri(TWILIO_WHATSAPP_FROM),
            to=whatsapp_uri(to),
            body=body,
        )
        logger.info("Message sent to %s — SID: %s", to, message.sid)
        return {
            "success": True,
            "sid": message.sid,
            "status": message.status,
            "to": to,
        }
    except TwilioRestException as exc:
        logger.error("Twilio error: %s", exc)
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        return {"success": False, "error": str(exc)}


def send_bulk_whatsapp_messages(
    numbers: list[str], body: str
) -> list[dict[str, Any]]:
    """Send the same message to multiple numbers."""
    return [send_whatsapp_message(n, body) for n in numbers]


# ── MCP Server ─────────────────────────────────────────────────────────────────
app = Server("whatsapp-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="send_whatsapp_message",
            description=(
                "Send a WhatsApp message to a single phone number "
                "via the Twilio WhatsApp API."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": (
                            "Recipient phone number in E.164 format "
                            "(e.g. +919876543210)."
                        ),
                    },
                    "body": {
                        "type": "string",
                        "description": "The text content of the WhatsApp message.",
                    },
                },
                "required": ["to", "body"],
            },
        ),
        Tool(
            name="send_bulk_whatsapp_messages",
            description=(
                "Send the same WhatsApp message to multiple phone numbers at once."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "numbers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of recipient phone numbers in E.164 format."
                        ),
                    },
                    "body": {
                        "type": "string",
                        "description": "The text content of the WhatsApp message.",
                    },
                },
                "required": ["numbers", "body"],
            },
        ),
        Tool(
            name="list_allowed_numbers",
            description=(
                "List the phone numbers currently in the allowed-list. "
                "Returns all numbers if no restriction is configured."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "send_whatsapp_message":
        to   = arguments.get("to", "")
        body = arguments.get("body", "")
        if not to or not body:
            result = {"success": False, "error": "'to' and 'body' are required."}
        else:
            result = send_whatsapp_message(to, body)

    elif name == "send_bulk_whatsapp_messages":
        numbers = arguments.get("numbers", [])
        body    = arguments.get("body", "")
        if not numbers or not body:
            result = {"success": False, "error": "'numbers' and 'body' are required."}
        else:
            result = send_bulk_whatsapp_messages(numbers, body)

    elif name == "list_allowed_numbers":
        result = {
            "allowed_numbers": sorted(ALLOWED_NUMBERS) if ALLOWED_NUMBERS else "ALL",
            "from_number": TWILIO_WHATSAPP_FROM or "NOT SET",
        }

    else:
        result = {"success": False, "error": f"Unknown tool: {name}"}

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


# ── Entry-point ────────────────────────────────────────────────────────────────
async def main():
    logger.info("Starting WhatsApp MCP server…")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
