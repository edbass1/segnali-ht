#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scanner «favorita HT 0-0» (FotMob + quote) → Telegram.

Una sola notifica per partita, senza messaggi di follow-up.
Solo libreria standard (Python 3.12).

Uso:
  python scanner/scan.py                 # scansione (workflow)
  python scanner/scan.py --dry-run       # partite live, niente Telegram, niente salvataggio, niente Odds API
  python scanner/scan.py --test          # una partita conclusa recente, senza Telegram
  python scanner/scan.py --test 5749714  # un matchId FotMob
  python scanner/scan.py --test 5749714 --send   # invia un messaggio di prova
  python scanner/scan.py --test 5749714 --odds   # legge anche la quota live (1 credito)
"""
import argparse, datetime, difflib, html, json, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

# ============================ SOGLIE ============================
# Tutte le soglie della strategia stanno qui.
CFG = {
    "SOT_MIN": 3,             # tiri in porta minimi della favorita (1° tempo)
    "SOT_MARGIN": 2,          # favorita >= avversaria + questo margine
    "BOX_RATIO_MIN": 1.6,     # tocchi in area avversaria, rapporto favorita/avversaria
    "POSS_MIN": 58,           # possesso % minimo della favorita
    "RED_MAX": 0,             # cartellini rossi massimi della favorita
    "PRE_MIN": 1.25,          # quota prematch favorita (inclusiva)
    "PRE_MAX": 1.60,
    "LIVE_MIN": 1.55,         # quota live back favorita (inclusiva)
    "LIVE_MAX": 1.85,
    "WINDOW_2H_MAX": 50,      # ultimo minuto di 2° tempo ancora valido
    "ODDS_MIN_REMAINING": 60, # sotto questa soglia non si chiama più The Odds API
    # "alert" = invia comunque con «quota live n.d.»; "skip" = non inviare
    "LOW_QUOTA_ACTION": "alert",
    "STAKE_PCT": 1,
    "GREENUP_ODDS": 1.25,
    "EXIT_MINUTE_FROM": 70,
    "EXIT_MINUTE_TO": 75,
    "PREMATCH_LOOKAHEAD_H": 6,
    "PREMATCH_FETCH_CAP": 12, # snapshot FotMob prematch per giro
    "DELAY": 0.3,
    "HEARTBEAT_MIN": 30,
    "ODDS_TIME_TOL_H": 3,
}
# Bookmaker live: una sola chiamata event-odds (regions=eu, markets=h2h).
# Si preferisce Betfair Exchange; se manca, il primo di questa lista presente nella stessa risposta.
ODDS_BOOK_PREFERRED = "betfair_ex_eu"
ODDS_BOOK_FALLBACK = (
    "pinnacle", "unibet_eu", "williamhill", "betclic", "marathonbet", "betsson",
    "coolbet", "leovegas", "betfair_sb_uk", "onexbet", "betvictor", "unibet",
    "betway", "skybet", "paddypower",
)
# FotMob league id → sport key The Odds API. Solo campionati nazionali senior maschili.
ODDS_SPORT = {
    112: "soccer_argentina_primera_division", 113: "soccer_australia_aleague", 38: "soccer_austria_bundesliga",
    40: "soccer_belgium_first_div", 268: "soccer_brazil_campeonato", 8814: "soccer_brazil_serie_b",
    273: "soccer_chile_campeonato", 46: "soccer_denmark_superliga", 47: "soccer_epl", 48: "soccer_efl_champ",
    108: "soccer_england_league1", 109: "soccer_england_league2", 51: "soccer_finland_veikkausliiga",
    53: "soccer_france_ligue_one", 110: "soccer_france_ligue_two", 54: "soccer_germany_bundesliga",
    146: "soccer_germany_bundesliga2", 208: "soccer_germany_liga3", 135: "soccer_greece_super_league",
    55: "soccer_italy_serie_a", 86: "soccer_italy_serie_b", 223: "soccer_japan_j_league", 9080: "soccer_korea_kleague1",
    126: "soccer_league_of_ireland", 230: "soccer_mexico_ligamx", 57: "soccer_netherlands_eredivisie",
    59: "soccer_norway_eliteserien", 196: "soccer_poland_ekstraklasa", 61: "soccer_portugal_primeira_liga",
    63: "soccer_russia_premier_league", 536: "soccer_saudi_arabia_pro_league", 87: "soccer_spain_la_liga",
    140: "soccer_spain_segunda_division", 64: "soccer_spl", 67: "soccer_sweden_allsvenskan",
    168: "soccer_sweden_superettan", 69: "soccer_switzerland_superleague", 71: "soccer_turkey_super_league",
    130: "soccer_usa_mls",
}
# Stesse leghe, pagina BetExplorer (quote prematch ancora disponibili a partita in corso).
BE_PATH = {
    112: "/football/argentina/liga-profesional/", 113: "/football/australia/a-league/",
    38: "/football/austria/bundesliga/", 40: "/football/belgium/jupiler-pro-league/",
    268: "/football/brazil/serie-a/", 8814: "/football/brazil/serie-b/",
    273: "/football/chile/liga-de-primera/", 46: "/football/denmark/superliga/",
    47: "/football/england/premier-league/", 48: "/football/england/championship/",
    108: "/football/england/league-one/", 109: "/football/england/league-two/",
    51: "/football/finland/veikkausliiga/", 53: "/football/france/ligue-1/",
    110: "/football/france/ligue-2/", 54: "/football/germany/bundesliga/",
    146: "/football/germany/2-bundesliga/", 208: "/football/germany/3-liga/",
    135: "/football/greece/super-league/", 55: "/football/italy/serie-a/",
    86: "/football/italy/serie-b/", 223: "/football/japan/j1-league/",
    9080: "/football/south-korea/k-league-1/", 126: "/football/ireland/premier-division/",
    230: "/football/mexico/liga-mx/", 57: "/football/netherlands/eredivisie/",
    59: "/football/norway/eliteserien/", 196: "/football/poland/ekstraklasa/",
    61: "/football/portugal/liga-portugal/", 63: "/football/russia/premier-league/",
    536: "/football/saudi-arabia/saudi-professional-league/", 87: "/football/spain/laliga/",
    140: "/football/spain/laliga2/", 64: "/football/scotland/premiership/",
    67: "/football/sweden/allsvenskan/", 168: "/football/sweden/superettan/",
    69: "/football/switzerland/super-league/", 71: "/football/turkey/super-lig/",
    130: "/football/usa/mls/",
}
# ================================================================

ROME = ZoneInfo("Europe/Rome")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "live", "data")
SIGNALS_F = os.path.join(DATA, "signals.json")
STATE_F = os.path.join(DATA, "state.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
FM = "https://www.fotmob.com/api/data"
BE = "https://www.betexplorer.com"
ODDS_API = "https://api.the-odds-api.com/v4"
DEFAULT_CHAT_ID = "621551511"
NREQ = [0]
ODDS_STATUS = {}
_BE_PAGES = {}
_EVENTS = {}

EXCLUDE_NAME_RE = re.compile(
    r"\b(u-?1[5-9]|u-?2[0-3]|under ?\d\d|youth|primavera|reserve|reserves|women|woman|femenil|femenina|"
    r"feminine|f[ée]minine|frauen|damallsvenskan|wsl|nwsl|baller|futsal|beach|esports|amateur|"
    r"premier league 2|professional development|next pro|friendl\w*|cup|copa|coppa|coupe|pokal|ta[cç]a|"
    r"puchar|beker|kupa|cupa|kup|trophy|shield|super ?cup|supercopa|supercoppa|play-?offs?)\b", re.I)
RESERVE_TEAM_RE = re.compile(r"(\s(II|2|B|U-?\d\d)$|^jong\s|\breserves?\b|\bacademy\b|\bwomen\b|\bu-?\d\d\b)", re.I)
_INVIS = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_BE_CELL = re.compile(
    r'data-odd="([0-9]+(?:[.,][0-9]+)?)".{0,220}?data-pos="([012])".{0,160}?data-bookie="([^"]+)"',
    re.S)
_BE_LINK = re.compile(
    r'href="([^"]*/([A-Za-z0-9]+)/)"[^>]*class="in-match"[^>]*>\s*'
    r'<span>(?:<strong>)?([^<]+)(?:</strong>)?</span>\s*-\s*<span>(?:<strong>)?([^<]+)(?:</strong>)?</span>')

_STOP = {
    "fc", "cf", "sk", "ac", "as", "sc", "fk", "cd", "ud", "sd", "rc", "rcd", "afc", "ss", "us", "ssc", "vfb", "vfl",
    "tsg", "sv", "bk", "if", "ik", "ff", "aik", "club", "de", "del", "la", "le", "the", "calcio", "cfc", "fbc", "bsc",
    "kv", "krc", "rsc", "osc", "ogc", "sco", "esp", "nk", "hnk", "gnk", "ks", "sl", "ca", "cs", "ec", "se", "cr", "fbk",
}
_TRANSLIT = str.maketrans({"æ": "ae", "ø": "o", "œ": "oe", "ß": "ss", "ł": "l", "ı": "i", "đ": "d", "þ": "th", "ð": "d"})
_ALIAS = {"olympiakos": "olympiacos", "man": "manchester", "wolves": "wolverhampton", "spurs": "tottenham",
          "psg": "paris saint germain", "gladbach": "monchengladbach"}
_1X2_HEADERS = {"fulltimeresult", "matchodds", "1x2", "matchresult", "result1x2", "whowillwin"}


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def ts():
    return time.time()

def rome_str(dt=None):
    return (dt or now_utc()).astimezone(ROME).strftime("%Y-%m-%d %H:%M")

def log(*a):
    print(*a, flush=True)

def esc(s):
    return html.escape(str(s if s is not None else ""), quote=False)

def num(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).strip().replace(",", "."))
    return float(m.group(0)) if m else None

def fmt_odd(v):
    if v is None:
        return "n.d."
    return f"{float(v):.2f}".replace(".", ",")

def fmt_stat(v):
    if v is None:
        return "n.d."
    if abs(float(v) - round(float(v))) < 1e-9:
        return str(int(round(float(v))))
    return f"{float(v):.2f}".replace(".", ",")

def fmt_delta(d):
    sign = "+" if d >= 0 else "-"
    return sign + fmt_odd(abs(d))

def in_range(p, lo, hi):
    return p is not None and (lo - 1e-9) <= float(p) <= (hi + 1e-9)

def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

def norm_tokens(name):
    t = unicodedata.normalize("NFKD", str(name or "").translate(_TRANSLIT)).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    toks = []
    for w in t.split():
        toks.extend(_ALIAS.get(w, w).split())
    core = [w for w in toks if w not in _STOP and not w.isdigit()]
    return core or toks

def tok_eq(a, b):
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return len(a) >= 6 and len(b) >= 6 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.84

def name_score(a, b):
    A, B = norm_tokens(a), norm_tokens(b)
    if not A or not B:
        return 0.0
    hit = sum(1 for x in A if any(tok_eq(x, y) for y in B))
    return hit / min(len(A), len(B))

def http_open(url, headers=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "application/json,text/html;q=0.9,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.9"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers

def get_json(url, tries=3):
    err = None
    for i in range(tries):
        try:
            NREQ[0] += 1
            raw, _hdr = http_open(url)
            time.sleep(CFG["DELAY"])
            if not raw or raw.strip() in (b"null", b""):
                return None
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            err = e
            time.sleep(1.2 * (i + 1))
    raise err

def get_text(url, headers=None, tries=2):
    err = None
    for i in range(tries):
        try:
            NREQ[0] += 1
            raw, _hdr = http_open(url, headers=headers)
            time.sleep(CFG["DELAY"])
            return raw.decode("utf-8", "replace")
        except Exception as e:
            err = e
            time.sleep(1.2 * (i + 1))
    raise err

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)

def rules_dict():
    keys = ("SOT_MIN", "SOT_MARGIN", "BOX_RATIO_MIN", "POSS_MIN", "RED_MAX", "PRE_MIN", "PRE_MAX",
            "LIVE_MIN", "LIVE_MAX", "WINDOW_2H_MAX", "ODDS_MIN_REMAINING", "LOW_QUOTA_ACTION", "STAKE_PCT")
    return {k: CFG[k] for k in keys}

# ---------------------------------------------------------------- finestra e filtri
def parse_minute(short):
    s = _INVIS.sub("", str(short or ""))
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None

def in_window(status, cfg=None):
    """True se l'intervallo è HT oppure il 2° tempo è ancora <= WINDOW_2H_MAX, partita in corso,  non supplementari."""
    cfg = cfg or CFG
    if not status or not status.get("started") or status.get("finished") or status.get("cancelled"):
        return False, ""
    lt = status.get("liveTime") or {}
    if lt.get("penalties"):
        return False, ""
    if (lt.get("maxTime") or 0) > 90:
        return False, ""
    short = _INVIS.sub("", str(lt.get("short") or "")).strip()
    keys = f"{lt.get('shortKey') or ''} {lt.get('longKey') or ''}".lower()
    if short.upper() == "HT" or "halftime" in keys or "pause_match" in keys:
        return True, "HT"
    minute = parse_minute(short)
    halfs = status.get("halfs") or {}
    in_2h = bool(halfs.get("secondHalfStarted")) or (lt.get("maxTime") or 0) >= 90 or (lt.get("basePeriod") or 0) >= 90
    if in_2h and minute is not None and minute <= cfg["WINDOW_2H_MAX"]:
        return True, "2T"
    return False, ""

