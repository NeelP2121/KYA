"""
Aadhaar Card verifier.
Validates 12-digit format, checks against mock DigiLocker, does name match.
"""

import re
from .base import BaseVerifier, VerificationResult
from .mock_digilocker import lookup_aadhaar, name_match


class AadhaarVerifier(BaseVerifier):

    @property
    def doc_type(self) -> str:
        return "AADHAAR"

    @property
    def display_name(self) -> str:
        return "Aadhaar Card"

    @property
    def required_fields(self) -> list[str]:
        return ["aadhaar_number"]

    def validate_format(self, payload: dict) -> tuple[bool, str | None]:
        number = payload.get("aadhaar_number", "").strip()
        if not number:
            return False, "aadhaar_number is required"
        # Must be exactly 12 digits; must not start with 0 or 1
        if not re.fullmatch(r"[2-9][0-9]{11}", number):
            return False, (
                "Invalid Aadhaar format. Must be 12 digits and must not start with 0 or 1."
            )
        return True, None

    def verify(self, payload: dict, user_name: str) -> VerificationResult:
        number = payload["aadhaar_number"].strip()
        record = lookup_aadhaar(number)

        if record is None:
            return VerificationResult(
                doc_type=self.doc_type,
                doc_number=self._mask(number),
                verified=False,
                name_matched=False,
                extracted_data={},
                failure_reason="Aadhaar number not found in DigiLocker records.",
            )

        matched = name_match(user_name, record["name"])

        # Mask the Aadhaar number in stored data (UIDAI standard: show last 4)
        masked = self._mask(number)

        return VerificationResult(
            doc_type=self.doc_type,
            doc_number=masked,
            verified=True,
            name_matched=matched,
            extracted_data={
                "name_on_record": record["name"],
                "dob": record["dob"],
                "gender": record["gender"],
                "state": record["state"],
                "pincode": record["pincode"],
                "aadhaar_masked": masked,
            },
            failure_reason=None if matched else "Name on Aadhaar does not match registered name.",
        )

    @staticmethod
    def _mask(number: str) -> str:
        return "XXXX-XXXX-" + number[-4:]
