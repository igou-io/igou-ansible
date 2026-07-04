"""Canonicalization filters for comparing RustFS state.

The server does not return stable orderings: IAM policy Action/Resource
arrays come back in a different order on every call (stored as sets), and
ILM rule ids are server-generated. Both sides of every comparison go
through these filters so ordering and volatile fields can never produce
false drift.
"""

import copy
import json


def rustfs_canonical_policy(doc):
    """Canonicalize an IAM policy document for comparison.

    Sorts Action/Resource lists inside each statement, drops empty
    Condition/ID fields the server adds, and orders statements
    deterministically.
    """
    d = copy.deepcopy(doc)
    statements = d.get("Statement", [])
    for s in statements:
        for k in ("Action", "Resource"):
            if isinstance(s.get(k), list):
                s[k] = sorted(s[k])
        if s.get("Condition") == {}:
            del s["Condition"]
    d["Statement"] = sorted(statements, key=lambda s: json.dumps(s, sort_keys=True))
    if d.get("ID") == "":
        del d["ID"]
    return d


def rustfs_canonical_ilm(rules):
    """Canonicalize an ILM rule list for comparison.

    Strips server-generated rule ids and orders rules deterministically.
    """
    rs = copy.deepcopy(rules)
    for r in rs:
        r.pop("id", None)
    return sorted(rs, key=lambda r: json.dumps(r, sort_keys=True))


class FilterModule(object):
    def filters(self):
        return {
            "rustfs_canonical_policy": rustfs_canonical_policy,
            "rustfs_canonical_ilm": rustfs_canonical_ilm,
        }
