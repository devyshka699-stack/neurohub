"""Генерация промокодов."""

import secrets
import string
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import PromoCode

_ALPHABET = string.ascii_uppercase + string.digits
# Исключаем похожие символы, чтобы код было легко продиктовать
_ALPHABET = _ALPHABET.translate(str.maketrans("", "", "O0I1"))


def _generate_code(prefix: str) -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    return f"{prefix}-{body}"


def create_promo(
    db: Session,
    user_id: int,
    discount_percent: int,
    reason: str,
    valid_hours: int | None = None,
    prefix: str = "AI",
) -> PromoCode:
    # гарантируем уникальность кода
    for _ in range(10):
        code = _generate_code(prefix)
        if not db.query(PromoCode).filter(PromoCode.code == code).first():
            break
    expires_at = (
        datetime.utcnow() + timedelta(hours=valid_hours) if valid_hours else None
    )
    promo = PromoCode(
        code=code,
        user_id=user_id,
        discount_percent=discount_percent,
        reason=reason,
        expires_at=expires_at,
    )
    db.add(promo)
    db.commit()
    return promo
