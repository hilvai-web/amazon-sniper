"""Envío de alertas a Telegram vía Bot API (gratuita)."""
from __future__ import annotations

import html

import aiohttp

API = "https://api.telegram.org/bot{token}/sendMessage"
BASE = "https://api.telegram.org/bot{token}/"


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


async def send_photo(session: aiohttp.ClientSession, token: str, chat_id: str,
                     photo: bytes, caption: str) -> bool:
    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    form.add_field("caption", caption)
    form.add_field("photo", photo, filename="captcha.jpg", content_type="image/jpeg")
    try:
        async with session.post(BASE.format(token=token) + "sendPhoto", data=form,
                                timeout=aiohttp.ClientTimeout(total=30)) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


async def get_updates(session: aiohttp.ClientSession, token: str, offset: int,
                      wait: int = 25) -> list[dict]:
    """Mensajes nuevos que ha recibido el bot (espera hasta `wait` s si no hay)."""
    try:
        async with session.get(BASE.format(token=token) + "getUpdates",
                               params={"offset": offset, "timeout": wait, "allowed_updates": '["message"]'},
                               timeout=aiohttp.ClientTimeout(total=wait + 10)) as resp:
            data = await resp.json(content_type=None)
            return data.get("result", []) if data.get("ok") else []
    except Exception:  # noqa: BLE001
        return []


async def next_offset(session: aiohttp.ClientSession, token: str) -> int:
    """Offset para leer solo los mensajes que lleguen a partir de ahora."""
    updates = await get_updates(session, token, -1, wait=0)
    return updates[-1]["update_id"] + 1 if updates else 0
