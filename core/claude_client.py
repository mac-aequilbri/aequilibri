"""
Shared Claude API client for æquilibri POC.
If ANTHROPIC_API_KEY is set, calls real Claude Sonnet 4.6.
Otherwise returns a demo/simulated response.
"""
import json
import os
from datetime import datetime
from django.conf import settings


def _get_api_key() -> str:
    """Return the Anthropic API key, checking both Django settings and os.environ.

    settings.py reads the key at import time via os.environ.get().  If the system
    environment already had ANTHROPIC_API_KEY set as an empty string before
    load_dotenv ran (common on Windows dev machines), settings ends up empty.
    Checking os.environ here as a fallback ensures the .env value always wins.
    """
    return getattr(settings, 'ANTHROPIC_API_KEY', '') or os.environ.get('ANTHROPIC_API_KEY', '')


def call_claude_vision(system_prompt: str, user_text: str, image_b64: str,
                       media_type: str = 'image/png', max_tokens: int = 2048) -> dict:
    """
    Call Claude with a single base64 image + text prompt.
    Returns { "content": str, "demo_mode": bool }
    """
    api_key = _get_api_key()
    if not api_key:
        return {"content": '{"sections":[],"notes":"Demo mode — no API key","confidence":"low"}', "demo_mode": True}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-opus-4-6",   # Opus required — roof polygon extraction is complex
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text", "text": user_text},
                ],
            }],
        )
        text = "\n".join(b.text for b in response.content if b.type == "text")
        return {"content": text, "demo_mode": False}
    except Exception as e:
        return {"content": f'{{"sections":[],"notes":"Claude error: {e}","confidence":"low"}}', "demo_mode": False}


def call_claude_vision_multi(system_prompt: str, user_text: str, images: list,
                              max_tokens: int = 1024) -> dict:
    """
    Call Claude with multiple base64 images + a text prompt.

    images: list of dicts with keys:
        "b64"        — base64-encoded image data
        "media_type" — e.g. "image/png" or "image/jpeg"
        "label"      — descriptive label (not sent to Claude, used locally)

    Returns { "content": str, "demo_mode": bool }
    """
    api_key = _get_api_key()
    if not api_key:
        return {
            "content": (
                '{"solar_panels":false,"solar_panels_confidence":"low",'
                '"solar_hw":false,"solar_hw_confidence":"low",'
                '"roof_style":"unknown","roof_style_confidence":"low",'
                '"roof_material":"unknown","roof_material_confidence":"low",'
                '"storeys":null,"condition":"unknown","other_features":[],'
                '"notes":"Demo mode — no API key"}'
            ),
            "demo_mode": True,
        }
    try:
        import anthropic
        content = []
        for img in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.get("media_type", "image/jpeg"),
                    "data": img["b64"],
                },
            })
        content.append({"type": "text", "text": user_text})

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )
        text = "\n".join(b.text for b in response.content if b.type == "text")
        return {"content": text, "demo_mode": False}
    except Exception as e:
        return {
            "content": (
                f'{{"solar_panels":false,"solar_panels_confidence":"low",'
                f'"solar_hw":false,"solar_hw_confidence":"low",'
                f'"roof_style":"unknown","roof_style_confidence":"low",'
                f'"roof_material":"unknown","roof_material_confidence":"low",'
                f'"storeys":null,"condition":"unknown","other_features":[],'
                f'"notes":"Claude error: {e}"}}'
            ),
            "demo_mode": False,
        }


def call_claude(system_prompt: str, user_message: str, tools: list = None,
                max_tokens: int = 1024) -> dict:
    """
    Call Claude and return a dict:
      { "content": str, "tool_uses": list, "demo_mode": bool }
    """
    api_key = _get_api_key()
    if not api_key:
        return _demo_response(system_prompt, user_message, tools)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        params = {
            "model": "claude-sonnet-4-6",
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        if tools:
            params["tools"] = tools
        response = client.messages.create(**params)
        text_parts = []
        tool_uses = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append({"name": block.name, "input": block.input})
        return {"content": "\n".join(text_parts), "tool_uses": tool_uses, "demo_mode": False}
    except Exception as e:
        return {"content": f"[Claude API error: {e}]", "tool_uses": [], "demo_mode": False}


def _demo_response(system_prompt: str, user_message: str, tools: list) -> dict:
    """
    Demo mode: return a plausible canned response when no API key is set.
    """
    msg_lower = user_message.lower()
    ts = datetime.now().strftime("%H:%M")

    if any(w in msg_lower for w in ["budget", "cost", "spend"]):
        reply = (
            f"**[Demo Mode — {ts}]** Based on the project data, the current budget "
            "utilisation sits at **68%** across all phases. Phase 3 (Framing & Structure) "
            "shows a 12% variance — $8,400 over estimate, primarily driven by timber "
            "price increases. Phase 1 (Site Works) closed within 2% of estimate. "
            "No immediate action required, but recommend reviewing Phase 4 procurement "
            "before placing orders."
        )
    elif any(w in msg_lower for w in ["action", "task", "overdue", "due"]):
        reply = (
            f"**[Demo Mode — {ts}]** I found **3 overdue action items** for your review:\n\n"
            "1. **Electrical rough-in inspection** — due 3 days ago (Owner: Jack Henderson)\n"
            "2. **Render quote finalisation** — due yesterday (Owner: Claudia Salem)\n"
            "3. **Soil test report sign-off** — due 5 days ago (Owner: Site Supervisor)\n\n"
            "Would you like me to update the due dates or send reminder notifications?"
        )
    elif any(w in msg_lower for w in ["risk", "hazard", "danger"]):
        reply = (
            f"**[Demo Mode — {ts}]** The current risk register has **2 HIGH** and "
            "**4 MEDIUM** risks active. The highest-priority item is: "
            "*Wet weather delays to slab pour* (Likelihood: High, Impact: High). "
            "Mitigation: monitor BOM 7-day forecast; have pump-out contractor on standby. "
            "No new risks identified since last session."
        )
    elif any(w in msg_lower for w in ["vendor", "supplier", "contractor"]):
        reply = (
            f"**[Demo Mode — {ts}]** Lighthouse Noosa (concrete supplier) has 3 invoices "
            "pending reconciliation. Per LRN-0030, I will cross-check CASHFLOWS before "
            "proposing any write to their records. Current total outstanding: $42,800. "
            "Recommend confirming the 15-May delivery before processing payment."
        )
    elif any(w in msg_lower for w in ["hello", "hi", "start", "session"]):
        reply = (
            f"**[Demo Mode — {ts}]** Good morning! Starting session for Dulong Downs. "
            "I've loaded 32 LEARNING_RULES and reviewed the CHANGE_LOG. "
            "No writes since last session (48 hours ago). "
            "ACTION_HUB shows 3 items overdue. How can I help you today?"
        )
    else:
        reply = (
            f"**[Demo Mode — {ts}]** I've queried the Dulong Downs database. "
            f"Your question: *\"{user_message[:80]}\"* — "
            "Here's what the data shows: the project is currently 54% complete "
            "overall, on track for the August 2026 target completion. "
            "Would you like me to drill into a specific phase, table, or topic?"
        )

    return {
        "content": reply,
        "tool_uses": [{"name": "get_records", "input": {"table": "ACTION_HUB", "filter": ""}}],
        "demo_mode": True,
    }
