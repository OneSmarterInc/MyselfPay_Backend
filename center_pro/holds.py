"""OBSOLETE experiment: patient payments are outside Center Pro scope. Not integrated."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum


MAX_HOLD = timedelta(hours=72)


class HoldStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


@dataclass(frozen=True)
class PaymentHold:
    payment_started_at: datetime
    appointment_starts_at: datetime
    payment_cutoff_at: datetime
    expires_at: datetime
    status: HoldStatus = HoldStatus.PENDING

    @classmethod
    def start(
        cls,
        payment_started_at: datetime,
        appointment_starts_at: datetime,
        payment_cutoff_at: datetime,
    ) -> "PaymentHold":
        """Start a hold when the payment step begins.

        The caller provides the appointment cutoff because its lead time has not
        yet been agreed. All comparisons use timezone-aware datetimes.
        """
        for name, value in (
            ("payment_started_at", payment_started_at),
            ("appointment_starts_at", appointment_starts_at),
            ("payment_cutoff_at", payment_cutoff_at),
        ):
            _require_aware(value, name)
        started_utc = payment_started_at.astimezone(timezone.utc)
        appointment_utc = appointment_starts_at.astimezone(timezone.utc)
        cutoff_utc = payment_cutoff_at.astimezone(timezone.utc)
        if cutoff_utc > appointment_utc:
            raise ValueError("payment cutoff cannot be after the appointment")
        expires_at = min(started_utc + MAX_HOLD, cutoff_utc)
        if expires_at <= started_utc:
            raise ValueError("payment deadline must be after payment starts")
        return cls(
            payment_started_at=started_utc,
            appointment_starts_at=appointment_utc,
            payment_cutoff_at=cutoff_utc,
            expires_at=expires_at,
        )

    def confirm(self, at: datetime) -> "PaymentHold":
        """Confirm only a pending hold before the payment deadline."""
        _require_aware(at, "at")
        at = at.astimezone(timezone.utc)
        if self.status == HoldStatus.CONFIRMED:
            return self
        if self.status != HoldStatus.PENDING or at >= self.expires_at:
            raise ValueError("hold is no longer payable")
        return replace(self, status=HoldStatus.CONFIRMED)

    def expire(self, at: datetime) -> "PaymentHold":
        """Release a pending hold once its deadline has passed."""
        _require_aware(at, "at")
        at = at.astimezone(timezone.utc)
        if self.status != HoldStatus.PENDING or at < self.expires_at:
            return self
        return replace(self, status=HoldStatus.EXPIRED)