def is_score_00(status=None, home_score=None, away_score=None):
    if home_score is not None and away_score is not None:
        try:
            return int(home_score) == 0 and int(away_score) == 0
        except Exception:
            return False
    text = (status or {}).get("scoreStr") if isinstance(status, dict) else None
    m = re.search(r"(\d+)\s*-\s*(\d+)", str(text or ""))
    return bool(m and int(m.group(1)) == 0 and int(m.group(2)) == 0)

def favourite_side(home_p, away_p):
    if home_p is None or away_p is None:
        return None
    if abs(float(home_p) - float(away_p)) < 1e-9:
        return None
    return "home" if float(home_p) < float(away_p) else "away"

def box_ratio(fav, opp):
    if fav is None or opp is None:
        return None
    if opp == 0:
        return float("inf") if fav > 0 else 0.0
    return float(fav) / float(opp)

def _side_idx(side):
    return 0 if side == "home" else 1

def dominates(stats, side, cfg=None):
    """Filtri del primo tempo sulla squadra `side` ('home' o 'away')."""
    cfg = cfg or CFG
    if not stats:
        return False, "statistiche assenti"
    i, j = _side_idx(side), 1 - _side_idx(side)
    sot = stats.get("sot") or [None, None]
    if sot[i] is None or sot[j] is None:
        return False, "tiri in porta assenti"
    if sot[i] < cfg["SOT_MIN"]:
        return False, "tiri in porta sotto soglia"
    if sot[i] < sot[j] + cfg["SOT_MARGIN"]:
        return False, "margine tiri in porta insufficiente"
    box = stats.get("box") or [None, None]
    if box[i] is None or box[j] is None:
        return False, "tocchi in area assenti"
    ratio = box_ratio(box[i], box[j])
    if ratio is None or ratio < cfg["BOX_RATIO_MIN"]:
        return False, "rapporto tocchi in area sotto soglia"
    poss = stats.get("poss") or [None, None]
    if poss[i] is None:
        return False, "possesso assente"
    if poss[i] < cfg["POSS_MIN"]:
        return False, "possesso sotto soglia"
    red = stats.get("red") or [None, None]
    if red[i] is None:
        return False, "rossi assenti"
    if red[i] > cfg["RED_MAX"]:
        return False, "cartellino rosso"
    return True, ""

def usable_prematch(pre):
    if not pre or pre.get("unavailable"):
        return None
    if pre.get("home") is None or pre.get("away") is None:
        return None
    return pre

