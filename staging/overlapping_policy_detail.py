"""Read the predicates of every overlapping permissive policy pair and judge each one.

WHY THIS IS NOT A DETAIL
------------------------
Permissive RLS policies are OR-ed. When one table/command carries two of them, the
BROADER predicate decides every outcome and the narrower one contributes nothing. That
is exactly how BUG-045 happened: `referral_system_redesign.sql` added
`referral_codes_select_own` with `USING (user_id = auth.uid())` and never dropped 006's
`referral_codes_select` with `USING (user_id = auth.uid() OR is_active = true)`. The
second disjunct made the whole thing unconditional and the "fix" changed nothing.

`scripts/verify_production_schema.py` section 3b reports the COUNT of such pairs. That
is the right thing for a gate to do -- it cannot judge a predicate -- but somebody has
to read them. This prints each pair with both predicates side by side and flags the ones
whose predicate contains a disjunct that is not an ownership test.

Exit 0 -> every overlapping pair is ownership-scoped on both sides.
Exit 1 -> at least one pair has a disjunct that is not an ownership comparison, so the
          pair needs reading by a human before launch.
"""

from __future__ import annotations

import os
import re
import sys

import psycopg2

from dsn import resolve_dsn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.owner_columns import derive_owner_columns  # noqa: E402

#: Pairs read by a human and judged intentional, with the reason. An allowlist with a
#: written justification, NOT a blanket suppression: anything not named here still turns
#: this probe red, so a newly-added broad policy is caught.
ACCEPTED_BY_DESIGN: dict[tuple[str, str], str] = {
    ("marketplace_listings", "SELECT"): (
        "`All users can view published listings` is USING (is_published = true) granted "
        "to `public`, which includes `anon`. That is the marketplace storefront and the "
        "breadth is the feature -- an unauthenticated visitor is meant to browse "
        "published listings. The companion policy `Users can view their own listings` "
        "(user_id = auth.uid()) adds the owner's UNPUBLISHED rows, which is the only "
        "thing it can add. Checked: `marketplace_listings` carries no financial column; "
        "settlement figures live in `marketplace_settlements`, which is owner/purchaser "
        "scoped."
    ),
}

QUERY = """
SELECT p.tablename, p.cmd, p.policyname, array_to_string(p.roles, ','),
       coalesce(p.qual, ''), coalesce(p.with_check, '')
FROM pg_policies p
WHERE p.schemaname = 'public'
  AND p.permissive = 'PERMISSIVE'
  AND ('anon' = ANY(p.roles) OR 'authenticated' = ANY(p.roles) OR 'public' = ANY(p.roles))
  AND EXISTS (SELECT 1 FROM information_schema.columns col
              WHERE col.table_schema = 'public'
                AND col.table_name = p.tablename
                AND col.column_name = ANY(%s))
ORDER BY p.tablename, p.cmd, p.policyname
"""


def _is_ownership_test(predicate: str, owner_columns: tuple[str, ...]) -> bool:
    """Does this predicate constrain rows to the caller, on every branch?

    Conservative by design: it returns True only for predicates it can positively
    recognise as ownership tests. Anything it cannot parse is reported for reading
    rather than waved through -- the failure mode to avoid is a clean-looking report
    over a predicate nobody understood.

    NOTE ON THE PARENTHESES. An earlier version did `d.strip().strip("()")` to peel the
    predicate's wrapping parens, which also ate the ones in `auth.uid()` -- so
    `(referrer_id = auth.uid())` became `referrer_id = auth.uid`, the identity test went
    unrecognised, and three correctly-scoped policies were reported as suspect. The
    disjunct is now matched as-is; wrapping parens are harmless to a substring search and
    stripping them was never necessary.
    """
    p = " ".join(predicate.split())
    if not p:
        return True  # an empty WITH CHECK is not a read path

    # Split on OR. A single non-ownership disjunct makes the whole predicate as broad as
    # that disjunct, which is the BUG-045 shape: `user_id = auth.uid() OR is_active`.
    for d in re.split(r"\s+OR\s+", p, flags=re.IGNORECASE):
        has_identity = "auth.uid()" in d or "current_setting" in d or "auth.jwt()" in d
        has_owner_column = any(c in d for c in owner_columns)
        # An EXISTS subquery correlating on an ownership column is a legitimate
        # ownership test -- "rows belonging to a strategy I own", say.
        correlated_exists = "EXISTS" in d.upper() and has_owner_column
        if not (has_identity and has_owner_column) and not correlated_exists:
            return False
    return True


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(" ".join(text.split()), width=width) or [""]


