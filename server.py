#!/usr/bin/env python3
"""
WhatsApp MCP Server
===================
Sends WhatsApp messages via Twilio. After sending, polls for a reply:

  Affirmative reply (Yes / True / 1)
    → Returns a next_tool directive so Claude chains the official
      Zerodha Kite MCP get_holdings tool automatically.

  Any other reply  → logs "Nothing"
  No reply         → logs "timeout"
"""

import json
import os
import sys
import time
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Any
from datetime import datetime, timezone

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from dotenv import load_dotenv

# ── Logging ────────────────────────────────────────────────────────────────────
# Logs are saved to:
#   <folder containing server.py>\logs\whatsapp-mcp.log
# A new file is created each day. Files older than 7 days are deleted automatically.
# Logs are also printed to stderr (visible in the VS Code terminal).

_LOG_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_LOG_FILE    = os.path.join(_LOG_DIR, "whatsapp-mcp.log")
_LOG_FORMAT  = "%(asctime)s [%(levelname)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

os.makedirs(_LOG_DIR, exist_ok=True)   # create the folder if it doesn't exist yet

# File handler — rotates at midnight, keeps last 7 days
_file_handler = TimedRotatingFileHandler(
    filename=_LOG_FILE,
    when="midnight",       # rotate once per day at midnight
    interval=1,            # every 1 day
    backupCount=7,         # keep last 7 daily files, delete older ones
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
_file_handler.suffix = "%Y-%m-%d"     # rotated files → whatsapp-mcp.log.2026-03-17

# Stderr handler — keeps logs visible in VS Code terminal
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

# ⚠️  Do NOT use logging.basicConfig() here — the mcp library initialises the
#     root logger before our code runs, so basicConfig() becomes a no-op.
#     Instead, attach handlers directly to the root logger so they always apply.
_root_logger = logging.getLogger()          # root logger — parent of every logger
_root_logger.setLevel(logging.INFO)
_root_logger.addHandler(_file_handler)      # → file
_root_logger.addHandler(_stderr_handler)    # → terminal

logger = logging.getLogger("whatsapp-mcp") # named child logger used throughout

logger.info("Logs folder: %s", _LOG_DIR)

# ── Config ─────────────────────────────────────────────────────────────────────
load_dotenv()

TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")
ALLOWED_NUMBERS_RAW  = os.getenv("ALLOWED_NUMBERS", "")

REPLY_POLL_INTERVAL_SEC = 5
REPLY_TIMEOUT_SEC       = 60

AFFIRMATIVE_REPLIES = {"yes", "true", "1"}

ALLOWED_NUMBERS: set[str] = {
    n.strip() for n in ALLOWED_NUMBERS_RAW.split(",") if n.strip()
}


# ── Twilio client (lazy singleton) ─────────────────────────────────────────────
_twilio_client: Client | None = None

def get_twilio_client() -> Client:
    global _twilio_client
    if _twilio_client is None:
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            raise ValueError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set in .env")
        _twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _twilio_client


# ── Helpers ────────────────────────────────────────────────────────────────────
def normalise_number(number: str) -> str:
    number = number.strip()
    if not number.startswith("+"):
        number = "+" + number
    return number

def whatsapp_uri(number: str) -> str:
    return f"whatsapp:{number}"


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  REPLY POLLING                                                              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def poll_for_reply(from_number: str, sent_after: datetime) -> str | None:
    """
    Polls Twilio every REPLY_POLL_INTERVAL_SEC seconds for an inbound
    WhatsApp message from `from_number` that arrived after `sent_after`.
    Returns the reply body string, or None on timeout.
    """
    client        = get_twilio_client()
    deadline      = time.time() + REPLY_TIMEOUT_SEC
    whatsapp_from = f"whatsapp:{from_number}"

    logger.info("[POLL] Waiting up to %ds for reply from %s ...", REPLY_TIMEOUT_SEC, from_number)

    while time.time() < deadline:
        time.sleep(REPLY_POLL_INTERVAL_SEC)
        try:
            messages = client.messages.list(
                from_=whatsapp_from,
                to=f"whatsapp:{TWILIO_WHATSAPP_FROM}",
                date_sent_after=sent_after,
                limit=5,
            )
            if messages:
                reply_body = messages[0].body.strip()
                logger.info("[POLL] Reply received: '%s'", reply_body)
                return reply_body

        except TwilioRestException as exc:
            logger.error("[POLL] Twilio error while polling: %s", exc)

    logger.info("[POLL] Timeout — no reply from %s within %ds", from_number, REPLY_TIMEOUT_SEC)
    return None


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  REPLY HANDLER                                                              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def handle_reply(reply: str | None, to: str) -> dict[str, Any]:
    """
    Decision logic after receiving (or not receiving) a reply.

    Affirmative (Yes/True/1) → return next_tool directive so Claude
                                chains kite:get_holdings automatically.
    Other text               → log "Nothing"
    None (timeout)           → log "timeout"
    """
    # ── No reply ───────────────────────────────────────────────────────────────
    if reply is None:
        logger.info("[REPLY] No reply received — action: timeout")
        return {"reply_received": False, "action": "timeout"}

    # ── Affirmative ────────────────────────────────────────────────────────────
    if reply.lower().strip() in AFFIRMATIVE_REPLIES:
        logger.info(
            "[REPLY] Affirmative '%s' from %s "
            "→ returning next_tool directive for Claude to chain kite:get_holdings",
            reply, to,
        )
        return {
            "reply_received": True,
            "reply":          reply,
            "action":         "call_kite_get_holdings",
            # Claude reads this and immediately calls the kite MCP get_holdings tool
            "next_tool": {
                "server": "kite",           # must match name in claude_desktop_config.json
                "tool":   "get_holdings",   # Zerodha's official tool name
                "args":   {},
            },
        }

    # ── Non-affirmative ────────────────────────────────────────────────────────
    else:
        logger.info("[REPLY] Non-affirmative '%s' from %s → Nothing", reply, to)
        return {
            "reply_received": True,
            "reply":          reply,
            "action":         "Nothing",
        }


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  CORE SEND FUNCTIONS                                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def send_and_await_reply(to: str, body: str) -> dict[str, Any]:
    """
    Send a WhatsApp message, poll for a reply, then act on it.
    On affirmative reply, returns a next_tool directive for Claude
    to chain kite:get_holdings automatically.
    """
    to = normalise_number(to)

    if ALLOWED_NUMBERS and to not in ALLOWED_NUMBERS:
        return {"success": False, "error": f"Number {to} is not in ALLOWED_NUMBERS."}
    if not TWILIO_WHATSAPP_FROM:
        return {"success": False, "error": "TWILIO_WHATSAPP_FROM is not configured."}

    try:
        client  = get_twilio_client()
        sent_at = datetime.now(timezone.utc)

        message = client.messages.create(
            from_=whatsapp_uri(TWILIO_WHATSAPP_FROM),
            to=whatsapp_uri(to),
            body=body,
        )
        logger.info("[SEND] Message sent to %s — SID: %s", to, message.sid)

        reply  = poll_for_reply(from_number=to, sent_after=sent_at)
        result = handle_reply(reply=reply, to=to)

        return {"success": True, "sid": message.sid, "to": to, **result}

    except TwilioRestException as exc:
        logger.error("[SEND] Twilio error: %s", exc)
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        logger.error("[SEND] Unexpected error: %s", exc)
        return {"success": False, "error": str(exc)}


def send_whatsapp_message(to: str, body: str) -> dict[str, Any]:
    """Simple one-way send — no reply wait."""
    to = normalise_number(to)
    if ALLOWED_NUMBERS and to not in ALLOWED_NUMBERS:
        return {"success": False, "error": f"Number {to} is not in ALLOWED_NUMBERS."}
    if not TWILIO_WHATSAPP_FROM:
        return {"success": False, "error": "TWILIO_WHATSAPP_FROM is not configured."}
    try:
        client  = get_twilio_client()
        message = client.messages.create(
            from_=whatsapp_uri(TWILIO_WHATSAPP_FROM),
            to=whatsapp_uri(to),
            body=body,
        )
        logger.info("[SEND] Sent to %s — SID: %s", to, message.sid)
        return {"success": True, "sid": message.sid, "status": message.status, "to": to}
    except TwilioRestException as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def send_bulk_whatsapp_messages(numbers: list[str], body: str) -> list[dict[str, Any]]:
    return [send_whatsapp_message(n, body) for n in numbers]


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  MCP SERVER                                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

app = Server("whatsapp-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="send_whatsapp_and_await_reply",
            description=(
                "Send a WhatsApp message and wait up to 60 seconds for a reply. "
                "If the reply is Yes/True/1, the response will contain "
                "action='call_kite_get_holdings' and a next_tool block — "
                "YOU MUST then immediately call the 'kite' MCP server's "
                "get_holdings tool and log the result. "
                "Any non-affirmative reply results in action='Nothing'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to":   {"type": "string", "description": "Recipient phone in E.164 format e.g. +919876543210"},
                    "body": {"type": "string", "description": "WhatsApp message text to send"},
                },
                "required": ["to", "body"],
            },
        ),
        Tool(
            name="send_whatsapp_message",
            description="Send a WhatsApp message to one number without waiting for a reply.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to":   {"type": "string", "description": "Recipient phone in E.164 format"},
                    "body": {"type": "string", "description": "Message text"},
                },
                "required": ["to", "body"],
            },
        ),
        Tool(
            name="send_bulk_whatsapp_messages",
            description="Send the same WhatsApp message to multiple numbers.",
            inputSchema={
                "type": "object",
                "properties": {
                    "numbers": {"type": "array", "items": {"type": "string"}, "description": "List of E.164 phone numbers"},
                    "body":    {"type": "string", "description": "Message text"},
                },
                "required": ["numbers", "body"],
            },
        ),
        Tool(
            name="list_allowed_numbers",
            description="Show the configured allowed-list and sender number.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:

    if name == "send_whatsapp_and_await_reply":
        to   = arguments.get("to", "")
        body = arguments.get("body", "")
        result = (
            send_and_await_reply(to, body)
            if to and body
            else {"success": False, "error": "'to' and 'body' are required."}
        )

    elif name == "send_whatsapp_message":
        to   = arguments.get("to", "")
        body = arguments.get("body", "")
        result = (
            send_whatsapp_message(to, body)
            if to and body
            else {"success": False, "error": "'to' and 'body' are required."}
        )

    elif name == "send_bulk_whatsapp_messages":
        numbers = arguments.get("numbers", [])
        body    = arguments.get("body", "")
        result = (
            send_bulk_whatsapp_messages(numbers, body)
            if numbers and body
            else {"success": False, "error": "'numbers' and 'body' are required."}
        )

    elif name == "list_allowed_numbers":
        result = {
            "allowed_numbers": sorted(ALLOWED_NUMBERS) if ALLOWED_NUMBERS else "ALL",
            "from_number":     TWILIO_WHATSAPP_FROM or "NOT SET",
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