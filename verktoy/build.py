#!/usr/bin/env python3
"""Bygger data.json for "Arbeidsplan VHU" fra Fremdriftsplan-arket.

Input: tekstutskriften av arket som Microsoft 365-koblingen (read_resource,
startPage=1, endPage=1) returnerer, lagret som fil (JSON-liste med {"text": ...}
eller ren tekst).

Bruk:  python3 -I build.py <input> <output.json> [--today YYYY-MM-DD]
                           [--modified ISO-tidspunkt for Excel-filen]
                           [--code KODE]   krypterer (AES-GCM, PBKDF2) til plan.json for nettsiden
"""
import json, re, sys, hashlib, datetime as dt

MONTHS = {m: i + 1 for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split())}
MONTHS.update({"mai": 5, "okt": 10, "des": 12, "januar": 1, "februar": 2, "mars": 3,
               "april": 4, "juni": 6, "juli": 7, "august": 8, "september": 9,
               "oktober": 10, "november": 11, "desember": 12, "sept": 9})
FRAVAER = re.compile(r"\b(kurs|syk|fri|ferie|permisjon|avspasering)\b", re.I)
NB_DAYS = ["Man", "Tir", "Ons", "Tor", "Fre"]


def load_text(path):
    raw = open(path, encoding="utf-8").read()
    try:
        d = json.loads(raw)
        if isinstance(d, list):
            return "\n".join(x.get("text", "") for x in d if isinstance(x, dict))
        if isinstance(d, dict):
            return d.get("text", raw)
    except ValueError:
        pass
    return raw


def grid_from_text(text):
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("## Sheet: Fremdriftsplan")) + 1
    rows = []
    for l in lines[start:]:
        if l.startswith("## Sheet") or l.startswith("Formulas"):
            break
        rows.append(l.split("\t"))
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    return rows


def cell(rows, r, c):
    return rows[r][c].strip() if r < len(rows) and c < len(rows[r]) else ""


def parse_daymonth(s):
    s = s.strip().lower()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return int(m.group(3)), int(m.group(2))
    m = re.match(r"^(\d{1,2})[-.\s]+\s*([a-zæøå]+)\.?$", s)
    if m and m.group(2) in MONTHS:
        return int(m.group(1)), MONTHS[m.group(2)]
    m = re.match(r"^([a-z]+)[-\s](\d{1,2})$", s)  # "Nov-9"
    if m and m.group(1) in MONTHS:
        return int(m.group(2)), MONTHS[m.group(1)]
    return None


def find_week_col(rows, monday, after=-1):
    """Kolonne i rad 2 med datoen for denne mandagen. Arket vokser mot høyre,
    så for første uke tas den siste treffet; senere uker søkes etter forrige."""
    hits = [c for c in range(len(rows[1]))
            if c > after and parse_daymonth(cell(rows, 1, c)) == (monday.day, monday.month)]
    if not hits:
        return None
    return hits[0] if after >= 0 else hits[-1]


def clean(text):
    text = re.sub(r"\s+", " ", text).strip()
    tentative = "?" in text
    if tentative:
        text = re.sub(r"\s*\?+\s*", " / ", text).strip(" /")
    return text, tentative


def color_key(text):
    key = re.sub(r"[^a-zæøå0-9]", "", text.lower())
    return int(hashlib.md5(key.encode()).hexdigest()[:6], 16) % 14


def build(rows, today, modified=None):
    start = today - dt.timedelta(days=today.weekday())
    if today.weekday() >= 5:  # lørdag/søndag: vis fra neste uke
        start += dt.timedelta(days=7)
    weeks, col = [], -1
    for k in range(3):
        monday = start + dt.timedelta(days=7 * k)
        c = find_week_col(rows, monday, col if k else -1)
        weeks.append({"week": monday.isocalendar()[1], "monday": monday.isoformat(), "col": c})
        if c is not None:
            col = c

    lag = []
    for r in range(2, len(rows)):
        a, b = cell(rows, r, 0), cell(rows, r, 1)
        if a.lower().startswith("reservebil"):
            break
        if not b:
            continue
        days = []
        for w in weeks:
            current = None
            for d in range(5):
                date = (dt.date.fromisoformat(w["monday"]) + dt.timedelta(days=d)).isoformat()
                v = cell(rows, r, w["col"] + d) if w["col"] is not None else ""
                start_here = bool(v)
                if v:
                    t, tent = clean(v)
                    current = {"p": t, "c": color_key(t)}
                    if tent:
                        current["f"] = 1
                    if FRAVAER.search(t):
                        current["a"] = 1
                entry = dict(current) if current else {}
                if current and not start_here:
                    entry["k"] = 1  # videreført fra dagen før
                entry["d"] = date
                days.append(entry)
        lag.append({"navn": re.sub(r"\s+", " ", b), "bil": "" if a in ("", "?") else re.sub(r"\s+", " ", a), "dager": days})

    return {
        "generert": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "kilde_endret": modified,
        "uker": [{"uke": w["week"], "mandag": w["monday"], "funnet": w["col"] is not None} for w in weeks],
        "lag": lag,
    }


if __name__ == "__main__":
    args = sys.argv[1:]
    today, modified = dt.date.today(), None
    if "--today" in args:
        i = args.index("--today"); today = dt.date.fromisoformat(args[i + 1]); del args[i:i + 2]
    if "--modified" in args:
        i = args.index("--modified"); modified = args[i + 1]; del args[i:i + 2]
    code = None
    if "--code" in args:
        i = args.index("--code"); code = args[i + 1]; del args[i:i + 2]
    data = build(grid_from_text(load_text(args[0])), today, modified)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if code:
        import os, base64
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        salt, iv, it = os.urandom(16), os.urandom(12), 200000
        key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=it).derive(code.strip().lower().encode())
        ct = AESGCM(key).encrypt(iv, payload.encode(), None)
        b = lambda x: base64.b64encode(x).decode()
        payload = json.dumps({"v": 1, "iter": it, "salt": b(salt), "iv": b(iv), "data": b(ct)})
    open(args[1], "w", encoding="utf-8").write(payload)
    missing = [u["uke"] for u in data["uker"] if not u["funnet"]]
    print(f"OK: {len(data['lag'])} lag, uker {[u['uke'] for u in data['uker']]}"
          + (f" — FANT IKKE uke {missing} i arket" if missing else ""))
