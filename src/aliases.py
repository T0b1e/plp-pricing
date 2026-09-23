"""Step 3: merge spellings of the same place.

Auto-applies only matches that are identical after normalisation (spacing, case,
punctuation, company boilerplate). Fuzzy matches go to alias_suggestions.csv for a
human to accept by copying the `canonical` value into `alias_of` in
locations_master.csv. Leading codes alone are NOT trusted: "TUF", "TUF โรงหมึก" and
"TUF 5 เกตุม" are different sites.
"""
import re

import pandas as pd
from rapidfuzz import fuzz, process

from .files import read_csv, require_columns, write_csv
from .paths import DATA, LOCATIONS

SUGGESTIONS = DATA / "alias_suggestions.csv"
OVERRIDES = DATA / "alias_overrides.csv"   # hand-accepted: token, alias_of
BOILERPLATE = ["บริษัท", "บจก.", "บจ.", "จำกัด", "(มหาชน)", "มหาชน", "co.,ltd.", "co., ltd.", "co.ltd", "ltd.", "ltd", "จ."]
FUZZY_MIN = 90


def key(token: str) -> str:
    s = token.lower()
    for b in BOILERPLATE:
        s = s.replace(b, " ")
    s = s.replace("km.", "กม.").replace("km", "กม.")
    s = re.sub(r"[\s\-_.,/()'\"]+", "", s)
    return s


def resolve(token: str, alias: dict[str, str]) -> str:
    seen = set()
    while token in alias and alias[token] and token not in seen:
        seen.add(token)
        token = alias[token]
    return token


def canonical_map() -> dict[str, str]:
    """token -> canonical token, for every name in locations_master.csv."""
    loc = read_csv(LOCATIONS, dtype=str).fillna("")
    require_columns(loc, ["token", "alias_of"], LOCATIONS.name)
    return {t: (a or t) for t, a in zip(loc["token"], loc["alias_of"])}


def main():
    loc = read_csv(LOCATIONS, dtype=str).fillna("")
    require_columns(loc, ["token", "alias_of", "frequency"], LOCATIONS.name)
    loc["frequency"] = pd.to_numeric(loc["frequency"], errors="coerce").fillna(0).astype(int)
    loc = loc.sort_values("frequency", ascending=False).reset_index(drop=True)
    loc["_key"] = loc["token"].map(key)

    # exact-after-normalisation: the most frequent spelling wins
    canonical_by_key = loc.drop_duplicates("_key").set_index("_key")["token"]
    auto = 0
    for i, row in loc.iterrows():
        canon = canonical_by_key[row["_key"]]
        if canon != row["token"] and not row["alias_of"]:
            loc.at[i, "alias_of"] = canon
            auto += 1

    if OVERRIDES.exists():
        ov = read_csv(OVERRIDES, dtype=str).fillna("")
        require_columns(ov, ["token", "alias_of"], OVERRIDES.name)
        ov_map = dict(zip(ov["token"], ov["alias_of"]))
        loc["alias_of"] = [ov_map.get(t, a) for t, a in zip(loc["token"], loc["alias_of"])]

    # resolve chains a -> b -> c so every alias points straight at a canonical name
    alias = {t: a for t, a in zip(loc["token"], loc["alias_of"]) if a}
    loc["alias_of"] = [resolve(t, alias) if a else "" for t, a in zip(loc["token"], loc["alias_of"])]

    # fuzzy suggestions among the remaining canonical names
    canon = loc[loc["alias_of"] == ""]
    keys, tokens, freqs = canon["_key"].tolist(), canon["token"].tolist(), canon["frequency"].tolist()
    rows = []
    for i, k in enumerate(keys):
        if len(k) < 4:
            continue
        for _, score, j in process.extract(k, keys, scorer=fuzz.ratio, limit=4):
            if j <= i or score < FUZZY_MIN:
                continue
            a, b = (i, j) if freqs[i] >= freqs[j] else (j, i)
            rows.append({"token": tokens[b], "canonical": tokens[a], "score": round(score, 1),
                         "freq_token": freqs[b], "freq_canonical": freqs[a]})
    sugg = pd.DataFrame(rows).sort_values("score", ascending=False) if rows else pd.DataFrame()

    write_csv(loc.drop(columns="_key"), LOCATIONS, index=False)
    write_csv(sugg, SUGGESTIONS, index=False)
    n_canon = (loc["alias_of"] == "").sum()
    print(f"[aliases] {auto} auto-merged, {n_canon} canonical places, "
          f"{len(sugg)} fuzzy suggestions -> {SUGGESTIONS.name}")


if __name__ == "__main__":
    main()
