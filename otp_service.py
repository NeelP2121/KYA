"""
OTP Service.

Fixed OTP: 421596
OTP is valid for 10 minutes from session initiation.
"""

from datetime import datetime, timezone, timedelta

FIXED_OTP = "421596"
OTP_VALIDITY_MINUTES = 10


def verify_otp(submitted_otp: str, session_initiated_at: str) -> tuple[bool, str | None]:
    """
    Verify submitted OTP against the fixed OTP.
    Also checks that the session hasn't expired.

    Returns (is_valid, error_message).
    """
    # Check OTP value
    if submitted_otp.strip() != FIXED_OTP:
        return False, "Incorrect OTP. Please try again."

    # Check expiry
    initiated = datetime.fromisoformat(session_initiated_at)
    expiry = initiated + timedelta(minutes=OTP_VALIDITY_MINUTES)
    now = datetime.now(timezone.utc)

    if now > expiry:
        return False, f"OTP has expired. Sessions are valid for {OTP_VALIDITY_MINUTES} minutes. Please initiate KYC again."

    return True, None
