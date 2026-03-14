"""
PAN Card verifier.
Validates AAAAA9999A format, checks against mock NSDL/DigiLocker, does name match.
"""

import re
from .base import BaseVerifier, VerificationResult
from .mock_digilocker import lookup_pan, name_match


class PANVerifier(BaseVerifier):

    @property
    def doc_type(self) -> str:
        return "PAN"

    @property
    def display_name(self) -> str:
        return "PAN Card"

    @property
    def required_fields(self) -> list[str]:
        return ["pan_number"]

    def validate_format(self, payload: dict) -> tuple[bool, str | None]:
        number = payload.get("pan_number", "").strip().upper()
        if not number:
            return False, "pan_number is required"
        # Standard PAN format: AAAAA9999A (5 letters, 4 digits, 1 letter)
        if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", number):
            return False, (
                "Invalid PAN format. Expected format: AAAAA9999A "
                "(5 uppercase letters, 4 digits, 1 uppercase letter)."
            )
        return True, None

    def verify(self, payload: dict, user_name: str) -> VerificationResult:
        number = payload["pan_number"].strip().upper()
        record = lookup_pan(number)

        if record is None:
            return VerificationResult(
                doc_type=self.doc_type,
                doc_number=number,
                verified=False,
                name_matched=False,
                extracted_data={},
                failure_reason="PAN not found in DigiLocker/NSDL records.",
            )

        if record.get("status") != "ACTIVE":
            return VerificationResult(
                doc_type=self.doc_type,
                doc_number=number,
                verified=False,
                name_matched=False,
                extracted_data={"pan_status": record.get("status")},
                failure_reason=f"PAN is not active. Status: {record.get('status')}",
            )

        matched = name_match(user_name, record["name"])

        return VerificationResult(
            doc_type=self.doc_type,
            doc_number=number,
            verified=True,
            name_matched=matched,
            extracted_data={
                "name_on_record": record["name"],
                "dob": record.get("dob"),
                "pan_type": record["pan_type"],
                "pan_status": record["status"],
            },
            failure_reason=None if matched else "Name on PAN does not match registered name.",
        )
