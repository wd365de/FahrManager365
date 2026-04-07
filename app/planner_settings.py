from sqlalchemy.orm import Session

from app.models import AppSetting
from app.settings import PLANNER_SETTING_DEFINITIONS


TRUE_VALUES = {"1", "true", "yes", "on"}


def ensure_default_planner_settings(db: Session) -> None:
    changed = False
    for key, definition in PLANNER_SETTING_DEFINITIONS.items():
        setting = db.query(AppSetting).filter(AppSetting.key == key).first()
        if not setting:
            db.add(AppSetting(key=key, value=str(definition["default"])))
            changed = True
    if changed:
        db.commit()


def get_planner_setting_value(db: Session, key: str) -> str:
    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
    if setting:
        return setting.value
    definition = PLANNER_SETTING_DEFINITIONS.get(key)
    if definition:
        return str(definition["default"])
    return ""


def get_planner_setting_bool(db: Session, key: str) -> bool:
    return get_planner_setting_value(db, key).strip().lower() in TRUE_VALUES


def set_planner_setting_value(db: Session, key: str, value: str) -> None:
    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
    if not setting:
        setting = AppSetting(key=key, value=value)
        db.add(setting)
    else:
        setting.value = value
    db.commit()