def pre_live_gate(stats, pre, cfg=None):
    """Decisione prima della chiamata a pagamento.

    status: reject | need_live | need_live_pick
    """
    cfg = cfg or CFG
    if not stats or stats.get("empty"):
        return {"status": "reject", "reasons": ["statistiche primo tempo assenti"], "favourite": None,
                "pre_price": None, "passers": []}
    pre_u = usable_prematch(pre)
    passers = []
    why = {}
    for side in ("home", "away"):
        ok, reason = dominates(stats, side, cfg)
        why[side] = reason
        if ok:
            passers.append(side)
    if pre_u:
        fav = favourite_side(pre_u.get("home"), pre_u.get("away"))
        if fav is None:
            return {"status": "reject", "reasons": ["quote prematch pari, nessun favorito"], "favourite": None,
                    "pre_price": None, "passers": passers}
        price = pre_u["home"] if fav == "home" else pre_u["away"]
        if not in_range(price, cfg["PRE_MIN"], cfg["PRE_MAX"]):
            return {"status": "reject", "reasons": [f"prematch {fmt_odd(price)} fuori range {fmt_odd(cfg['PRE_MIN'])}-{fmt_odd(cfg['PRE_MAX'])}"],
                    "favourite": fav, "pre_price": price, "passers": passers}
        if fav not in passers:
            return {"status": "reject", "reasons": [f"favorita non domina il primo tempo ({why.get(fav) or 'filtri'})"],
                    "favourite": fav, "pre_price": price, "passers": passers}
        return {"status": "need_live", "reasons": [], "favourite": fav, "pre_price": price, "passers": passers}
    if not passers:
        return {"status": "reject", "reasons": [f"casa: {why.get('home')}", f"ospite: {why.get('away')}"],
                "favourite": None, "pre_price": None, "passers": []}
    return {"status": "need_live_pick", "reasons": [], "favourite": None, "pre_price": None, "passers": passers}

def assess(stats, pre=None, live=None, quota_blocked=False, live_status="ok", action=None, cfg=None):
    """Decisione finale. live_status: ok | quota | error | absent."""
    cfg = cfg or CFG
    action = (action or cfg["LOW_QUOTA_ACTION"] or "alert").lower()
    gate = pre_live_gate(stats, pre, cfg)
    base = {"favourite": gate.get("favourite"), "pre_price": gate.get("pre_price"), "live_price": None,
            "passers": gate.get("passers") or [], "live_status": live_status}
    if gate["status"] == "reject":
        return {**base, "send": False, "final": True, "reasons": gate["reasons"]}
    if quota_blocked or live_status == "quota":
        base["live_status"] = "quota"
        if gate.get("favourite") is None:
            return {**base, "send": False, "final": False,
                    "reasons": ["prematch n.d. e crediti insufficienti: favorito non identificabile"]}
        if action == "skip":
            return {**base, "send": False, "final": False, "reasons": ["quota live sotto soglia, segnale saltato"]}
        return {**base, "send": True, "final": True, "reasons": ["quota live n.d."]}
    if live_status == "error" or (live is None and live_status != "absent"):
        return {**base, "send": False, "final": False, "live_status": "error", "reasons": ["errore quote live, riprovo"]}
    if live_status == "absent" or not live or live.get("home") is None or live.get("away") is None:
        return {**base, "send": False, "final": True, "live_status": "absent", "reasons": ["quota live assente"]}
    fav = gate.get("favourite")
    if fav is None:
        fav = favourite_side(live.get("home"), live.get("away"))
        if fav is None:
            return {**base, "send": False, "final": True, "reasons": ["quote live pari, nessun favorito"]}
        if fav not in (gate.get("passers") or []):
            return {**base, "send": False, "final": True, "favourite": fav,
                    "reasons": ["il favorito live non ha dominato il primo tempo"]}
        base["favourite"] = fav
    live_price = live.get("home") if fav == "home" else live.get("away")
    base["live_price"] = live_price
    if not in_range(live_price, cfg["LIVE_MIN"], cfg["LIVE_MAX"]):
        return {**base, "send": False, "final": True,
                "reasons": [f"quota live {fmt_odd(live_price)} fuori range {fmt_odd(cfg['LIVE_MIN'])}-{fmt_odd(cfg['LIVE_MAX'])}"]}
    return {**base, "send": True, "final": True, "reasons": []}

# ---------------------------------------------------------------- quote prematch
def _header_key(market):
    h = re.sub(r"[^a-z0-9]+", "", str(market.get("header") or "").lower())
    tk = ""
    mid = market.get("fotMobMarketTypeId") or {}
    if isinstance(mid, dict):
        tk = str(mid.get("translationKey") or "").lower()
    return h, tk

def parse_fotmob_prematch(payload):
    """Solo oddsType PreMatch. Dopo il calcio d'inizio FotMob passa a Live e qui si restituisce None."""
    if not isinstance(payload, dict):
        return None
    odds = payload.get("odds") or {}
    if odds.get("oddsType") != "PreMatch":
        return None
    markets = []
    for blk in odds.get("oddsTabMarkets") or []:
        markets.extend(blk.get("markets") or [])
    markets = (odds.get("matchfactMarkets") or []) + markets
    for market in markets:
        h, tk = _header_key(market)
        if tk != "who_will_win" and h not in _1X2_HEADERS and not h.startswith("fulltime"):
            continue
        found = {}
        for sel in market.get("selections") or []:
            name = str(sel.get("name") or "").strip().lower()
            price = num(sel.get("oddsDecimal"))
            if price is None:
                continue
            if name in ("1", "home"):
                found["home"] = price
            elif name in ("x", "draw", "pareggio"):
                found["draw"] = price
            elif name in ("2", "away"):
                found["away"] = price
        if found.get("home") is not None and found.get("away") is not None:
            return {"home": found["home"], "draw": found.get("draw"), "away": found["away"],
                    "source": "fotmob:" + str(payload.get("persistentKey") or "prematch"),
                    "ts": int(ts())}
    return None

def parse_betexplorer_events(page_html):
    events, seen = [], set()
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page_html or "", re.S):
        try:
            blob = json.loads(m.group(1).strip())
        except Exception:
            continue
        items = blob if isinstance(blob, list) else [blob]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "SportsEvent":
                continue
            url = item.get("url") or ""
            mm = re.search(r"/([A-Za-z0-9]+)/?$", url.rstrip("/"))
            parts = re.split(r"\s+-\s+", str(item.get("name") or ""), maxsplit=1)
            if not mm or len(parts) != 2:
                continue
            eid = mm.group(1)
            events.append({"id": eid, "home": parts[0].strip(), "away": parts[1].strip(),
                           "start": item.get("startDate"), "url": url})
            seen.add(eid)
    for m in _BE_LINK.finditer(page_html or ""):
        eid = m.group(2)
        if eid in seen:
            continue
        events.append({"id": eid, "home": html.unescape(m.group(3)).strip(), "away": html.unescape(m.group(4)).strip(),
                       "start": None, "url": m.group(1)})
        seen.add(eid)
    return events

def pick_be_event(events, home, away, ko_iso):
    ko = parse_iso(ko_iso)
    best, best_key = None, None
    for e in events or []:
        sh, sa = name_score(home, e.get("home")), name_score(away, e.get("away"))
        if min(sh, sa) < 0.6:
            continue
        penalty = 0
        if ko and e.get("start"):
            dt = parse_iso(e.get("start"))
            if dt is None:
                penalty = 3600
            else:
                delta = abs((dt - ko).total_seconds())
                if delta > CFG["ODDS_TIME_TOL_H"] * 3600:
                    continue
                penalty = delta
        elif ko:
            penalty = 3 * 3600
        key = (sh + sa, -penalty)
        if best_key is None or key > best_key:
            best, best_key = e, key
    return best

