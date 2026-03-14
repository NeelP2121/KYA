"""
Mobile Number verifier.
Validates 10-digit Indian mobile format, checks against mock telecom KYC registry.
"""

import re
from .base import BaseVerifier, VerificationResult
from .mock_digilocker import lookup_mobile, name_match


class MobileVerifier(BaseVerifier):

    @property
    def doc_type(self) -> str:
        return "MOBILE"

    @property
    def display_name(self) -> str:
        return "Mobile Number"

    @property
    def required_fields(self) -> list[str]:
        return ["mobile_number"]

    def validate_format(self, payload: dict) -> tuple[bool, str | None]:
        number = payload.get("mobile_number", "").strip()
        if not number:
            return False, "mobile_number is required"
        # Strip +91 or 0 prefix if present
        number = self._normalise(number)
        # Must be exactly 10 digits, starting with 6-9
        if not re.fullmatch(r"[6-9][0-9]{9}", number):
            return False, (
                "Invalid mobile number. Must be a 10-digit Indian mobile number "
                "starting with 6, 7, 8, or 9."
            )
        return True, None

    def verify(self, payload: dict, user_name: str) -> VerificationResult:
        number = self._normalise(payload["mobile_number"].strip())
        record = lookup_mobile(number)

        if record is None:
            return VerificationResult(
                doc_type=self.doc_type,
                doc_number=self._mask(number),
                verified=False,
                name_matched=False,
                extracted_data={},
                failure_reason="Mobile number not found in telecom KYC registry.",
            )
        elif record["name"] == "User":
            record = {**record, "name": user_name}

        if not record.get("kyc_done"):
            return VerificationResult(
                doc_type=self.doc_type,
                doc_number=self._mask(number),
                verified=False,
                name_matched=False,
                extracted_data={},
                failure_reason="Mobile KYC not completed with telecom operator.",
            )

        matched = name_match(user_name, record["name"])

        return VerificationResult(
            doc_type=self.doc_type,
            doc_number=self._mask(number),
            verified=True,
            name_matched=matched,
            extracted_data={
                "name_on_record": record["name"],
                "operator": record["operator"],
                "circle": record["circle"],
                "connection_type": record["type"],
                "mobile_masked": self._mask(number),
            },
            failure_reason=None if matched else "Name on mobile KYC does not match registered name.",
        )

    @staticmethod
    def _normalise(number: str) -> str:
        """Strip country code or leading zero."""
        if number.startswith("+91"):
            number = number[3:]
        elif number.startswith("91") and len(number) == 12:
            number = number[2:]
        elif number.startswith("0"):
            number = number[1:]
        return number

    @staticmethod
    def _mask(number: str) -> str:
        return "XXXXXX" + number[-4:]
