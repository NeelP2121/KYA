"""
Base verifier interface.

To add a new document type:
1. Create a new file in /verifiers/
2. Subclass BaseVerifier
3. Register it in verifiers/registry.py

That's it. No other files need to change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class VerificationResult:
    doc_type: str
    doc_number: str
    verified: bool
    name_matched: bool
    extracted_data: dict[str, Any]
    failure_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "doc_number": self.doc_number,
            "verified": self.verified,
            "name_matched": self.name_matched,
            "extracted_data": self.extracted_data,
            "failure_reason": self.failure_reason,
        }


class BaseVerifier(ABC):
    """
    Abstract base class for all KYC document verifiers.
    Each verifier handles exactly one document type.
    """

    @property
    @abstractmethod
    def doc_type(self) -> str:
        """Unique identifier for this document type. E.g. 'AADHAAR'"""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name. E.g. 'Aadhaar Card'"""
        ...

    @property
    @abstractmethod
    def required_fields(self) -> list[str]:
        """Fields that must be present in the input payload."""
        ...

    @abstractmethod
    def validate_format(self, payload: dict) -> tuple[bool, str | None]:
        """
        Validate the format of the document number / fields.
        Returns (is_valid, error_message).
        Called before hitting mock DigiLocker.
        """
        ...

    @abstractmethod
    def verify(self, payload: dict, user_name: str) -> VerificationResult:
        """
        Run the mock DigiLocker verification.
        payload contains all required_fields.
        user_name is used for name-match checks.
        """
        ...