def parse_betexplorer_odds(odds_html):
    """1 = casa, 0 = pareggio, 2 = ospite. Preferisce bet365, poi un altro book completo."""
    books = {}
    order = []
    for odd, pos, book in _BE_CELL.findall(odds_html or ""):
        slot = books.setdefault(book, {})
        if book not in order:
            order.append(book)
        if pos not in slot:
            slot[pos] = num(odd)
    triples = []
    for book in order:
        slot = books[book]
        if slot.get("1") is None or slot.get("2") is None:
            continue
        triples.append((book, slot["1"], slot.get("0"), slot["2"]))
    if not triples:
        return None
    def rank(item):
        name = item[0].lower()
        for i, key in enumerate(("bet365", "pinnacle", "unibet", "william", "bwin", "betfair")):
            if key in name:
                return i
        return 50
    triples.sort(key=rank)
    book, h, d, a = triples[0]
    return {"home": h, "draw": d, "away": a, "source": "betexplorer:" + book, "ts": int(ts())}

def _be_page(path):
    if path in _BE_PAGES:
        return _BE_PAGES[path]
    page = get_text(BE + path, headers={"Referer": BE + "/"})
    _BE_PAGES[path] = page
    return page

def fetch_fotmob_prematch(mid):
    for cc in ("GBR", "ESP", "USA", "DEU", "FRA"):
        try:
            data = get_json(f"{FM}/matchOdds?matchId={int(mid)}&ccode3={cc}")
        except Exception as e:
            log(f"   prematch FotMob {cc}: {type(e).__name__}")
            continue
        if not data:
            continue
        parsed = parse_fotmob_prematch(data)
        if parsed:
            return parsed
        if (data.get("odds") or {}).get("oddsType") == "Live":
            return None
    return None

