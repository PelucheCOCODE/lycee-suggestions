"""Génération de PDF pour l'historique des suggestions et propositions officielles."""
from io import BytesIO
from datetime import datetime, timezone


def _fmt_date(dt):
    if not dt:
        return "—"
    if hasattr(dt, "isoformat"):
        return dt.strftime("%d/%m/%Y %H:%M") if hasattr(dt, "strftime") else str(dt)
    return str(dt)


def _clean_text(s):
    if not s:
        return ""
    return str(s).replace("\r", "").strip()


def build_suggestion_pdf(suggestion) -> bytes:
    """Génère un PDF pour une suggestion (élève)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(name="CustomTitle", parent=styles["Heading1"], fontSize=16, spaceAfter=12)
    heading_style = ParagraphStyle(name="CustomHeading", parent=styles["Heading2"], fontSize=12, spaceAfter=8)
    body_style = styles["Normal"]

    story = []
    story.append(Paragraph("Historique — Suggestion", title_style))
    story.append(Paragraph(f"<b>#{suggestion.id}</b>", body_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Titre</b>", heading_style))
    story.append(Paragraph(_clean_text(suggestion.title).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), body_style))
    story.append(Spacer(1, 8))

    if suggestion.subtitle:
        story.append(Paragraph("<b>Sous-titre</b>", heading_style))
        story.append(Paragraph(_clean_text(suggestion.subtitle).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), body_style))
        story.append(Spacer(1, 8))

    story.append(Paragraph("<b>Texte original</b>", heading_style))
    story.append(Paragraph(_clean_text(suggestion.original_text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), body_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Informations</b>", heading_style))
    vote_for = getattr(suggestion, "vote_for", 0) or 0
    vote_against = getattr(suggestion, "vote_against", 0) or 0
    vote_count = suggestion.vote_count or 0
    needs_debate = getattr(suggestion, "needs_debate", False)

    info_data = [
        ["Catégorie", suggestion.category or "—"],
        ["Statut", suggestion.status or "—"],
        ["Lieu", suggestion.location.name if suggestion.location else "—"],
        ["Créée le", _fmt_date(suggestion.created_at)],
        ["Mots-clés", ", ".join(suggestion.keywords.split(",")[:10]) if suggestion.keywords else "—"],
    ]
    if needs_debate:
        info_data.extend([
            ["Pour", str(vote_for)],
            ["Contre", str(vote_against)],
        ])
    else:
        info_data.append(["Soutiens", str(vote_count)])

    t = Table(info_data, colWidths=[4*cm, 12*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (0, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    story.append(Paragraph("<b>Résultat des votes</b>", heading_style))
    if needs_debate:
        total = vote_for + vote_against or 1
        pct_for = (vote_for / total * 100) if total else 0
        pct_against = (vote_against / total * 100) if total else 0
        chart_data = [
            ["Pour", str(vote_for), f"{pct_for:.0f}%", "█" * int(pct_for / 5) + "░" * (20 - int(pct_for / 5))],
            ["Contre", str(vote_against), f"{pct_against:.0f}%", "█" * int(pct_against / 5) + "░" * (20 - int(pct_against / 5))],
        ]
    else:
        chart_data = [["Soutiens", str(vote_count), "100%", "█" * 20]]
    chart_table = Table(chart_data, colWidths=[3*cm, 2*cm, 12*cm])
    chart_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(chart_table)
    story.append(Spacer(1, 16))

    if needs_debate and hasattr(suggestion, "arguments"):
        args_for = [a for a in suggestion.arguments if a.side == "for" and a.status == "approved"]
        args_against = [a for a in suggestion.arguments if a.side == "against" and a.status == "approved"]
        if args_for or args_against:
            story.append(Paragraph("<b>Arguments pour</b>", heading_style))
            for a in args_for:
                txt = (a.summary or a.original_text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(f"• {txt[:500]}", body_style))
            story.append(Spacer(1, 8))
            story.append(Paragraph("<b>Arguments contre</b>", heading_style))
            for a in args_against:
                txt = (a.summary or a.original_text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(f"• {txt[:500]}", body_style))
            story.append(Spacer(1, 12))

    if getattr(suggestion, "ai_proportion", None) is not None:
        story.append(Paragraph("<b>Évaluation IA</b>", heading_style))
        story.append(Paragraph(
            f"Impact: {int((suggestion.ai_proportion or 0)*100)}% — "
            f"Faisabilité: {int((getattr(suggestion, 'ai_feasibility', 0.5) or 0.5)*100)}% — "
            f"Coût: {int((getattr(suggestion, 'ai_cost', 0.5) or 0.5)*100)}%",
            body_style
        ))

    doc.build(story)
    return buf.getvalue()


def build_proposal_pdf(proposal) -> bytes:
    """Génère un PDF pour une proposition officielle CVL."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(name="CustomTitle", parent=styles["Heading1"], fontSize=16, spaceAfter=12)
    heading_style = ParagraphStyle(name="CustomHeading", parent=styles["Heading2"], fontSize=12, spaceAfter=8)
    body_style = styles["Normal"]

    story = []
    story.append(Paragraph("Historique — Proposition Officielle CVL", title_style))
    story.append(Paragraph(f"<b>#{proposal.id}</b>", body_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Contenu</b>", heading_style))
    content = _clean_text(proposal.content or "")
    if content:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")
            txt = soup.get_text(separator="\n")
        except Exception:
            txt = content.replace("<br>", "\n").replace("<br/>", "\n").replace("</p>", "\n")
        txt = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(txt[:2000], body_style))
    else:
        story.append(Paragraph("—", body_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Informations</b>", heading_style))
    vote_for = proposal.vote_for or 0
    vote_against = proposal.vote_against or 0
    needs_debate = proposal.needs_debate or False

    info_data = [
        ["Statut", proposal.status or "—"],
        ["Active", "Oui" if proposal.active else "Non"],
        ["Créée le", _fmt_date(proposal.created_at)],
        ["Publiée le", _fmt_date(proposal.published_at)],
    ]
    if needs_debate:
        info_data.extend([["Pour", str(vote_for)], ["Contre", str(vote_against)]])
    else:
        info_data.append(["Soutiens", str(vote_for)])

    t = Table(info_data, colWidths=[4*cm, 12*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (0, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    story.append(Paragraph("<b>Résultat des votes</b>", heading_style))
    if needs_debate:
        total = vote_for + vote_against or 1
        pct_for = (vote_for / total * 100) if total else 0
        pct_against = (vote_against / total * 100) if total else 0
        chart_data = [
            ["Pour", str(vote_for), f"{pct_for:.0f}%", "█" * int(pct_for / 5) + "░" * (20 - int(pct_for / 5))],
            ["Contre", str(vote_against), f"{pct_against:.0f}%", "█" * int(pct_against / 5) + "░" * (20 - int(pct_against / 5))],
        ]
    else:
        chart_data = [["Soutiens", str(vote_for), "100%", "█" * 20]]
    chart_table = Table(chart_data, colWidths=[3*cm, 2*cm, 12*cm])
    chart_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(chart_table)
    story.append(Spacer(1, 16))

    if needs_debate and hasattr(proposal, "arguments"):
        args_for = [a for a in proposal.arguments if a.side == "for" and a.status == "approved"]
        args_against = [a for a in proposal.arguments if a.side == "against" and a.status == "approved"]
        if args_for or args_against:
            story.append(Paragraph("<b>Arguments pour</b>", heading_style))
            for a in args_for:
                txt = (a.summary or a.original_text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(f"• {txt[:500]}", body_style))
            story.append(Spacer(1, 8))
            story.append(Paragraph("<b>Arguments contre</b>", heading_style))
            for a in args_against:
                txt = (a.summary or a.original_text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(f"• {txt[:500]}", body_style))

    if getattr(proposal, "proportion", None) is not None or getattr(proposal, "feasibility", None) is not None:
        story.append(Spacer(1, 12))
        story.append(Paragraph("<b>Évaluation IA</b>", heading_style))
        story.append(Paragraph(
            f"Impact: {int((getattr(proposal, 'proportion', 0) or 0)*100)}% — "
            f"Faisabilité: {int((getattr(proposal, 'feasibility', 0.5) or 0.5)*100)}% — "
            f"Coût: {int((getattr(proposal, 'cost', 0.5) or 0.5)*100)}%",
            body_style
        ))

    doc.build(story)
    return buf.getvalue()
