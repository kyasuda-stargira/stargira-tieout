"""
Load a vertical schema file. A schema may extend another one:

    extends: listed_insurer.yaml     # path relative to this file
    meta: {...}                      # keys replace the base meta keys
    assumptions: [...]               # an entry whose id is in the base replaces the keys it
                                     # gives; a new id is inserted after `after:` (or appended)
    history:
      years: 2                       # replaces the base count
      fields: [...]                  # same rule as assumptions

Everything else comes from the base unchanged. build_model.py, verify_model.py,
export_json.py and history_to_answers.py all read schemas through load_schema(),
so a schema and its extension can never be read two ways.
"""

from __future__ import annotations

import copy
import os

import yaml


def _merge_items(base, extra, what):
    out = copy.deepcopy(base)
    for item in extra or []:
        item = dict(item)
        after = item.pop("after", None)
        at = next((i for i, b in enumerate(out) if b["id"] == item["id"]), None)
        if at is not None:
            out[at].update(item)
            continue
        if after is None:
            out.append(item)
            continue
        pos = next((i for i, b in enumerate(out) if b["id"] == after), None)
        if pos is None:
            raise ValueError("%s %s: 'after: %s' names no %s in the base schema"
                             % (what, item["id"], after, what))
        out.insert(pos + 1, item)
    return out


def load_schema(path):
    path = os.fspath(path)
    with open(path, encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    base_name = schema.pop("extends", None)
    if not base_name:
        return schema
    base = load_schema(os.path.join(os.path.dirname(os.path.abspath(path)), base_name))
    base["meta"].update(schema.get("meta") or {})
    base["assumptions"] = _merge_items(base["assumptions"], schema.get("assumptions"),
                                       "assumption")
    hist = schema.get("history") or {}
    if hist:
        if "years" in hist:
            base["history"]["years"] = hist["years"]
        base["history"]["fields"] = _merge_items(base["history"]["fields"], hist.get("fields"),
                                                 "history field")
    for key, value in schema.items():
        if key not in ("meta", "assumptions", "history"):
            base[key] = value
    return base
