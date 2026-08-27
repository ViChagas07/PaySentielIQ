# ============================================================
# PaySentinelIQ — Email Templates (bill.due_soon)
# ============================================================
# Plain templates with no framework dependency. Both text and HTML
# variants are rendered; the provider adds the HTML alternative.
# ============================================================

from __future__ import annotations

import html
from typing import Any


def render_bill_due_soon_email(data: dict[str, Any], *, base_url: str) -> tuple[str, str]:
    """Render (subject, text_body, html_body) for a `bill.due_soon` email.

    Expected keys: user_name, beneficiary, amount, due_date (ISO),
    days_until_due, bill_id, app_link (optional).
    """
    user_name = data.get("user_name") or ""
    beneficiary = data.get("beneficiary") or "beneficiário não informado"
    amount = _fmt_brl(float(data.get("amount") or 0))
    due_date = _fmt_date(data.get("due_date"))
    days = int(data.get("days_until_due") or 0)
    app_link = data.get("app_link") or f"{base_url}/payroll"

    if days <= 0:
        horizon = "vence hoje" if days == 0 else f"está {abs(days)} dia(s) em atraso"
    elif days == 1:
        horizon = "vence amanhã"
    else:
        horizon = f"vence em {days} dias"

    subject = f"Conta próxima do vencimento: {html.escape(beneficiary)} — {amount}"

    text_body = (
        f"Olá {user_name},\n\n"
        f"Uma conta cadastrada no SentinelaPay está próxima do vencimento.\n\n"
        f"  Conta:        {beneficiary}\n"
        f"  Valor:        {amount}\n"
        f"  Vencimento:   {due_date}\n"
        f"  Prazo:        {horizon}\n\n"
        f"Acesse o SentinelaPay para revisar os detalhes:\n{app_link}\n\n"
        f"Atenciosamente,\nEquipe SentinelaPay"
    )

    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;'
        'margin:0 auto;padding:24px;color:#1f2937;">'
        '<h2 style="color:#1e6fff;margin:0 0 16px;">SentinelaPay</h2>'
        f"<p>Olá <strong>{html.escape(user_name)}</strong>,</p>"
        "<p>Uma conta cadastrada está <strong>próxima do vencimento</strong>:</p>"
        '<table style="border-collapse:collapse;width:100%;margin:16px 0;">'
        f'<tr><td style="padding:8px;border-bottom:1px solid #e5e7eb;">Conta</td>'
        f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;"><strong>{html.escape(beneficiary)}</strong></td></tr>'
        f'<tr><td style="padding:8px;border-bottom:1px solid #e5e7eb;">Valor</td>'
        f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;"><strong>{html.escape(amount)}</strong></td></tr>'
        f'<tr><td style="padding:8px;border-bottom:1px solid #e5e7eb;">Vencimento</td>'
        f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;"><strong>{html.escape(due_date)}</strong></td></tr>'
        f'<tr><td style="padding:8px;">Situação</td>'
        f'<td style="padding:8px;"><strong>{html.escape(horizon)}</strong></td></tr>'
        "</table>"
        f'<a href="{html.escape(app_link, quote=True)}" style="display:inline-block;'
        'background:#1e6fff;color:#fff;padding:12px 24px;border-radius:8px;'
        'text-decoration:none;font-weight:bold;">Abrir SentinelaPay</a>'
        '<p style="color:#6b7280;font-size:12px;margin-top:24px;">'
        "Este é um e-mail automático — não responda a esta mensagem.</p>"
        "</div>"
    )

    return subject, text_body, html_body


# ── Formatting helpers ───────────────────────────────────────


def _fmt_brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_date(iso_value: str | None) -> str:
    from datetime import datetime

    if not iso_value:
        return "—"
    try:
        return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return str(iso_value)
