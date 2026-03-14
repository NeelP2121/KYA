"""
OTP Service.

Dummy OTP verification: any non-empty OTP is accepted.
OTP is valid for 10 minutes from session initiation.
"""

from datetime import datetime, timezone, timedelta

FIXED_OTP = "DUMMY"
OTP_VALIDITY_MINUTES = 10


def verify_otp(submitted_otp: str, session_initiated_at: str) -> tuple[bool, str | None]:
    """
    Verify submitted OTP against the dummy OTP rule.
    Also checks that the session hasn't expired.

    Returns (is_valid, error_message).
    """
    # Accept any non-empty OTP for the simplified mock flow.
    if not submitted_otp.strip():
        return False, "OTP is required. Please enter any OTP to continue."

    # Check expiry
    initiated = datetime.fromisoformat(session_initiated_at)
    expiry = initiated + timedelta(minutes=OTP_VALIDITY_MINUTES)
    now = datetime.now(timezone.utc)

    if now > expiry:
        return False, f"OTP has expired. Sessions are valid for {OTP_VALIDITY_MINUTES} minutes. Please initiate KYC again."

    return True, None
