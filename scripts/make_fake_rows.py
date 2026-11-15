#!/usr/bin/env python3
"""
Generate a fake Google-Sheet CSV to test scripts/update_data.py (no real data involved).

    python scripts/make_fake_rows.py --n 2500 --out /tmp/fake.csv

The output looks like what the Sheet gives: a header, then "Timestamp,<json payload>" rows.
It also contains on purpose: duplicates, out-of-range answers, a bad uuid, a wrong
axis_mode, a missing question and plain junk, so the validation report shows each case.
"""
import argparse, csv, json, random, uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AREAS = ["Ciências Exatas e da Terra", "Ciências Biológicas", "Engenharias", "Outra / Multidisciplinar", None]
COUNTRIES = ["BR", "US", "PT", "ES", "AR", "DE", None]
SCALE = ["very_unlikely", "unlikely", "unsure", "likely", "very_likely", None]
FAM = ["none", "little", "moderate", "good", "expert", None]


def answer(q, rng, skip_p=0.03):
    if rng.random() < skip_p:
        return {"question_id": q["id"], "axis_mode": q["axisMode"], "t_lo": 0, "t_mode": 0, "t_hi": 0, "skipped": True}
    lo_b, hi_b = q["tMin"], q["tMax"]
    mode = rng.uniform(lo_b, hi_b)
    lo = max(lo_b, mode - rng.uniform(0.03, (hi_b - lo_b) / 2))
    hi = min(hi_b, mode + rng.uniform(0.03, (hi_b - lo_b) / 2))
    return {"question_id": q["id"], "axis_mode": q["axisMode"], "t_lo": lo, "t_mode": mode, "t_hi": hi, "skipped": False}


def payload(questions, rng, today):
    return {
        "v": 1, "id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "date": (today - timedelta(days=rng.randint(0, 20))).isoformat(),
        "lang": rng.choice(["pt", "en", "es"]), "consent": "online-1.0",
        "answers": [answer(q, rng) for q in questions],
        "context": {"country": rng.choice(COUNTRIES), "area_grande": rng.choice(AREAS),
                    "familiarity_astro": rng.choice(FAM), "familiarity_bio": rng.choice(FAM),
                    "returning_self": rng.choice(["yes", "no", None]), "returning_device": rng.random() < 0.1,
                    "beliefs": {k: rng.choice(SCALE) for k in
                                ["life_solar", "life_galaxy", "intel_galaxy", "life_universe", "intel_universe"]}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2500)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--today", default=date.today().isoformat())
    a = ap.parse_args()
    rng = random.Random(a.seed)
    today = date.fromisoformat(a.today)
    questions = json.loads((ROOT / "questions.json").read_text(encoding="utf-8"))

    rows = [payload(questions, rng, today) for _ in range(a.n)]
    bad = []
    p = payload(questions, rng, today); p["answers"][0]["t_mode"] = 99; bad.append(p)           # out of range
    p = payload(questions, rng, today); p["id"] = "not-a-uuid"; bad.append(p)                    # bad id
    p = payload(questions, rng, today); p["answers"][1]["axis_mode"] = "lin"; bad.append(p)      # wrong axis mode
    p = payload(questions, rng, today); p["answers"].pop(); bad.append(p)                        # missing question
    p = payload(questions, rng, today); p["answers"][2]["t_lo"] = p["answers"][2]["t_hi"] + 1; bad.append(p)  # lo > hi
    p = payload(questions, rng, today); p["lang"] = "xx"; bad.append(p)                          # bad lang
    p = payload(questions, rng, today); p["date"] = "1999-01-01"; rows.append(p)                 # accepted, date replaced
    dups = [rows[3], rows[10]]                                                                   # duplicates
    allp = rows + bad + dups
    rng.shuffle(allp)

    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp", "payload"])
        for i, p in enumerate(allp):
            w.writerow([f"11/{1 + i % 28:02d}/2026 12:00:00", json.dumps(p, ensure_ascii=False)])
        w.writerow(["11/30/2026 12:00:00", "hello, this is not json"])
        w.writerow(["11/30/2026 12:00:01", '{"some": "other json"}'])
    print(f"wrote {len(allp) + 2} rows to {a.out} ({a.n + 1} should be accepted)")


if __name__ == "__main__":
    main()
