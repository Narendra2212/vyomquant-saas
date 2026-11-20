"""REF-LEN-01 -- validate, claim and the signup form accept the same referral-code shape.

Before: the form accepted 3-32 characters, `/referral/validate` 5-20, `/referral/claim`
1-32. A 3-4 character code passed the form and got a bare 422 from validate; claim
accepted codes validate refused. Real codes are 8-10 letters/digits.
"""
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend_app.routers import referral

GOOD = ["ABCDE", "CLAUDEBDA7", "a1b2c3d4", "Z" * 20]
BAD = ["", "A", "ABCD", "Z" * 21, "AB-CD-EF", "ABC DEF", "ABCDE;"]


@pytest.mark.parametrize("model", [referral.ValidateReferralCodeRequest, referral.ClaimReferralRequest])
@pytest.mark.parametrize("code", GOOD)
def test_real_codes_are_accepted_by_both(model, code):
    assert model(referral_code=code).referral_code == code


@pytest.mark.parametrize("model", [referral.ValidateReferralCodeRequest, referral.ClaimReferralRequest])
@pytest.mark.parametrize("code", BAD)
def test_malformed_codes_are_refused_by_both(model, code):
    with pytest.raises(ValidationError):
        model(referral_code=code)


def test_the_signup_form_uses_the_same_rule():
    js = Path("algo22-terminal/src/lib/pendingReferral.js").read_text()
    m = re.search(r"const CODE = /(.+?)/;", js)
    assert m, "pendingReferral.js no longer declares CODE"
    assert m.group(1) == referral.REFERRAL_CODE_PATTERN
