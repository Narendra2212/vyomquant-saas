"""LEGAL-01: enforce the Terms' restricted-jurisdiction clause.

WHY THIS EXISTS
===============
Terms of Service section 3 says:

    "You must not use the Platform if you are located in, ordinarily resident in,
    or acting on behalf of a person in a country or region subject to
    comprehensive sanctions, or if you are a person named on an applicable
    sanctions list. We may refuse or close an account on that basis."

Signups are open globally (the product owner's answer), and until now NOTHING IN
CODE CHECKED THAT. The clause was a sentence in a document with no mechanism
behind it, which is the same defect class LEGAL-01 is about: a policy that
promises something the product does not do.

WHAT THIS IS, STATED PRECISELY
==============================
A DECLARED-COUNTRY GATE. The user states where they are; this refuses the
declared value if it is on the restricted list, and the declaration is recorded
alongside their legal acceptance.

IT IS NOT SANCTIONS SCREENING, AND MUST NOT BE DESCRIBED AS SUCH.
It does not:
  * verify the declaration against anything (no IP geolocation, no KYC, no
    document check -- the schema has no identity fields at all);
  * screen the person against SDN, UN, EU or UK consolidated lists, which needs
    a licensed data provider and a name-matching process;
  * detect a VPN, or a user who simply picks a different country.

So it raises the cost of lying and creates a recorded, dated representation by
the user. That is a real improvement over nothing, and it is categorically
weaker than screening. The Terms are worded as a representation by the user for
exactly this reason, and REVIEW_NOTES records the gap rather than implying it is
closed.

WHY THE LIST IS DELIBERATELY SHORT
==================================
Only jurisdictions under COMPREHENSIVE sanctions programmes, which is what the
Terms' own wording covers. Extending it to partially-sanctioned jurisdictions,
or to countries that restrict crypto generally, is a legal and commercial
decision rather than an engineering one -- a wrong entry here refuses a lawful
customer, and a missing one is the gap this was written to narrow. So the list is
small, sourced, and flagged for counsel to set definitively.
"""
from __future__ import annotations

import logging
from typing import Dict, Final, FrozenSet, Optional

logger = logging.getLogger("RestrictedJurisdictions")

#: ISO 3166-1 alpha-2 codes for jurisdictions under comprehensive sanctions
#: programmes, which is the scope the Terms' clause actually describes.
#:
#: COUNSEL MUST CONFIRM THIS LIST. It is drawn from the jurisdictions commonly
#: subject to comprehensive (rather than targeted or sectoral) programmes. It is
#: NOT a sanctions feed, it does not update itself, and it does not cover
#: sub-national regions -- a country-level code cannot express "the Crimea,
#: Donetsk and Luhansk regions of Ukraine", which several programmes treat as
#: comprehensively restricted while the rest of Ukraine is not.
RESTRICTED_COUNTRIES: Final[Dict[str, str]] = {
    "CU": "Cuba",
    "IR": "Iran",
    "KP": "North Korea",
    "SY": "Syria",
}

#: Jurisdictions NOT blocked, recorded so the omission is visible as a decision
#: rather than looking like an oversight. Each is subject to extensive but
#: targeted or sectoral measures rather than a comprehensive embargo, so blocking
#: every resident would refuse lawful customers. Counsel's call, not ours.
DELIBERATELY_NOT_BLOCKED: Final[Dict[str, str]] = {
    "RU": "Russia - extensive sectoral and targeted measures, not a comprehensive embargo",
    "BY": "Belarus - as above",
    "MM": "Myanmar - targeted measures; also an FATF high-risk jurisdiction",
    "VE": "Venezuela - targeted measures against specified persons and entities",
    "CN": "China - restricts crypto domestically; that is the user's legal problem "
          "under Terms section 3, not a sanctions matter",
    "UA": "Ukraine - comprehensive measures apply to SPECIFIC REGIONS only, which a "
          "country code cannot express. A material gap in this control.",
}

#: The full ISO 3166-1 alpha-2 set is not enumerated here; a two-letter uppercase
#: code is accepted structurally and checked against the restricted list. The
#: alternative -- shipping a 249-entry table -- would need maintaining for no
#: benefit, since an invented-but-well-formed code lands in the "not restricted"
#: branch either way and the declaration is unverified regardless.
_CODE_LENGTH: Final[int] = 2


class JurisdictionRefused(Exception):
    """Signup refused because the declared jurisdiction is restricted."""

    def __init__(self, code: str, country: str) -> None:
        self.code = "JURISDICTION_RESTRICTED"
        self.country_code = code
        self.country = country
        super().__init__(
            f"{country} ({code}) is subject to comprehensive sanctions, so an "
            f"account cannot be opened from there under our Terms of Service."
        )


class JurisdictionMalformed(Exception):
    """The declared country is not a well-formed ISO 3166-1 alpha-2 code."""

    def __init__(self, value: Optional[str]) -> None:
        self.code = "JURISDICTION_MALFORMED"
        super().__init__(
            f"a declared country must be a two-letter ISO 3166-1 alpha-2 code; "
            f"got {value!r}"
        )


def normalise(code: Optional[str]) -> str:
    """Uppercase and validate the shape of a declared country code.

    Raises:
        JurisdictionMalformed: not two ASCII letters.
    """
    candidate = (code or "").strip().upper()
    if len(candidate) != _CODE_LENGTH or not candidate.isalpha() or not candidate.isascii():
        raise JurisdictionMalformed(code)
    return candidate


def is_restricted(code: Optional[str]) -> bool:
    """Whether a declared country is on the restricted list."""
    try:
        return normalise(code) in RESTRICTED_COUNTRIES
    except JurisdictionMalformed:
        # A malformed code is not "restricted"; it is invalid. The caller
        # distinguishes the two, because conflating them would report a typo as a
        # sanctions hit.
        return False


def assert_permitted(code: Optional[str]) -> str:
    """Return the normalised code, or refuse.

    Raises:
        JurisdictionMalformed: the code is not two letters.
        JurisdictionRefused: the code is on the restricted list.
    """
    normalised = normalise(code)
    if normalised in RESTRICTED_COUNTRIES:
        # Logged at WARNING rather than INFO: a refusal is the kind of event an
        # operator should be able to find later, and the country is not personal
        # data on its own.
        logger.warning(
            f"signup refused: declared jurisdiction {normalised} is restricted"
        )
        raise JurisdictionRefused(normalised, RESTRICTED_COUNTRIES[normalised])
    return normalised


def restricted_list() -> FrozenSet[str]:
    return frozenset(RESTRICTED_COUNTRIES)


__all__ = [
    "RESTRICTED_COUNTRIES",
    "DELIBERATELY_NOT_BLOCKED",
    "JurisdictionRefused",
    "JurisdictionMalformed",
    "normalise",
    "is_restricted",
    "assert_permitted",
    "restricted_list",
]
