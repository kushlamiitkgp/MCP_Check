# WhatsApp MCP Server

Send WhatsApp messages from your local machine via Claude (or any MCP client)
using the **Twilio WhatsApp API**.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | `python --version` |
| Twilio account | [console.twilio.com](https://console.twilio.com) — free trial is enough |
| WhatsApp sandbox OR approved sender | See step 2 below |

---

## 1 — Install dependencies

```bash
pip install -r requirements.txt
```

---

## 2 — Twilio WhatsApp setup

### Option A — Sandbox (quickest, free)
1. In the Twilio Console go to **Messaging → Try it out → Send a WhatsApp message**.
2. Follow the on-screen instructions: each recipient must send a one-time opt-in
   message from their phone to the sandbox number (`+14155238886`) — usually
   something like `join <word>-<word>`.
3. Your `TWILIO_WHATSAPP_FROM` = `+14155238886`.

### Option B — Approved WhatsApp Business sender (production)
1. Apply for a WhatsApp-enabled Twilio number in the Console.
2. Use that number as `TWILIO_WHATSAPP_FROM`.

---

## 3 — Configure credentials

```bash
cp .env.example .env
# edit .env with your real values
```

Key variables:

| Variable | Description |
|---|---|
| `TWILIO_ACCOUNT_SID` | From your Twilio Console dashboard |
| `TWILIO_AUTH_TOKEN` | From your Twilio Console dashboard |
| `TWILIO_WHATSAPP_FROM` | Your Twilio WhatsApp sender number |
| `ALLOWED_NUMBERS` | Optional comma-separated allow-list (E.164 format) |

---

## 4 — Run the server

```bash
python server.py
```

The server communicates over **stdio** (standard MCP transport).

---

## 5 — Connect to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "whatsapp": {
      "command": "python",
      "args": ["/absolute/path/to/whatsapp-mcp/server.py"],
      "env": {
        "TWILIO_ACCOUNT_SID": "ACxxxx",
        "TWILIO_AUTH_TOKEN": "xxxx",
        "TWILIO_WHATSAPP_FROM": "+14155238886",
        "ALLOWED_NUMBERS": "+91XXX,+1XYZ081234567"
      }
    }
  }
}
```

Restart Claude Desktop — you'll see the 🔨 tool icon appear in the chat bar.

---

## Available tools

### `send_whatsapp_message`
Send a message to one number.
```
to:   +91XXX
body: Hello from Claude!
```

### `send_bulk_whatsapp_messages`
Send the same message to multiple numbers at once.
```
numbers: ["+91XXX", "+1XYZ4081234567"]
body:    Bulk message from Claude!
```

### `list_allowed_numbers`
Check which numbers are configured and what sender is active.

---

## Security notes

* Never commit your real `.env` file — it's in `.gitignore` by default.
* Use `ALLOWED_NUMBERS` to prevent the server from messaging arbitrary numbers.
* Rotate your `TWILIO_AUTH_TOKEN` if it is ever exposed.
