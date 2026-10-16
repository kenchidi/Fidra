"""
Mock responding-bank connector for Path B (bank-confirmed).

In production this would be a real integration to the referee's bank.
For now it simulates:
  1. Account existence check
  2. Structured bank reference response after consent
"""
from typing import Optional
from app.models.models import Institution


def verify_account(
    institution: Institution,
    account_number: str,
    account_name: Optional[str] = None,
) -> dict:
    """Simulate bank account lookup at the responding institution."""
    # Demo rule: accounts ending in 0000 are "not found"
    if account_number.endswith("0000"):
        return {
            "account_exists": False,
            "account_active": False,
            "name_match": None,
            "account_age_band": None,
            "institution_code": institution.code,
            "institution_name": institution.name,
        }

    return {
        "account_exists": True,
        "account_active": True,
        "name_match": "full" if account_name else "not_checked",
        "account_age_band": "3-5_years",
        "institution_code": institution.code,
        "institution_name": institution.name,
    }


def build_bank_reference_response(
    institution: Institution,
    account_check: dict,
    good_standing: bool = True,
    relationship_length_band: str = "3-5_years",
    comments: Optional[str] = None,
) -> dict:
    """Structured response as if returned by the responding bank."""
    return {
        "account_exists": account_check.get("account_exists", True),
        "account_active": account_check.get("account_active", True),
        "account_name_match": account_check.get("name_match") == "full",
        "good_standing": good_standing,
        "relationship_length_band": relationship_length_band,
        "additional_comments": comments,
        "responded_by": institution.code,
        "responded_by_name": institution.name,
        "assurance_level": "bank_confirmed",
        "confirmation_type": "bank_confirmed",
    }
