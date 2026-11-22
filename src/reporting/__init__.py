"""Automated scouting reporting layer for il-scout-engine."""

from src.reporting.scout_digest import (
    fetch_top_prospects,
    generate_scouting_blurbs,
    render_html_digest,
    export_whatsapp_digest,
    export_pdf_digest,
    run_digest,
)

__all__ = [
    "fetch_top_prospects",
    "generate_scouting_blurbs",
    "render_html_digest",
    "export_whatsapp_digest",
    "export_pdf_digest",
    "run_digest",
]
