"""
Mock DigiLocker — simulates the government DigiLocker backend.

Known records are preserved for deterministic demos, but the mock also
accepts any correctly formatted Aadhaar, PAN, or mobile number by
synthesizing a matching record on the fly.
"""

# ─────────────────────────────────────────────────────────
# AADHAAR test records
# Format: 12-digit number → citizen record
# ─────────────────────────────────────────────────────────
AADHAAR_RECORDS: dict[str, dict] = {
    "999999999999": {
        "name": "Rahul Sharma",
        "dob": "1990-05-15",
        "gender": "M",
        "address": "12, MG Road, Bengaluru, Karnataka - 560001",
        "state": "Karnataka",
        "pincode": "560001",
    },
    "888888888888": {
        "name": "Priya Mehta",
        "dob": "1985-11-22",
        "gender": "F",
        "address": "45, Andheri West, Mumbai, Maharashtra - 400058",
        "state": "Maharashtra",
        "pincode": "400058",
    },
    "777777777777": {
        "name": "Amit Kumar Singh",
        "dob": "1995-03-08",
        "gender": "M",
        "address": "78, Connaught Place, New Delhi - 110001",
        "state": "Delhi",
        "pincode": "110001",
    },
    "666666666666": {
        "name": "Sneha Iyer",
        "dob": "1992-07-30",
        "gender": "F",
        "address": "23, T Nagar, Chennai, Tamil Nadu - 600017",
        "state": "Tamil Nadu",
        "pincode": "600017",
    },
}

# ─────────────────────────────────────────────────────────
# PAN test records
# Format: 10-char alphanumeric → taxpayer record
# ─────────────────────────────────────────────────────────
PAN_RECORDS: dict[str, dict] = {
    "ABCDE1234F": {
        "name": "Rahul Sharma",
        "dob": "1990-05-15",
        "pan_type": "Individual",
        "status": "ACTIVE",
    },
    "PQRST5678G": {
        "name": "Priya Mehta",
        "dob": "1985-11-22",
        "pan_type": "Individual",
        "status": "ACTIVE",
    },
    "LMNOP9012H": {
        "name": "Amit Kumar Singh",
        "dob": "1995-03-08",
        "pan_type": "Individual",
        "status": "ACTIVE",
    },
    "UVWXY3456I": {
        "name": "Sneha Iyer",
        "dob": "1992-07-30",
        "pan_type": "Individual",
        "status": "ACTIVE",
    },
    "ZZZZZ9999Z": {
        "name": "Test Business Entity",
        "dob": None,
        "pan_type": "Company",
        "status": "ACTIVE",
    },
}

# ─────────────────────────────────────────────────────────
# MOBILE test records
# Format: 10-digit number → telecom record
# ─────────────────────────────────────────────────────────
MOBILE_RECORDS: dict[str, dict] = {
    "9876543210": {
        "name": "Rahul Sharma",
        "operator": "Airtel",
        "circle": "Karnataka",
        "type": "Prepaid",
        "kyc_done": True,
    },
    "9123456789": {
        "name": "Priya Mehta",
        "operator": "Jio",
        "circle": "Maharashtra",
        "type": "Postpaid",
        "kyc_done": True,
    },
    "9000000001": {
        "name": "Amit Kumar Singh",
        "operator": "Vi",
        "circle": "Delhi",
        "type": "Prepaid",
        "kyc_done": True,
    },
    "9000000002": {
        "name": "Sneha Iyer",
        "operator": "BSNL",
        "circle": "Tamil Nadu",
        "type": "Postpaid",
        "kyc_done": True,
    },
}


def lookup_aadhaar(number: str) -> dict | None:
    number = number.strip()
    record = AADHAAR_RECORDS.get(number)
    if record:
        return record
    if not number:
        return None
    return {
        "name": "User",
        "dob": "1990-01-01",
        "gender": "U",
        "address": "Mock DigiLocker Address",
        "state": "Karnataka",
        "pincode": "560001",
    }


def lookup_pan(number: str) -> dict | None:
    number = number.strip().upper()
    record = PAN_RECORDS.get(number)
    if record:
        return record
    if not number:
        return None
    return {
        "name": "User",
        "dob": "1990-01-01",
        "pan_type": "Individual",
        "status": "ACTIVE",
    }


def lookup_mobile(number: str) -> dict | None:
    number = number.strip()
    record = MOBILE_RECORDS.get(number)
    if record:
        return record
    if not number:
        return None
    return {
        "name": "User",
        "operator": "MockTel",
        "circle": "Karnataka",
        "type": "Prepaid",
        "kyc_done": True,
    }


def name_match(submitted_name: str, record_name: str) -> bool:
    """
    Case-insensitive, partial token match.
    At least one meaningful token must match.
    E.g. 'rahul sharma' matches 'Rahul Sharma'.
    """
    submitted_tokens = set(submitted_name.lower().split())
    record_tokens = set(record_name.lower().split())
    # Remove common filler words
    fillers = {"mr", "mrs", "ms", "dr", "shri", "smt"}
    submitted_tokens -= fillers
    record_tokens -= fillers
    return bool(submitted_tokens & record_tokens)