def main() -> int:
    conn = psycopg2.connect(resolve_dsn())
    conn.set_session(readonly=True, autocommit=True)
    owner_columns, derived_only = derive_owner_columns(conn)
    with conn.cursor() as cur:
        cur.execute(QUERY, (list(owner_columns),))
        rows = cur.fetchall()
    conn.close()

    groups: dict[tuple[str, str], list[tuple]] = {}
    for table, cmd, name, roles, qual, with_check in rows:
        groups.setdefault((table, cmd), []).append((name, roles, qual, with_check))

    overlapping = {k: v for k, v in groups.items() if len(v) > 1}

    print("=" * 78)
    print("OVERLAPPING PERMISSIVE POLICIES -- PREDICATE BY PREDICATE")
    print("=" * 78)
    print("Permissive policies are OR-ed. For each pair below, the BROADER predicate")
    print("decides every outcome; the narrower one contributes nothing.")
    print(f"ownership columns in use: {len(owner_columns)} "
          f"(derived from foreign keys to profiles/auth.users, union a written floor)")
    if derived_only:
        print(f"  derived but NOT in the written floor: {list(derived_only)}")
        print("  -> add these to scripts/owner_columns.py BASELINE_OWNER_COLUMNS")
    print("-" * 78)

    suspect: list[str] = []

    for (table, cmd), policies in sorted(overlapping.items()):
        verdicts = []
        for name, roles, qual, with_check in policies:
            ok_qual = _is_ownership_test(qual, owner_columns)
            ok_check = _is_ownership_test(with_check, owner_columns)
            verdicts.append(ok_qual and ok_check)

        all_scoped = all(verdicts)
        accepted = (table, cmd) in ACCEPTED_BY_DESIGN
        mark = "OK  " if all_scoped else ("NOTED" if accepted else "READ")
        print(f"  [{mark}] {table} [{cmd}] x{len(policies)}")
        for (name, roles, qual, with_check), scoped in zip(policies, verdicts):
            flag = " " if scoped else "*"
            print(f"       {flag} {name}  to {roles}")
            if qual:
                print(f"           USING      {' '.join(qual.split())[:110]}")
            if with_check:
                print(f"           WITH CHECK {' '.join(with_check.split())[:110]}")
        if not all_scoped:
            if accepted:
                for line in _wrap(ACCEPTED_BY_DESIGN[(table, cmd)], 68):
                    print(f"           | {line}")
            else:
                suspect.append(f"{table}[{cmd}]")
        print()

    print("-" * 78)
    noted = sum(1 for k in overlapping if k in ACCEPTED_BY_DESIGN)
    print(f"SUMMARY: {len(overlapping)} overlapping pair(s), "
          f"{noted} accepted by design with a written reason, "
          f"{len(suspect)} needing a human to read the predicate")
    if suspect:
        print()
        print("  These carry at least one disjunct (marked *) that is not recognisably")
        print("  an ownership comparison. That does not prove a leak -- it means the")
        print("  predicate decides the table's confidentiality and nobody has read it.")
        for s in suspect:
            print(f"    {s}")
        return 1
    print()
    print("  Every overlapping pair is ownership-scoped on BOTH sides, so OR-ing them")
    print("  cannot widen access beyond the caller's own rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
