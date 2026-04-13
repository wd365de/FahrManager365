import re

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.planner_settings import get_planner_setting_value
from app.settings import SCHOOL_NAME, SCHOOL_PRIMARY_COLOR

router = APIRouter()

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$")


def _safe_color(value: str, fallback: str = "#e11d48") -> str:
    value = (value or "").strip()
    return value if _HEX_RE.match(value) else fallback


@router.get("/theme.css")
def theme_css(db: Session = Depends(get_db)):
    color = _safe_color(get_planner_setting_value(db, SCHOOL_PRIMARY_COLOR), "#e11d48")
    school_name = (get_planner_setting_value(db, SCHOOL_NAME) or "Fahrschule").strip()
    # Derive a darker shade for hover/active states (~20% darker)
    css = f""":root {{
  --school-color: {color};
  --school-color-dark: color-mix(in srgb, {color} 80%, black);
  --school-name: "{school_name}";
}}
.app-sidebar-rail {{
  background: var(--school-color) !important;
}}
.rail-link.active,
.rail-link:hover {{
  color: var(--school-color) !important;
}}
.rail-logo {{
  background: #ffffff !important;
  color: var(--school-color) !important;
}}
"""
    return Response(content=css, media_type="text/css", headers={"Cache-Control": "no-store"})


@router.get("/api/school-settings")
def school_settings_api(db: Session = Depends(get_db)):
    return {
        "name": (get_planner_setting_value(db, SCHOOL_NAME) or "Fahrschule").strip(),
        "color": _safe_color(get_planner_setting_value(db, SCHOOL_PRIMARY_COLOR), "#e11d48"),
    }