def fetch_betexplorer_prematch(lid, home, away, ko_iso):
    path = BE_PATH.get(int(lid)) if lid else None
    if not path:
        return None, False
    try:
        page = _be_page(path)
    except Exception as e:
        log(f"   BetExplorer pagina: {type(e).__name__}")
        return None, False
    ev = pick_be_event(parse_betexplorer_events(page), home, away, ko_iso)
    if not ev:
        return None, True
    last_error = False
    for stage in ("1", "0"):
        url = f"{BE}/match-odds/{ev['id']}/{stage}/1x2/odds/?lang=en"
        try:
            raw = get_text(url, headers={"Referer": BE + path, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
            blob = json.loads(raw)
        except Exception as e:
            log(f"   BetExplorer quote: {type(e).__name__}")
            last_error = True
            continue
        parsed = parse_betexplorer_odds((blob or {}).get("odds") or "")
        if parsed:
            parsed["event_id"] = ev["id"]
            return parsed, True
    return None, not last_error

def lookup_prematch(lid, home, away, ko_iso, mid, allow_fotmob=True):
    """Ritorna (quote|None, missing_confermato)."""
    if allow_fotmob:
        try:
            parsed = fetch_fotmob_prematch(mid)
        except Exception as e:
            log(f"   prematch FotMob: {type(e).__name__}")
            parsed = None
        if parsed:
            return parsed, True
    return fetch_betexplorer_prematch(lid, home, away, ko_iso)

# ---------------------------------------------------------------- quote live (The Odds API)
def quota_low(state, cfg=None):
    cfg = cfg or CFG
    rem = (state.get("odds_api") or {}).get("remaining")
    if rem is None or rem == "":
        return False
    try:
        return int(float(rem)) < int(cfg["ODDS_MIN_REMAINING"])
    except Exception:
        return False

def _remember_quota(headers):
    if not headers:
        return
    rem = headers.get("x-requests-remaining")
    if rem is None:
        return
    ODDS_STATUS["remaining"] = rem
    ODDS_STATUS["used"] = headers.get("x-requests-used")
    ODDS_STATUS["last_cost"] = headers.get("x-requests-last")
    ODDS_STATUS["time"] = rome_str()

def odds_get(path, params):
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError("chiave API assente")
    q = urllib.parse.urlencode(dict(params, apiKey=key))
    req = urllib.request.Request(f"{ODDS_API}{path}?{q}", headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
            hdr = r.headers
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Odds API HTTP {e.code}") from None
    except Exception as e:
        raise RuntimeError(f"Odds API errore {type(e).__name__}") from None
    _remember_quota(hdr)
    return data

def pick_odds_event(events, home, away, ko_iso):
    ko = parse_iso(ko_iso)
    best, best_key = None, None
    for e in events or []:
        if ko and e.get("commence_time"):
            ct = parse_iso(e.get("commence_time"))
            if ct is not None and abs((ct - ko).total_seconds()) > CFG["ODDS_TIME_TOL_H"] * 3600:
                continue
        sh, sa = name_score(home, e.get("home_team")), name_score(away, e.get("away_team"))
        if min(sh, sa) < 0.6:
            continue
        key = sh + sa
        if best_key is None or key > best_key:
            best, best_key = e, key
    return best

def parse_h2h_book(book, home, away):
    for mk in book.get("markets") or []:
        if mk.get("key") != "h2h":
            continue
        draw = None
        teams = []
        for o in mk.get("outcomes") or []:
            name = str(o.get("name") or "").strip()
            price = num(o.get("price"))
            if price is None or not name:
                continue
            if name.lower() in ("draw", "pareggio", "x", "tie"):
                draw = price
            else:
                teams.append((name, price))
        if len(teams) < 2:
            continue
        a, b = teams[0], teams[1]
        if name_score(home, a[0]) + name_score(away, b[0]) >= name_score(home, b[0]) + name_score(away, a[0]):
            home_p, away_p = a, b
        else:
            home_p, away_p = b, a
        if name_score(home, home_p[0]) < 0.5 or name_score(away, away_p[0]) < 0.5:
            continue
        return {"home": home_p[1], "draw": draw, "away": away_p[1]}
    return None

def parse_event_odds(payload, home, away):
    books = payload.get("bookmakers") or []
    by_key = {b.get("key"): b for b in books}
    order, seen = [], set()
    for key in (ODDS_BOOK_PREFERRED,) + tuple(ODDS_BOOK_FALLBACK) + tuple(by_key):
        if not key or key in seen:
            continue
        seen.add(key)
        order.append(key)
    for key in order:
        book = by_key.get(key)
        if not book:
            continue
        prices = parse_h2h_book(book, home, away)
        if not prices:
            continue
        prices["book_key"] = key
        prices["book_label"] = "Betfair" if key == ODDS_BOOK_PREFERRED else "book europeo"
        return prices
    return None

def fetch_live_h2h(sport, home, away, ko_iso, state):
    """Ritorna (quote|None, status) con status ok|quota|absent|error. Al massimo 1 chiamata a pagamento."""
    if quota_low(state):
        return None, "quota"
    try:
        if sport not in _EVENTS:
            _EVENTS[sport] = odds_get(f"/sports/{sport}/events", {"dateFormat": "iso"})
        events = _EVENTS[sport]
    except Exception as e:
        log(f"   eventi Odds API: {e}")
        return None, "error"
    if quota_low(state):
        return None, "quota"
    ev = pick_odds_event(events, home, away, ko_iso)
    if not ev:
        return None, "absent"
    try:
        data = odds_get(f"/sports/{sport}/events/{ev['id']}/odds",
                        {"regions": "eu", "markets": "h2h", "oddsFormat": "decimal"})
    except Exception as e:
        log(f"   quote Odds API: {e}")
        return None, "error"
    cost = str(ODDS_STATUS.get("last_cost") or "")
    if cost and cost not in ("0", "1"):
        log(f"   attenzione: ultima chiamata Odds API ha costo {cost}")
    parsed = parse_event_odds(data, home, away)
    if not parsed:
        return None, "absent"
    return parsed, "ok"

# ---------------------------------------------------------------- statistiche FotMob
def extract_fh(md):
    periods = (((md.get("content") or {}).get("stats") or {}).get("Periods") or {})
    fh = periods.get("FirstHalf") or {}
    raw = {}
    for grp in fh.get("stats") or []:
        for s in grp.get("stats") or []:
            k, v = s.get("key"), s.get("stats")
            if k and v and k not in raw and v[0] is not None:
                raw[k] = v
    def pair(key):
        v = raw.get(key)
        if not v or len(v) < 2:
            return [None, None]
        return [num(v[0]), num(v[1])]
    sot, poss, box, xg, red = pair("ShotsOnTarget"), pair("BallPossesion"), pair("touches_opp_box"), pair("expected_goals"), pair("red_cards")
    if red[0] is None or red[1] is None:
        st = (md.get("header") or {}).get("status") or {}
        if red[0] is None:
            red[0] = num(st.get("numberOfHomeRedCards"))
            if red[0] is None:
                red[0] = 0
        if red[1] is None:
            red[1] = num(st.get("numberOfAwayRedCards"))
            if red[1] is None:
                red[1] = 0
    empty = all(v is None for v in (sot + poss + box))
    return {"sot": sot, "poss": poss, "box": box, "xg": xg, "red": red, "empty": empty}

def first_half_goal(goal):
    period = str((goal.get("shotmapEvent") or {}).get("period") or "")
    if period == "FirstHalf":
        return True
    if period == "SecondHalf":
        return False
    t = goal.get("time")
    return t is not None and int(t) <= 45

def ht_score(md):
    """Gol del primo tempo da header.events. None se gli eventi non ci sono."""
    events = (md.get("header") or {}).get("events")
    if not isinstance(events, dict):
        return None
    if "homeTeamGoals" not in events and "awayTeamGoals" not in events:
        return None
    h = a = 0
    for goal in _iter_goals(events.get("homeTeamGoals")):
        if first_half_goal(goal):
            h += 1
    for goal in _iter_goals(events.get("awayTeamGoals")):
        if first_half_goal(goal):
            a += 1
    return h, a

def _iter_goals(group):
    if isinstance(group, dict):
        for goals in group.values():
            for g in goals or []:
                if isinstance(g, dict):
                    yield g
    elif isinstance(group, list):
        for g in group:
            if isinstance(g, dict):
                yield g

def odds_sport(ids):
    for i in ids or []:
        try:
            i = int(i)
        except Exception:
            continue
        if i in ODDS_SPORT:
            return i, ODDS_SPORT[i]
    return None, None

def covered_league(name, ids, ccode="", gender=None):
    if (ccode or "").upper() == "INT":
        return False, None, None, "internazionale"
    if gender and str(gender).lower() not in ("male", "unknown", ""):
        return False, None, None, "non maschile"
    if EXCLUDE_NAME_RE.search(name or ""):
        return False, None, None, "nome campionato escluso"
    lid, sport = odds_sport(ids)
    if not sport:
        return False, None, None, "campionato non coperto"
    return True, lid, sport, ""

def teams_ok(home, away):
    return not (RESERVE_TEAM_RE.search(home or "") or RESERVE_TEAM_RE.search(away or ""))

def iter_listed(payload):
    for L in (payload or {}).get("leagues") or []:
        ids = []
        for k in ("primaryId", "parentLeagueId", "id"):
            if L.get(k) is not None:
                try:
                    ids.append(int(L.get(k)))
                except Exception:
                    pass
        for m in L.get("matches") or []:
            item = dict(m)
            item["_league"] = {"name": L.get("name") or "", "ccode": L.get("ccode") or "", "ids": ids}
            yield item

def phase_label(status, phase):
    if phase == "HT":
        return "Intervallo"
    minute = parse_minute((status.get("liveTime") or {}).get("short"))
    if minute is not None:
        return f"{minute}'"
    return "2° tempo"

# ---------------------------------------------------------------- messaggio
def _ratio_text(stats, side):
    i = _side_idx(side)
    return box_ratio((stats.get("box") or [None, None])[i], (stats.get("box") or [None, None])[1 - i])

def alternative_text(stats, side):
    i = _side_idx(side)
    sot = stats.get("sot") or [None, None]
    xg = (stats.get("xg") or [None, None])[i]
    margin = None
    if sot[i] is not None and sot[1 - i] is not None:
        margin = sot[i] - sot[1 - i]
    ratio = _ratio_text(stats, side)
    if xg is not None and xg >= 1.0 and margin is not None and margin >= 3:
        core = (f"Over 1,5 solo se la favorita segna presto (xG del primo tempo {fmt_stat(xg)}): "
                "è un'idea separata, non questa giocata.")
    elif ratio is not None and ratio >= 2:
        core = "Over 0,5 secondo tempo, oppure il prossimo gol della favorita: il dominio territoriale del primo tempo è netto."
    else:
        core = "Over 0,5 secondo tempo o prossimo gol della favorita. Over 1,5 ha senso solo se la favorita sblocca presto."
    return core

def why_text(info, cfg=None):
    cfg = cfg or CFG
    fav, opp = info["favourite_team"], info["opponent"]
    stats = info["stats"]
    i = _side_idx(info["favourite_side"])
    j = 1 - i
    sot, poss, box, xg = stats["sot"], stats["poss"], stats["box"], stats.get("xg") or [None, None]
    ratio = _ratio_text(stats, info["favourite_side"])
    bits = []
    if info.get("prematch_price") is None:
        bits.append(f"{fav} è la favorita individuata senza quota prematch (prematch n.d.)")
    else:
        bits.append(f"{fav} era favorita in prematch a {fmt_odd(info['prematch_price'])}")
    if info.get("live_quota_nd"):
        bits.append("a gioco fermo sullo 0-0 la quota live non è stata letta (quota live n.d.)")
    elif info.get("live_price") is not None and info.get("prematch_price") is not None:
        delta = float(info["live_price"]) - float(info["prematch_price"])
        if delta >= 0.05:
            bits.append(f"sullo 0-0 la quota è salita a {fmt_odd(info['live_price'])} ({fmt_delta(delta)} rispetto al prematch)")
        elif delta <= -0.05:
            bits.append(f"sullo 0-0 la quota live è {fmt_odd(info['live_price'])}, più bassa del prematch ({fmt_delta(delta)})")
        else:
            bits.append(f"la quota live {fmt_odd(info['live_price'])} è in linea con il prematch")
    elif info.get("live_price") is not None:
        bits.append(f"quota live back {fmt_odd(info['live_price'])}")
    ratio_txt = "dominio pieno, avversario a zero tocchi" if ratio == float("inf") else f"rapporto {fmt_stat(ratio)}"
    bits.append(
        f"nel primo tempo {sot[i]} tiri in porta contro {sot[j]} di {opp}, "
        f"possesso {fmt_stat(poss[i])}%, tocchi in area avversaria {fmt_stat(box[i])}-{fmt_stat(box[j])} ({ratio_txt})"
    )
    if xg[i] is not None and xg[j] is not None:
        bits.append(f"xG primo tempo {fmt_stat(xg[i])}-{fmt_stat(xg[j])}")
    bits.append("nessun cartellino rosso alla favorita")
    return ". ".join(bits) + "."

def build_message(info, test=False, cfg=None):
    cfg = cfg or CFG
    L = []
    if test:
        L.append("🧪 <b>TEST</b> — messaggio di prova, non è un segnale operativo")
    L.append("⚽ <b>FAVORITA HT 0-0</b>")
    L.append(f"🏆 {esc(info.get('league'))} ({esc(info.get('country'))})")
    L.append(f"⚽ <b>{esc(info['home'])} - {esc(info['away'])}</b>")
    L.append(f"⏱ {esc(info.get('phase_label') or '?')}   📊 {esc(info.get('score') or '0 - 0')}")
    L.append("")
    if info.get("prematch_price") is None:
        L.append("💰 Prematch favorita: <b>prematch n.d.</b>")
    else:
        src = info.get("prematch_source") or "prematch"
        L.append(f"💰 Prematch {esc(info['favourite_team'])}: <b>{fmt_odd(info['prematch_price'])}</b> ({esc(src)})")
        L.append("   1X2 prematch: "
                 + " - ".join(fmt_odd(info.get(k)) for k in ("prematch_home", "prematch_draw", "prematch_away")))
    if info.get("live_quota_nd"):
        L.append("💰 Live favorita: <b>quota live n.d.</b>")
    elif info.get("live_price") is None:
        L.append("💰 Live favorita: <b>n.d.</b>")
    else:
        lab = info.get("live_book") or "live"
        L.append(f"💰 Live {esc(info['favourite_team'])} back ({esc(lab)}): <b>{fmt_odd(info['live_price'])}</b>")
        if info.get("live_home") is not None:
            L.append("   1X2 live: " + " - ".join(fmt_odd(info.get(k)) for k in ("live_home", "live_draw", "live_away")))
    st = info["stats"]
    L.append("")
    L.append("📡 <b>Primo tempo</b> (casa - ospite)")
    L.append(f"   Tiri in porta: {fmt_stat(st['sot'][0])} - {fmt_stat(st['sot'][1])}")
    L.append(f"   Possesso: {fmt_stat(st['poss'][0])}% - {fmt_stat(st['poss'][1])}%")
    L.append(f"   Tocchi in area avversaria: {fmt_stat(st['box'][0])} - {fmt_stat(st['box'][1])}")
    xg = st.get("xg") or [None, None]
    if xg[0] is not None or xg[1] is not None:
        L.append(f"   xG: {fmt_stat(xg[0])} - {fmt_stat(xg[1])}")
    L.append(f"   Rossi: {fmt_stat(st['red'][0])} - {fmt_stat(st['red'][1])}")
    L.append("")
    L.append("🧠 <b>PERCHÉ la giocata</b>")
    L.append(esc(why_text(info, cfg)))
    L.append("")
    L.append("🎯 <b>COSA giocare</b>")
    L.append(f"Punta la favorita <b>{esc(info['favourite_team'])}</b>, Esito Finale / Match Odds (1X2).")
    if info.get("live_price") is not None:
        L.append(f"Quota minima consigliata: <b>{fmt_odd(cfg['LIVE_MIN'])}</b> "
                 f"(quota vista {fmt_odd(info['live_price'])}; non accettare sotto {fmt_odd(cfg['LIVE_MIN'])} "
                 f"né sopra {fmt_odd(cfg['LIVE_MAX'])}).")
    else:
        L.append(f"Quota minima consigliata: <b>{fmt_odd(cfg['LIVE_MIN'])}</b>, "
                 f"e non entrare sopra {fmt_odd(cfg['LIVE_MAX'])}. "
                 "Verifica la quota live prima di puntare: qui non è stata confermata.")
    L.append(f"Stake: <b>{int(cfg['STAKE_PCT'])}% del capitale</b>. Una sola entrata, niente raddoppi.")
    L.append("")
    L.append("🚪 <b>COME e QUANDO uscire</b>")
    L.append(f"• Green up quando, dopo il gol della favorita, la quota scende a circa {fmt_odd(cfg['GREENUP_ODDS'])}.")
    L.append("• Stop loss se segna l'avversaria: chiudi subito.")
    L.append(f"• Se al {int(cfg['EXIT_MINUTE_FROM'])}'-{int(cfg['EXIT_MINUTE_TO'])}' è ancora 0-0, chiudi la posizione (perdita parziale).")
    L.append("• Esci prima se la favorita prende un rosso o se cala in modo netto la pressione.")
    L.append("")
    L.append("🔄 <b>GIOCATA ALTERNATIVA (facoltativa)</b>")
    L.append(esc(alternative_text(info["stats"], info["favourite_side"])))
    L.append("È facoltativa e non va sommata allo stake. Nessun altro messaggio su questa partita.")
    if info.get("match_id"):
        L.append("")
        L.append(f'<a href="https://www.fotmob.com/match/{int(info["match_id"])}">Apri su FotMob</a>')
    return "\n".join(L)

def send_telegram(text):
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID") or DEFAULT_CHAT_ID
    if not tok:
        log("  [telegram] TELEGRAM_BOT_TOKEN assente: messaggio non inviato")
        return False
    body = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=body)
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = json.load(r).get("ok", False)
        log(f"  [telegram] inviato: {ok}")
        return bool(ok)
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode()[:300]
        except Exception:
            msg = ""
        log(f"  [telegram] errore HTTP {e.code} {msg}")
        return False
    except Exception as e:
        log(f"  [telegram] errore {type(e).__name__}")
        return False

def signal_record(info):
    stats = info["stats"]
    i = _side_idx(info["favourite_side"])
    return {
        "id": f"{info['match_id']}-favht",
        "strategy": "favht",
        "matchId": int(info["match_id"]),
        "time": info.get("time") or rome_str(),
        "ts": int(ts()),
        "league": info.get("league"),
        "country": info.get("country"),
        "leagueId": info.get("league_id"),
        "home": info["home"],
        "away": info["away"],
        "favourite": info["favourite_side"],
        "favouriteTeam": info["favourite_team"],
        "phase": info.get("phase_label"),
        "minute": info.get("minute"),
        "score": info.get("score"),
        "odds": {
            "prematch": None if info.get("prematch_price") is None else {
                "home": info.get("prematch_home"), "draw": info.get("prematch_draw"), "away": info.get("prematch_away"),
                "favourite": info.get("prematch_price"), "source": info.get("prematch_source"),
            },
            "live": None if info.get("live_quota_nd") or info.get("live_price") is None else {
                "home": info.get("live_home"), "draw": info.get("live_draw"), "away": info.get("live_away"),
                "favourite": info.get("live_price"), "book": info.get("live_book"),
            },
            "live_note": "quota live n.d." if info.get("live_quota_nd") else None,
        },
        "stats": {
            "sot": stats["sot"], "poss": stats["poss"], "box": stats["box"], "xg": stats.get("xg"),
            "red": stats["red"], "box_ratio": None if _ratio_text(stats, info["favourite_side"]) == float("inf")
            else _ratio_text(stats, info["favourite_side"]),
            "favourite_index": i,
        },
        "url": f"https://www.fotmob.com/match/{int(info['match_id'])}",
    }

def make_info(md, decision, pre, live, phase, status):
    g = md.get("general") or {}
    home = (g.get("homeTeam") or {}).get("name") or ""
    away = (g.get("awayTeam") or {}).get("name") or ""
    fav = decision.get("favourite") or "home"
    stats = decision.get("stats") or extract_fh(md)
    pre_u = usable_prematch(pre)
    return {
        "match_id": int(g.get("matchId") or 0),
        "home": home, "away": away,
        "league": g.get("leagueName") or "",
        "country": g.get("countryCode") or "",
        "league_id": decision.get("league_id"),
        "phase_label": phase_label(status, phase),
        "minute": parse_minute((status.get("liveTime") or {}).get("short")),
        "score": (status or {}).get("scoreStr") or "0 - 0",
        "favourite_side": fav,
        "favourite_team": home if fav == "home" else away,
        "opponent": away if fav == "home" else home,
        "prematch_price": decision.get("pre_price") if pre_u else None,
        "prematch_home": None if not pre_u else pre_u.get("home"),
        "prematch_draw": None if not pre_u else pre_u.get("draw"),
        "prematch_away": None if not pre_u else pre_u.get("away"),
        "prematch_source": None if not pre_u else pre_u.get("source"),
        "live_price": decision.get("live_price"),
        "live_home": None if not live else live.get("home"),
        "live_draw": None if not live else live.get("draw"),
        "live_away": None if not live else live.get("away"),
        "live_book": None if not live else live.get("book_label"),
        "live_quota_nd": decision.get("live_status") == "quota",
        "stats": stats,
        "time": rome_str(),
    }

# ---------------------------------------------------------------- scansione
def match_dates():
    today = now_utc().astimezone(ROME)
    days = [today]
    if today.hour < 6:
        days.insert(0, today - datetime.timedelta(days=1))
    if today.hour >= 18:
        days.append(today + datetime.timedelta(days=1))
    return days

def load_listed_matches():
    out = {}
    for d in match_dates():
        payload = get_json(f"{FM}/matches?date={d.strftime('%Y%m%d')}&timezone=Europe%2FRome")
        for m in iter_listed(payload):
            try:
                out[int(m["id"])] = m
            except Exception:
                continue
    return out

def snapshot_prematch(matches, state):
    now = now_utc()
    horizon = now + datetime.timedelta(hours=CFG["PREMATCH_LOOKAHEAD_H"])
    soon = []
    for mid, m in matches.items():
        st = m.get("status") or {}
        if st.get("started") or st.get("cancelled"):
            continue
        if str(mid) in state["prematch"]:
            continue
        ok, lid, _sport, _why = covered_league(m["_league"]["name"], m["_league"]["ids"], m["_league"]["ccode"])
        if not ok or not teams_ok((m.get("home") or {}).get("name"), (m.get("away") or {}).get("name")):
            continue
        ko = parse_iso(st.get("utcTime"))
        if ko is None or ko < now - datetime.timedelta(minutes=20) or ko > horizon:
            continue
        soon.append((ko, mid, m, lid))
    soon.sort(key=lambda x: x[0])
    n = 0
    for ko, mid, m, lid in soon:
        if n >= CFG["PREMATCH_FETCH_CAP"]:
            break
        n += 1
        home, away = m["home"]["name"], m["away"]["name"]
        log(f"snapshot prematch {home} - {away}")
        try:
            parsed = fetch_fotmob_prematch(mid)
        except Exception as e:
            log(f"   snapshot fallito: {type(e).__name__}")
            continue
        if not parsed:
            continue
        parsed["ts"] = int(ts())
        parsed["home_name"] = home
        parsed["away_name"] = away
        parsed["league_id"] = lid
        state["prematch"][str(mid)] = parsed
        log(f"   {fmt_odd(parsed['home'])} / {fmt_odd(parsed.get('draw'))} / {fmt_odd(parsed['away'])} ({parsed['source']})")

def list_candidates(matches, state):
    cands = []
    for mid, m in matches.items():
        st = m.get("status") or {}
        if str(mid) in state["sent"] or str(mid) in state["done"]:
            continue
        if not teams_ok((m.get("home") or {}).get("name"), (m.get("away") or {}).get("name")):
            continue
        ok, _lid, _sport, _why = covered_league(m["_league"]["name"], m["_league"]["ids"], m["_league"]["ccode"])
        if not ok:
            continue
        win, _phase = in_window(st)
        if not win:
            continue
        if not is_score_00(st, (m.get("home") or {}).get("score"), (m.get("away") or {}).get("score")):
            continue
        cands.append(m)
    cands.sort(key=lambda m: (m["_league"]["name"], (m.get("home") or {}).get("name") or ""))
    return cands

def mark_done(state, mid, reason):
    state["done"][str(mid)] = {"t": int(ts()), "r": reason[:180]}
    log(f"   chiusa: {reason}")

def reseed_sent(state, signals):
    state.setdefault("sent", {})
    state.setdefault("done", {})
    state.setdefault("prematch", {})
    for s in signals or []:
        mid = str(s.get("matchId") or "")
        if mid:
            state["sent"].setdefault(mid, {"t": s.get("ts") or 0, "time": s.get("time")})

def handle_candidate(m, state, signals, dry):
    mid = int(m["id"])
    home, away = m["home"]["name"], m["away"]["name"]
    log(f"- {m['_league']['ccode']} {m['_league']['name']}: {home} - {away}")
    md = get_json(f"{FM}/matchDetails?matchId={mid}")
    g = md.get("general") or {}
    status = (md.get("header") or {}).get("status") or {}
    ids = list(m["_league"]["ids"])
    if g.get("parentLeagueId"):
        ids.insert(0, int(g.get("parentLeagueId")))
    ok, lid, sport, why = covered_league(g.get("leagueName") or m["_league"]["name"], ids,
                                         g.get("countryCode") or m["_league"]["ccode"], g.get("gender"))
    if not ok or not teams_ok(g.get("homeTeam", {}).get("name"), g.get("awayTeam", {}).get("name")):
        mark_done(state, mid, why or "squadre escluse")
        return False
    if not is_score_00(status):
        mark_done(state, mid, "non più 0-0")
        return False
    win, phase = in_window(status)
    if not win:
        minute = parse_minute((status.get("liveTime") or {}).get("short")) or 0
        if status.get("finished") or minute > CFG["WINDOW_2H_MAX"]:
            mark_done(state, mid, "fuori finestra")
        else:
            log("   non più in finestra, riprovo")
        return False
    stats = extract_fh(md)
    log(f"   1T SoT {stats['sot']} poss {stats['poss']} box {stats['box']} xG {stats['xg']} rossi {stats['red']}")
    pre = usable_prematch(state["prematch"].get(str(mid)))
    if pre is None and not (state["prematch"].get(str(mid)) or {}).get("unavailable"):
        parsed, confirmed = lookup_prematch(lid, home, away, g.get("matchTimeUTCDate") or (m.get("status") or {}).get("utcTime"), mid)
        if parsed:
            parsed["ts"] = int(ts())
            parsed["league_id"] = lid
            state["prematch"][str(mid)] = parsed
            pre = parsed
            log(f"   prematch {fmt_odd(parsed['home'])} / {fmt_odd(parsed.get('draw'))} / {fmt_odd(parsed['away'])} ({parsed['source']})")
        elif confirmed:
            state["prematch"][str(mid)] = {"unavailable": True, "ts": int(ts()), "source": "none"}
            log("   prematch n.d.")
        else:
            log("   prematch non letto (rete), riprovo al giro dopo")
    elif pre:
        log(f"   prematch in cache {fmt_odd(pre['home'])} / {fmt_odd(pre.get('draw'))} / {fmt_odd(pre['away'])} ({pre.get('source')})")
    else:
        log("   prematch n.d. (già cercato)")
    gate = pre_live_gate(stats, pre)
    if gate["status"] == "reject":
        mark_done(state, mid, "; ".join(gate["reasons"]) or "filtri")
        return False
    log(f"   gate {gate['status']} favorita={gate.get('favourite') or 'da quote live'} passano={gate.get('passers')}")
    if dry:
        log("   dry-run: filtri ok, niente chiamata The Odds API e niente Telegram")
        return False
    if not os.environ.get("ODDS_API_KEY"):
        log("   ODDS_API_KEY assente, riprovo")
        return False
    live, live_status = fetch_live_h2h(sport, home, away, g.get("matchTimeUTCDate"), state)
    if ODDS_STATUS:
        state["odds_api"] = dict(ODDS_STATUS)
    decision = assess(stats, pre, live, quota_blocked=(live_status == "quota"), live_status=live_status)
    decision["stats"] = stats
    decision["league_id"] = lid
    log(f"   esito send={decision['send']} {'; '.join(decision['reasons'])}")
    if not decision["send"]:
        if decision["final"]:
            mark_done(state, mid, "; ".join(decision["reasons"]) or "scartata")
        return False
    info = make_info(md, decision, pre, live, phase, status)
    msg = build_message(info)
    if not send_telegram(msg):
        log("   Telegram non inviato, non segno la partita")
        return False
    state["sent"][str(mid)] = {"t": int(ts()), "time": rome_str()}
    signals.append(signal_record(info))
    save_json(STATE_F, state)
    save_json(SIGNALS_F, signals)
    return True

def prune_state(state):
    now = ts()
    for key, ttl in (("done", 2 * 86400), ("prematch", 3 * 86400)):
        bucket = state.get(key) or {}
        for k in list(bucket):
            item = bucket[k]
            t = item.get("ts") or item.get("t") or 0 if isinstance(item, dict) else 0
            if now - t > ttl:
                del bucket[k]
    for k in list(state.get("sent") or {}):
        item = state["sent"][k]
        t = item.get("t") if isinstance(item, dict) else 0
        if t and now - t > 14 * 86400:
            del state["sent"][k]

VOLATILE = ("last_scan", "last_write", "last_write_ts", "live_count", "candidates_last", "requests_last")

def state_fingerprint(state):
    return json.dumps({k: v for k, v in state.items() if k not in VOLATILE}, sort_keys=True, ensure_ascii=False)

def scan(dry=False):
    state = load_json(STATE_F, {})
    if not isinstance(state, dict):
        state = {}
    signals = load_json(SIGNALS_F, [])
    if not isinstance(signals, list):
        signals = []
    reseed_sent(state, signals)
    old_fp = state_fingerprint(state)
    old_sig = json.dumps(signals, sort_keys=True, ensure_ascii=False)
    matches = load_listed_matches()
    live_n = sum(1 for m in matches.values() if (m.get("status") or {}).get("started") and not (m.get("status") or {}).get("finished"))
    log(f"partite in elenco: {len(matches)}, live: {live_n}")
    snapshot_prematch(matches, state)
    cands = list_candidates(matches, state)
    log(f"candidate finestra HT/2T≤{CFG['WINDOW_2H_MAX']} sullo 0-0: {len(cands)}")
    n_sig = 0
    for m in cands:
        try:
            if handle_candidate(m, state, signals, dry):
                n_sig += 1
        except Exception as e:
            log(f"   errore: {type(e).__name__}: {e}")
    prune_state(state)
    state["rules"] = rules_dict()
    state["live_count"] = live_n
    state["candidates_last"] = len(cands)
    state["last_scan"] = rome_str()
    state["requests_last"] = NREQ[0]
    if ODDS_STATUS:
        state["odds_api"] = dict(ODDS_STATUS)
    if dry:
        log(f"dry-run: niente salvataggio. segnali nuovi {n_sig}, richieste {NREQ[0]}")
        return
    changed = state_fingerprint(state) != old_fp or json.dumps(signals, sort_keys=True, ensure_ascii=False) != old_sig
    due = ts() - state.get("last_write_ts", 0) > CFG["HEARTBEAT_MIN"] * 60
    if changed or due:
        state["last_write"] = rome_str()
        state["last_write_ts"] = int(ts())
        save_json(STATE_F, state)
        save_json(SIGNALS_F, signals)
    log(f"fine: segnali nuovi {n_sig}, richieste HTTP {NREQ[0]}, scritto: {changed or due}")

# ---------------------------------------------------------------- test
def find_recent_finished():
    today = now_utc().astimezone(ROME)
    fallback = None
    for delta in range(0, 6):
        day = today - datetime.timedelta(days=delta)
        payload = get_json(f"{FM}/matches?date={day.strftime('%Y%m%d')}&timezone=Europe%2FRome")
        for m in iter_listed(payload):
            st = m.get("status") or {}
            if not st.get("finished"):
                continue
            ok, _lid, _sport, _why = covered_league(m["_league"]["name"], m["_league"]["ids"], m["_league"]["ccode"])
            if not ok or not teams_ok(m["home"].get("name"), m["away"].get("name")):
                continue
            if is_score_00(st, m["home"].get("score"), m["away"].get("score")):
                return int(m["id"])
            if fallback is None:
                fallback = int(m["id"])
        if fallback is not None and delta >= 1:
            return fallback
    return fallback

def test_mode(mid=None, send=False, use_odds=False):
    if mid is None:
        mid = find_recent_finished()
        if mid is None:
            log("nessuna partita conclusa recente in un campionato coperto")
            return
        log(f"partita conclusa scelta: {mid}")
    md = get_json(f"{FM}/matchDetails?matchId={int(mid)}")
    g = md.get("general") or {}
    status = (md.get("header") or {}).get("status") or {}
    home = (g.get("homeTeam") or {}).get("name")
    away = (g.get("awayTeam") or {}).get("name")
    ids = [g.get("parentLeagueId"), g.get("leagueId")]
    ok, lid, sport, why = covered_league(g.get("leagueName"), ids, g.get("countryCode"), g.get("gender"))
    stats = extract_fh(md)
    pre, confirmed = lookup_prematch(lid, home, away, g.get("matchTimeUTCDate"), mid)
    ht = ht_score(md)
    current_00 = is_score_00(status)
    if status.get("finished"):
        score_00 = ht == (0, 0) if ht is not None else current_00
    else:
        score_00 = current_00
    win, phase = in_window(status)
    log(f"{g.get('countryCode')} | {g.get('leagueName')} | coperto: {ok} {why}")
    log(f"{home} - {away}  punteggio {status.get('scoreStr')}  fase lista: {phase or '-'}  finestra ora: {win}")
    log(f"gol primo tempo (da eventi): {ht}")
    log(f"1T SoT {stats['sot']} poss {stats['poss']} box {stats['box']} xG {stats['xg']} rossi {stats['red']}")
    if pre:
        log(f"prematch {fmt_odd(pre['home'])} / {fmt_odd(pre.get('draw'))} / {fmt_odd(pre['away'])} ({pre.get('source')})")
    else:
        log("prematch n.d." + ("" if confirmed else " (lettura non riuscita)"))
    gate = pre_live_gate(stats, pre)
    log(f"gate: {gate['status']} favorita={gate.get('favourite')} motivi={gate.get('reasons')}")
    live = None
    live_status = "ok"
    if use_odds and gate["status"] != "reject" and ok and score_00:
        state = {"odds_api": {}}
        live, live_status = fetch_live_h2h(sport, home, away, g.get("matchTimeUTCDate"), state)
        log(f"quote live: {live} status={live_status} crediti={ODDS_STATUS}")
    elif gate["status"] != "reject":
        log("quote live non richieste (aggiungi --odds per una chiamata, 1 credito)")
    decision = None
    if use_odds and gate["status"] != "reject":
        decision = assess(stats, pre, live, quota_blocked=(live_status == "quota"), live_status=live_status)
        decision["stats"] = stats
        decision["league_id"] = lid
        log(f"decisione: send={decision['send']} {decision['reasons']}")
    would = ok and score_00 and teams_ok(home, away) and gate["status"] != "reject"
    if not score_00:
        log("condizione 0-0 all'intervallo: NO")
    else:
        log("condizione 0-0 all'intervallo: SI")
    if not ok:
        log(f"non sarebbe un segnale: campionato escluso ({why})")
    elif not teams_ok(home, away):
        log("non sarebbe un segnale: squadra riserve/giovanile/femminile")
    elif not score_00:
        log("non sarebbe un segnale: all'intervallo non era 0-0")
    elif gate["status"] == "reject":
        log("non sarebbe un segnale: " + "; ".join(gate["reasons"]))
    elif not use_odds:
        log("filtri statistici/prematch superati: in produzione qui partira 1 chiamata The Odds API")
    if decision and decision.get("send"):
        info = make_info(md, decision, pre, live, phase or "HT", status)
        if not is_score_00(status):
            info["score"] = "0 - 0"
            info["phase_label"] = "Intervallo"
        msg = build_message(info, test=True)
        log("\n--- messaggio ---\n" + msg)
        if send:
            send_telegram(msg)
    elif would and gate.get("favourite"):
        fake = {"favourite": gate["favourite"], "pre_price": gate.get("pre_price"), "live_price": None,
                "live_status": "ok", "stats": stats, "league_id": lid}
        info = make_info(md, fake, pre, None, "HT", status)
        info["score"] = "0 - 0"
        info["phase_label"] = "Intervallo (ricostruito)"
        info["live_price"] = None
        info["live_quota_nd"] = False
        msg = build_message(info, test=True)
        log("\n--- anteprima (quota live non confermata) ---\n" + msg)
        if send:
            send_telegram(msg)
    log(f"richieste HTTP: {NREQ[0]}")

def main(argv=None):
    ap = argparse.ArgumentParser(description="Scanner favorita all'intervallo sullo 0-0")
    ap.add_argument("--dry-run", action="store_true", help="valuta le live senza Telegram, senza salvare e senza Odds API")
    ap.add_argument("--test", nargs="?", const="auto", default=None,
                    help="valuta un matchId FotMob (senza valore: una conclusa recente). Niente storico.")
    ap.add_argument("--send", action="store_true", help="con --test invia un messaggio Telegram di prova")
    ap.add_argument("--odds", action="store_true", help="con --test legge la quota live (1 credito)")
    args = ap.parse_args(argv)
    t0 = time.time()
    try:
        if args.test is not None:
            mid = None if args.test == "auto" else int(args.test)
            test_mode(mid, send=args.send, use_odds=args.odds)
        else:
            try:
                scan(dry=args.dry_run)
            except Exception as e:
                log(f"ERRORE scansione: {type(e).__name__}: {e}")
    finally:
        log(f"durata {time.time() - t0:.1f}s")

if __name__ == "__main__":
    main()
