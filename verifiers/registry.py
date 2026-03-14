"""
Verifier Registry.

This is the ONLY file that needs to change when adding a new document type.

Steps to add a new verifier (e.g. Passport):
1. Create verifiers/passport_verifier.py, subclass BaseVerifier
2. Import it here and add to VERIFIERS list
Done.
"""

from .base import BaseVerifier
from .aadhaar_verifier import AadhaarVerifier
from .pan_verifier import PANVerifier
from .mobile_verifier import MobileVerifier

# ─────────────────────────────────────────────────────────
# Registry: add new verifiers to this list
# ─────────────────────────────────────────────────────────
VERIFIERS: list[BaseVerifier] = [
    AadhaarVerifier(),
    PANVerifier(),
    MobileVerifier(),
]

# Build a lookup dict: doc_type → verifier instance
_REGISTRY: dict[str, BaseVerifier] = {v.doc_type: v for v in VERIFIERS}


def get_verifier(doc_type: str) -> BaseVerifier | None:
    """Return the verifier for a given doc_type, or None if unsupported."""
    return _REGISTRY.get(doc_type.upper())


def supported_doc_types() -> list[dict]:
    """Return metadata about all supported document types."""
    return [
        {
            "doc_type": v.doc_type,
            "display_name": v.display_name,
            "required_fields": v.required_fields,
        }
        for v in VERIFIERS
    ]
