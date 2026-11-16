#!/usr/bin/env python3
"""
Drakelicit online — monthly data update (run by .github/workflows/update-data.yml)
================================================================================

  Google Sheet (CSV)  ->  validate / dedup  ->  data/part-NNNN.jsonl (+ .csv)
                                            ->  data/community.json
                                            ->  data/manifest.json

* Source: the Sheet linked to the Google Form, published to the web as CSV.
  Its address comes from the SHEET_CSV_URL environment variable (a repository
  secret), or from --csv <file> for local tests.
* Each row of the Sheet holds ONE cell with the JSON payload sent by the browser.
  The script finds that cell by content, so column order/names do not matter.
  The Sheet's own timestamp is ignored: the public date is the date the browser
  sent (date only, checked for plausibility).
* Only rows that pass validation are published. Duplicates (same id) are dropped.
* Parts are append-only: new rows fill the last part; once it holds PART_SIZE rows
  the next part starts and the full one is never touched again.
* The community distribution is recomputed from ALL parts with scripts/compute.py
  (same maths as the website), with a fixed seed, so reruns give the same curve.

Local test:
    python scripts/make_fake_rows.py --n 2500 --out /tmp/fake.csv
    python scripts/update_data.py --csv /tmp/fake.csv --data-dir /tmp/data-test

Dependencies: numpy scipy   (pip install -r scripts/requirements.txt)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import urllib.request
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import compute as C  # noqa: E402  (the website's maths, reused as-is)

PART_SIZE = 1000          # rows per part file
N_MC = 100_000            # Monte Carlo samples for the community N
SEED = 1
SCHEMA = 1
LICENSE = "CC-BY-4.0"
MIN_DATE = date(2026, 1, 1)

SUPPORTED_LANGS = {"pt", "en", "es"}
PART_RE = re.compile(r"^part-(\d{4})\.jsonl$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

# canonical (Portuguese) values, as used by context.html. areas.json is the single source:
# broad field -> list of specific areas (CNPq "áreas do conhecimento")
AREAS_MAP = json.loads((ROOT / "areas.json").read_text(encoding="utf-8"))
AREAS = set(AREAS_MAP)
FAMILIARITY = {"none", "little", "moderate", "good", "expert"}
YES_NO = {"yes", "no"}
BELIEF_KEYS = ["life_solar", "life_galaxy", "intel_galaxy", "life_universe", "intel_universe"]
BELIEF_VALUES = {"very_unlikely", "unlikely", "unsure", "likely", "very_likely"}


# ── validation ─────────────────────────────────────────────────────────────

class Reject(Exception):
    """A row that must not be published; the message is counted in the report."""


def _num(x, what):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise Reject(f"non-numeric {what}")
    return float(x)


def validate(raw: dict, questions: list[dict], today: date) -> dict:
    """Return the public row for one payload, or raise Reject."""
    if raw.get("v") != 1:
        raise Reject("unknown payload version")

    try:
        u = uuid.UUID(str(raw.get("id")))
    except ValueError:
        raise Reject("bad id")
    if u.version != 4:
        raise Reject("id is not a v4 uuid")

    lang = raw.get("lang")
    if lang not in SUPPORTED_LANGS:
        raise Reject("bad lang")

    consent = raw.get("consent")
    if not isinstance(consent, str) or not (0 < len(consent) <= 20):
        raise Reject("bad consent version")

    # date: the browser's UTC date; anything implausible is replaced by the run date
    d = today
    try:
        cand = date.fromisoformat(str(raw.get("date")))
        if MIN_DATE <= cand <= today + timedelta(days=1):
            d = cand
    except ValueError:
        pass

    # answers: every question exactly once, same rules as the old validate_answers()
    qmap = {q["id"]: q for q in questions}
    items = raw.get("answers")
    if not isinstance(items, list) or not all(isinstance(a, dict) for a in items):
        raise Reject("answers is not a list")
    ids = [a.get("question_id") for a in items]
    if len(ids) != len(set(ids)) or set(ids) != set(qmap):
        raise Reject("answers do not match the questions")
    by_id = {a["question_id"]: a for a in items}
    eps = 1e-6
    answers = []
    for q in questions:                      # published in questions.json order
        a = by_id[q["id"]]
        if a.get("axis_mode") != q.get("axisMode", "log"):
            raise Reject("wrong axis_mode")
        if not isinstance(a.get("skipped"), bool):
            raise Reject("bad skipped flag")
        if a["skipped"]:
            lo = mo = hi = None              # slider defaults of a skipped question carry no information
        else:
            lo, mo, hi = (_num(a.get(k), k) for k in ("t_lo", "t_mode", "t_hi"))
            if not (q["tMin"] - eps <= lo <= mo <= hi <= q["tMax"] + eps):
                raise Reject("answer out of range")
            lo, mo, hi = round(lo, 6), round(mo, 6), round(hi, 6)
        answers.append({"question_id": q["id"], "axis_mode": q.get("axisMode", "log"),
                        "t_lo": lo, "t_mode": mo, "t_hi": hi, "skipped": a["skipped"]})

    # context: optional, every field optional; unknown fields are dropped
    c = raw.get("context") if isinstance(raw.get("context"), dict) else {}

    def pick(key, allowed):
        v = c.get(key)
        return v if isinstance(v, str) and v in allowed else None

    country = c.get("country")
    country = country if isinstance(country, str) and COUNTRY_RE.match(country) else None
    beliefs_in = c.get("beliefs") if isinstance(c.get("beliefs"), dict) else {}
    beliefs = {k: (beliefs_in.get(k) if beliefs_in.get(k) in BELIEF_VALUES else None) for k in BELIEF_KEYS}
    rd = c.get("returning_device")
    area_g = pick("area_grande", AREAS)
    esp = c.get("area_especifica")           # only accepted if it belongs to the chosen broad field
    area_e = esp if area_g and isinstance(esp, str) and esp in AREAS_MAP[area_g] else None
    context = {
        "country": country,
        "area_grande": area_g,
        "area_especifica": area_e,
        "familiarity_astro": pick("familiarity_astro", FAMILIARITY),
        "familiarity_bio": pick("familiarity_bio", FAMILIARITY),
        "returning_self": pick("returning_self", YES_NO),
        "returning_device": rd if isinstance(rd, bool) else None,
        "beliefs": beliefs,
    }
    return {"id": str(u), "date": d.isoformat(), "lang": lang, "consent": consent,
            "answers": answers, "context": context}


# ── reading the Sheet ──────────────────────────────────────────────────────

def read_csv_text(csv_path: str | None) -> str | None:
    if csv_path:
        return Path(csv_path).read_text(encoding="utf-8-sig")
    url = os.environ.get("SHEET_CSV_URL", "").strip()
    if not url:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "drakelicit-online-action"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def payloads_from_csv(text: str):
    """Yield one payload dict per row; the cell is found by content."""
    csv.field_size_limit(10**7)
    for row in csv.reader(io.StringIO(text)):
        found = None
        for cell in row:
            cell = cell.strip()
            if not cell.startswith("{"):
                continue
            try:
                obj = json.loads(cell)
            except ValueError:
                continue
            if isinstance(obj, dict) and "id" in obj and "answers" in obj:
                found = obj
                break
        yield found                            # None = not a submission (header, junk)


# ── parts on disk ──────────────────────────────────────────────────────────

def list_parts(data_dir: Path) -> list[Path]:
    return sorted(p for p in data_dir.glob("part-*.jsonl") if PART_RE.match(p.name))


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def csv_columns(questions: list[dict]) -> list[str]:
    cols = ["id", "date", "lang", "consent", "country", "area_grande", "area_especifica",
            "familiarity_astro", "familiarity_bio", "returning_self", "returning_device"]
    cols += [f"belief_{k}" for k in BELIEF_KEYS]
    for q in questions:
        cols += [f"{q['id']}_t_lo", f"{q['id']}_t_mode", f"{q['id']}_t_hi", f"{q['id']}_skipped"]
    return cols


def flat_csv_row(r: dict, questions: list[dict]) -> list:
    c = r["context"]
    out = [r["id"], r["date"], r["lang"], r["consent"], c["country"], c["area_grande"], c["area_especifica"],
           c["familiarity_astro"], c["familiarity_bio"], c["returning_self"], c["returning_device"]]
    out += [c["beliefs"].get(k) for k in BELIEF_KEYS]
    by_id = {a["question_id"]: a for a in r["answers"]}
    for q in questions:
        a = by_id[q["id"]]
        out += [a["t_lo"], a["t_mode"], a["t_hi"], a["skipped"]]
    return ["" if v is None else v for v in out]


def write_part_csv(part: Path, questions: list[dict]) -> None:
    rows = read_rows(part)
    with part.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(csv_columns(questions))
        for r in rows:
            w.writerow(flat_csv_row(r, questions))


def append_rows(data_dir: Path, new_rows: list[dict], part_size: int) -> set[Path]:
    """Append to the last part, opening new parts as needed. Returns the parts touched."""
    parts = list_parts(data_dir)
    if parts:
        idx = int(PART_RE.match(parts[-1].name).group(1))
        count = sum(1 for line in parts[-1].open(encoding="utf-8") if line.strip())
    else:
        idx, count = 0, part_size             # forces the creation of part-0001
    touched: set[Path] = set()
    f = None
    try:
        for r in new_rows:
            if count >= part_size:
                if f:
                    f.close()
                idx += 1
                count = 0
                path = data_dir / f"part-{idx:04d}.jsonl"
                f = path.open("a", encoding="utf-8", newline="\n")
            elif f is None:
                path = data_dir / f"part-{idx:04d}.jsonl"
                f = path.open("a", encoding="utf-8", newline="\n")
            f.write(dumps(r) + "\n")
            count += 1
            touched.add(path)
    finally:
        if f:
            f.close()
    return touched


# ── community distribution ────────────────────────────────────────────────

def sig5(x: float) -> float:
    return float(f"{x:.5g}")


def build_community(rows: list[dict], questions: list[dict], n_mc: int) -> dict:
    out = {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "n_sessions": len(rows)}
    if not rows:
        out.update({"parameters": {}, "community_N": {"error": "No data yet."}})
        return out
    flat = [{**a, "session_id": r["id"]} for r in rows for a in r["answers"]]
    pdfs = C.compute_community_pdfs(flat, questions)
    qmap = {q["id"]: q for q in questions}
    out["parameters"] = {
        qid: {"t_min": qmap[qid]["tMin"], "t_max": qmap[qid]["tMax"],
              "n_grid": len(d["community_pdf"]), "n_answers": d["n_answers"],
              "pdf": [sig5(v) for v in d["community_pdf"]]}
        for qid, d in pdfs.items()
    }
    cn = C.compute_community_N(pdfs, questions, n_mc=n_mc, seed=SEED)
    if "error" not in cn:
        for k in ("log_N_grid", "pdf"):
            cn[k] = [sig5(v) for v in cn[k]]
    out["community_N"] = cn
    return out


# ── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="read this CSV instead of SHEET_CSV_URL (tests)")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--questions", default=str(ROOT / "questions.json"))
    ap.add_argument("--part-size", type=int, default=PART_SIZE)
    ap.add_argument("--n-mc", type=int, default=N_MC)
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for tests")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    today = date.fromisoformat(args.today) if args.today else datetime.now(timezone.utc).date()

    # 1. what is already published
    seen: set[str] = set()
    for p in list_parts(data_dir):
        seen.update(r["id"] for r in read_rows(p))
    n_before = len(seen)

    # 2. new rows from the Sheet
    text = read_csv_text(args.csv)
    new_rows: list[dict] = []
    stats: Counter = Counter()
    if text is None:
        print("::warning::SHEET_CSV_URL is not set and no --csv given: nothing to import; "
              "only the community distribution is recomputed.")
    else:
        for obj in payloads_from_csv(text):
            if obj is None:
                stats["not a submission (header or junk)"] += 1
                continue
            try:
                row = validate(obj, questions, today)
            except Reject as e:
                stats[f"rejected: {e}"] += 1
                continue
            if row["id"] in seen:
                stats["duplicate"] += 1
                continue
            seen.add(row["id"])
            new_rows.append(row)
            stats["accepted"] += 1

    # 3. append (full parts are never touched again)
    touched = append_rows(data_dir, new_rows, args.part_size) if new_rows else set()
    for p in touched:
        write_part_csv(p, questions)

    # 4. community + manifest from ALL parts
    parts = list_parts(data_dir)
    all_rows = [r for p in parts for r in read_rows(p)]
    community = build_community(all_rows, questions, args.n_mc)
    (data_dir / "community.json").write_text(dumps(community) + "\n", encoding="utf-8")
    manifest = {
        "schema": SCHEMA, "license": LICENSE, "part_size": args.part_size,
        "n_rows": len(all_rows),
        "questions": [q["id"] for q in questions],
        "parts": [{"file": p.name, "csv": p.with_suffix(".csv").name,
                   "n_rows": sum(1 for line in p.open(encoding="utf-8") if line.strip()),
                   "frozen": sum(1 for line in p.open(encoding="utf-8") if line.strip()) >= args.part_size}
                  for p in parts],
    }
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")

    # 5. report
    print(f"published before: {n_before} | after: {len(all_rows)} | parts: {len(parts)}")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    cn = community["community_N"]
    if "error" in cn:
        print(f"  community N: {cn['error']}")
    else:
        print(f"  community N: median {cn['N_median']:.3g}, 90% [{cn['N_q05']:.3g}, {cn['N_q95']:.3g}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
