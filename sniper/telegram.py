"""Envío de alertas a Telegram vía Bot API (gratuita)."""
from __future__ import annotations

import html

import aiohttp

API = "https://api.telegram.org/bot{token}/sendMessage"


def format_alert(title: str | None, asin: str, market: str, price: float,
                 reference: float | None, discount: float, url: str, source: str) -> str:
    name = html.escape((title or asin)[:150])
    ref = f"<s>{reference:.2f} €</s> → " if reference else ""
    return (
        f"🚨 <b>POSIBLE ERROR DE PRECIO</b> ({market})\n\n"
        f"{name}\n\n"
        f"💶 {ref}<b>{price:.2f} €</b>\n"
        f"📉 Descuento: <b>-{discount:.0f}%</b> (vs {source})\n"
        f"🔖 ASIN: <code>{asin}</code>\n\n"
        f'<a href="{html.escape(url)}">Abrir en Amazon</a>'
    )


async def send_message(session: aiohttp.ClientSession, token: str, chat_id: str,
                       text: str) -> tuple[bool, str]:
    if not token or not chat_id:
        return False, "Telegram no configurado"
    try:
        async with session.post(
            API.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": False},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                return True, "ok"
            body = await resp.text()
            return False, f"HTTP {resp.status}: {body[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
