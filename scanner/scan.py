#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scanner live (FotMob + quote) → Telegram.

Due giocate, al massimo un messaggio per giocata e per partita, senza follow-up:
  favht     punta la favorita sullo 0-0, dall'intervallo fino a FAV_MAX_MINUTE
  lay_sfav  banca la sfavorita quando è in vantaggio e la favorita domina

Solo libreria standard (Python 3.12).

Uso:
  python scanner/scan.py                 # scansione (workflow)
  python scanner/scan.py --dry-run       # partite live, niente Telegram, niente salvataggio, niente Odds API
  python scanner/scan.py --test          # una partita conclusa recente, senza Telegram
  python scanner/scan.py --test 5749714  # un matchId FotMob
  python scanner/scan.py --test 5749714 --send   # invia un messaggio di prova
  python scanner/scan.py --test 5749714 --odds   # legge anche la quota live (1 credito; 2 se parte la banca)
"""
import argparse, datetime, difflib, html, json, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

# ============================ SOGLIE ============================
# Tutte le soglie della strategia stanno qui.
CFG = {
    # --- favht: punta la favorita sullo 0-0, dall'intervallo fino a FAV_MAX_MINUTE ---
    # SOT_MIN e SOT_MARGIN sono calibrati su STAT_REF_MINUTE e crescono coi minuti giocati,
    # perché i tiri sono un conteggio. Possesso e rapporto dei tocchi in area sono tassi: restano fissi.
    "SOT_MIN": 3,
    "SOT_MARGIN": 2,
    "BOX_RATIO_MIN": 1.6,
    "POSS_MIN": 58,
    "RED_MAX": 0,
    "PRE_MIN": 1.25,
    "PRE_MAX": 1.60,
    # Fascia live back all'intervallo. Dopo il 45', sullo 0-0, il tempo che manca allunga la favorita:
    # ogni minuto aggiunge LIVE_*_PER_MIN. A 60' circa 1,85–2,30, a 70' circa 2,05–2,60.
    # Il massimo sale più del minimo, così la fascia si allarga quando il prezzo è meno preciso.
    "LIVE_MIN": 1.55,
    "LIVE_MAX": 1.85,
    "LIVE_MIN_PER_MIN": 0.02,
    "LIVE_MAX_PER_MIN": 0.03,
    "STAT_REF_MINUTE": 45,
    "FAV_MAX_MINUTE": 70,
    "STAKE_PCT": 1,
    "GREENUP_ODDS": 1.25,
    "EXIT_AFTER_MIN": 10,
    "EXIT_AFTER_MAX": 15,
    "EXIT_HARD_CAP": 80,
    # --- lay_sfav: banca la sfavorita in vantaggio ---
    "LAY_MAX_MINUTE": 70,
    "LAY_PRE_MAX": 1.45,
    "LAY_PRE_TWO_GOAL": 1.25,
    "LAY_SOT_MARGIN": 3,
    "LAY_BOX_RATIO_MIN": 2.0,
    "LAY_POSS_MIN": 60,
    "LAY_RED_MAX": 0,
    "LAY_LIVE_MIN": 1.80,
    "LAY_LIVE_MAX": 3.00,
    "LAY_LIABILITY_PCT": 1,
    "LAY_EXIT_MINUTE": 80,
    # --- comuni ---
    "ODDS_MIN_REMAINING": 60,
    # favht: "alert" invia con «quota live n.d.»; "skip" non invia.
    # lay_sfav non parte mai senza la quota live: il prezzo è la giocata.
    "LOW_QUOTA_ACTION": "alert",
    "ODDS_RECHECK_MIN": 10,
    "PREMATCH_LOOKAHEAD_H": 6,
    "PREMATCH_FETCH_CAP": 12,
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
    keys = (
        "SOT_MIN", "SOT_MARGIN", "BOX_RATIO_MIN", "POSS_MIN", "RED_MAX", "PRE_MIN", "PRE_MAX",
        "LIVE_MIN", "LIVE_MAX", "LIVE_MIN_PER_MIN", "LIVE_MAX_PER_MIN", "STAT_REF_MINUTE",
        "FAV_MAX_MINUTE", "STAKE_PCT", "GREENUP_ODDS", "EXIT_AFTER_MIN", "EXIT_AFTER_MAX", "EXIT_HARD_CAP",
        "LAY_MAX_MINUTE", "LAY_PRE_MAX", "LAY_PRE_TWO_GOAL", "LAY_SOT_MARGIN", "LAY_BOX_RATIO_MIN",
        "LAY_POSS_MIN", "LAY_LIVE_MIN", "LAY_LIVE_MAX", "LAY_LIABILITY_PCT", "LAY_EXIT_MINUTE",
        "ODDS_MIN_REMAINING", "LOW_QUOTA_ACTION", "ODDS_RECHECK_MIN",
    )
    return {k: CFG[k] for k in keys}

# ---------------------------------------------------------------- finestra e filtri
def parse_minute(short):
    s = _INVIS.sub("", str(short or ""))
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None

def in_window(status, max_minute=None, cfg=None):
    """True dall'intervallo fino a max_minute incluso. Partita in corso, niente supplementari."""
    cfg = cfg or CFG
    if max_minute is None:
        max_minute = max(cfg["FAV_MAX_MINUTE"], cfg["LAY_MAX_MINUTE"])
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
    if in_2h and minute is not None and minute <= max_minute:
        return True, "2T"
    return False, ""

def parse_score_pair(status=None, home_score=None, away_score=None):
    if home_score is not None and away_score is not None:
        try:
            return int(home_score), int(away_score)
        except Exception:
            return None
    text = (status or {}).get("scoreStr") if isinstance(status, dict) else None
    m = re.search(r"(\d+)\s*-\s*(\d+)", str(text or ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))

def is_score_00(status=None, home_score=None, away_score=None):
    return parse_score_pair(status, home_score, away_score) == (0, 0)

def played_minute(phase, status=None, cfg=None):
    """Minuto su cui tarare soglie e fascia. All'intervallo si usa il riferimento (45)."""
    cfg = cfg or CFG
    if phase == "HT":
        return int(cfg["STAT_REF_MINUTE"])
    minute = None
    if isinstance(status, dict):
        minute = parse_minute((status.get("liveTime") or {}).get("short"))
    if minute is None:
        return int(cfg["STAT_REF_MINUTE"])
    return int(minute)

def stat_scale(minute, cfg=None):
    cfg = cfg or CFG
    ref = float(cfg["STAT_REF_MINUTE"] or 45)
    return max(float(minute), ref) / ref

def fav_live_band(minute, cfg=None):
    """Fascia back della favorita sullo 0-0. Sale dopo il minuto di riferimento."""
    cfg = cfg or CFG
    extra = max(0.0, float(minute) - float(cfg["STAT_REF_MINUTE"]))
    return (float(cfg["LIVE_MIN"]) + extra * float(cfg["LIVE_MIN_PER_MIN"]),
            float(cfg["LIVE_MAX"]) + extra * float(cfg["LIVE_MAX_PER_MIN"]))

def exit_window(minute, cfg=None):
    """Chiusura temporale: 10–15 minuti dopo l'ingresso, mai oltre EXIT_HARD_CAP."""
    cfg = cfg or CFG
    entry = int(cfg["STAT_REF_MINUTE"] if minute is None else minute)
    start = entry + int(cfg["EXIT_AFTER_MIN"])
    end = entry + int(cfg["EXIT_AFTER_MAX"])
    cap = int(cfg["EXIT_HARD_CAP"])
    end = min(end, cap)
    start = min(start, end)
    return start, end

def one_tick_above(price):
    """Tick Betfair subito sopra il back. Sul bordo (2,00, 3,00, …) vale il passo della fascia successiva."""
    p = round(float(price), 2)
    ladder = ((2, 0.01), (3, 0.02), (4, 0.05), (6, 0.10), (10, 0.20),
              (20, 0.50), (30, 1.0), (50, 2.0), (100, 5.0), (1000, 10.0))
    for limit, step in ladder:
        if abs(p - limit) < 1e-9:
            continue
        if p < limit:
            return round(p + step, 2)
    return round(p + ladder[-1][1], 2)

def lay_stake_pct(lay_odds, liability_pct):
    """Stake del banco, in % del capitale, dato una responsabilità fissa: L / (quota − 1)."""
    odds = float(lay_odds)
    if odds <= 1:
        return None
    return float(liability_pct) / (odds - 1.0)

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

def _limits(minute, profile, cfg):
    """Soglie effettive. I tiri scalano coi minuti; rapporto area e possesso no."""
    k = stat_scale(minute, cfg)
    if profile == "lay":
        margin = cfg["LAY_SOT_MARGIN"] * k
        return {"sot_min": 0.0, "sot_margin": margin, "box": cfg["LAY_BOX_RATIO_MIN"],
                "poss": cfg["LAY_POSS_MIN"], "red": cfg["LAY_RED_MAX"]}
    return {"sot_min": cfg["SOT_MIN"] * k, "sot_margin": cfg["SOT_MARGIN"] * k,
            "box": cfg["BOX_RATIO_MIN"], "poss": cfg["POSS_MIN"], "red": cfg["RED_MAX"]}

def dominates(stats, side, cfg=None, minute=None, profile="fav"):
    """Dominio della squadra `side`. `minute` scala solo i tiri in porta. `profile` è fav o lay."""
    cfg = cfg or CFG
    if minute is None:
        minute = cfg["STAT_REF_MINUTE"]
    lim = _limits(minute, profile, cfg)
    if not stats:
        return False, "statistiche assenti"
    i, j = _side_idx(side), 1 - _side_idx(side)
    sot = stats.get("sot") or [None, None]
    if sot[i] is None or sot[j] is None:
        return False, "tiri in porta assenti"
    if sot[i] + 1e-9 < lim["sot_min"]:
        return False, "tiri in porta sotto soglia"
    if sot[i] + 1e-9 < sot[j] + lim["sot_margin"]:
        return False, "margine tiri in porta insufficiente"
    box = stats.get("box") or [None, None]
    if box[i] is None or box[j] is None:
        return False, "tocchi in area assenti"
    ratio = box_ratio(box[i], box[j])
    if ratio is None or ratio + 1e-9 < lim["box"]:
        return False, "rapporto tocchi in area sotto soglia"
    poss = stats.get("poss") or [None, None]
    if poss[i] is None:
        return False, "possesso assente"
    if poss[i] + 1e-9 < lim["poss"]:
        return False, "possesso sotto soglia"
    red = stats.get("red") or [None, None]
    if red[i] is None:
        return False, "rossi assenti"
    if red[i] > lim["red"]:
        return False, "cartellino rosso"
    return True, ""

def usable_prematch(pre):
    if not pre or pre.get("unavailable"):
        return None
    if pre.get("home") is None or pre.get("away") is None:
        return None
    return pre

def _gate(status, reasons, favourite=None, pre_price=None, passers=None, final=True, **extra):
    out = {"status": status, "reasons": reasons, "favourite": favourite, "pre_price": pre_price,
           "passers": passers or [], "final": final}
    out.update(extra)
    return out

def pre_live_gate(stats, pre, cfg=None, minute=None):
    """Decisione favht prima della chiamata a pagamento.

    status: reject | need_live | need_live_pick
    Un rifiuto sulle statistiche non è definitivo: i numeri crescono fino a FAV_MAX_MINUTE.
    """
    cfg = cfg or CFG
    if minute is None:
        minute = cfg["STAT_REF_MINUTE"]
    if not stats or stats.get("empty"):
        return _gate("reject", ["statistiche assenti"], final=False)
    pre_u = usable_prematch(pre)
    passers, why = [], {}
    for side in ("home", "away"):
        ok, reason = dominates(stats, side, cfg, minute=minute, profile="fav")
        why[side] = reason
        if ok:
            passers.append(side)
    if pre_u:
        fav = favourite_side(pre_u.get("home"), pre_u.get("away"))
        if fav is None:
            return _gate("reject", ["quote prematch pari, nessun favorito"], passers=passers, final=True)
        price = pre_u["home"] if fav == "home" else pre_u["away"]
        if not in_range(price, cfg["PRE_MIN"], cfg["PRE_MAX"]):
            return _gate("reject", [f"prematch {fmt_odd(price)} fuori range {fmt_odd(cfg['PRE_MIN'])}-{fmt_odd(cfg['PRE_MAX'])}"],
                         favourite=fav, pre_price=price, passers=passers, final=True)
        if fav not in passers:
            reason = why.get(fav) or "filtri"
            return _gate("reject", [f"favorita non domina ({reason})"], favourite=fav, pre_price=price,
                         passers=passers, final=str(reason).startswith("cartellino rosso"))
        return _gate("need_live", [], favourite=fav, pre_price=price, passers=passers, final=False)
    if not passers:
        return _gate("reject", [f"casa: {why.get('home')}", f"ospite: {why.get('away')}"], final=False)
    return _gate("need_live_pick", [], passers=passers, final=False)

def assess(stats, pre=None, live=None, quota_blocked=False, live_status="ok", action=None, cfg=None, minute=None):
    """Decisione finale favht. live_status: ok | quota | error | absent.

    La fascia live dipende dal minuto. Fuori fascia non è definitivo: prezzo e fascia si muovono.
    """
    cfg = cfg or CFG
    if minute is None:
        minute = cfg["STAT_REF_MINUTE"]
    action = (action or cfg["LOW_QUOTA_ACTION"] or "alert").lower()
    gate = pre_live_gate(stats, pre, cfg, minute=minute)
    base = {"favourite": gate.get("favourite"), "pre_price": gate.get("pre_price"), "live_price": None,
            "passers": gate.get("passers") or [], "live_status": live_status, "minute": minute}
    if gate["status"] == "reject":
        return {**base, "send": False, "final": gate.get("final", True), "reasons": gate["reasons"]}
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
                    "reasons": ["il favorito live non ha dominato"]}
        base["favourite"] = fav
    live_price = live.get("home") if fav == "home" else live.get("away")
    base["live_price"] = live_price
    lo, hi = fav_live_band(minute, cfg)
    base["live_min"], base["live_max"] = lo, hi
    if not in_range(live_price, lo, hi):
        return {**base, "send": False, "final": False,
                "reasons": [f"quota live {fmt_odd(live_price)} fuori range {fmt_odd(lo)}-{fmt_odd(hi)} al {int(minute)}'"]}
    return {**base, "send": True, "final": True, "reasons": []}

def lay_gate(stats, pre, score, minute=None, cfg=None):
    """Banca della sfavorita, prima della chiamata a pagamento.

    Il prematch è obbligatorio. Un punteggio non adatto non chiude la partita finché si è in finestra:
    il vantaggio può cambiare. Il rosso alla favorita invece chiude.
    """
    cfg = cfg or CFG
    if minute is None:
        minute = cfg["STAT_REF_MINUTE"]
    if score is None:
        return _gate("reject", ["punteggio assente"], final=False)
    pre_u = usable_prematch(pre)
    if not pre_u:
        return _gate("reject", ["prematch assente: la favorita non si identifica"], final=False)
    fav = favourite_side(pre_u.get("home"), pre_u.get("away"))
    if fav is None:
        return _gate("reject", ["quote prematch pari, nessun favorito"], final=True)
    price = pre_u["home"] if fav == "home" else pre_u["away"]
    h, a = int(score[0]), int(score[1])
    lead = (a - h) if fav == "home" else (h - a)
    und = "away" if fav == "home" else "home"
    common = {"favourite": fav, "pre_price": price, "lead": lead, "underdog": und}
    if float(price) > float(cfg["LAY_PRE_MAX"]) + 1e-9:
        return _gate("reject", [f"prematch favorita {fmt_odd(price)} sopra {fmt_odd(cfg['LAY_PRE_MAX'])}"],
                     final=True, **common)
    if lead <= 0:
        return _gate("reject", ["la sfavorita non è in vantaggio"], final=False, **common)
    if lead >= 3:
        return _gate("reject", ["vantaggio della sfavorita di 3 o più gol"], final=False, **common)
    if lead == 2 and float(price) > float(cfg["LAY_PRE_TWO_GOAL"]) + 1e-9:
        return _gate("reject", [f"svantaggio di 2 gol: serve prematch ≤ {fmt_odd(cfg['LAY_PRE_TWO_GOAL'])} (ora {fmt_odd(price)})"],
                     final=False, **common)
    if not stats or stats.get("empty"):
        return _gate("reject", ["statistiche assenti"], final=False, **common)
    ok, reason = dominates(stats, fav, cfg, minute=minute, profile="lay")
    if not ok:
        return _gate("reject", [f"favorita non domina ({reason})"], final=str(reason).startswith("cartellino rosso"), **common)
    return _gate("need_live", [], final=False, **common)

def assess_lay(stats, pre, score, live=None, quota_blocked=False, live_status="ok", minute=None, cfg=None):
    """Decisione lay_sfav. Senza quota live non si invia: il prezzo della sfavorita è la giocata."""
    cfg = cfg or CFG
    if minute is None:
        minute = cfg["STAT_REF_MINUTE"]
    gate = lay_gate(stats, pre, score, minute=minute, cfg=cfg)
    base = {"favourite": gate.get("favourite"), "underdog": gate.get("underdog"), "pre_price": gate.get("pre_price"),
            "lead": gate.get("lead"), "live_price": None, "lay_price": None, "lay_estimated": False,
            "live_status": live_status, "minute": minute}
    if gate["status"] == "reject":
        return {**base, "send": False, "final": gate.get("final", True), "reasons": gate["reasons"]}
    if quota_blocked or live_status == "quota":
        return {**base, "send": False, "final": False, "live_status": "quota",
                "reasons": ["crediti insufficienti: banca non inviata senza quota live"]}
    if live_status == "error" or (live is None and live_status != "absent"):
        return {**base, "send": False, "final": False, "live_status": "error", "reasons": ["errore quote live, riprovo"]}
    if live_status == "absent" or not live or live.get("home") is None or live.get("away") is None:
        return {**base, "send": False, "final": True, "live_status": "absent", "reasons": ["quota live assente"]}
    und = gate["underdog"]
    back = live.get("home") if und == "home" else live.get("away")
    base["live_price"] = back
    if not in_range(back, cfg["LAY_LIVE_MIN"], cfg["LAY_LIVE_MAX"]):
        return {**base, "send": False, "final": False,
                "reasons": [f"back sfavorita {fmt_odd(back)} fuori range {fmt_odd(cfg['LAY_LIVE_MIN'])}-{fmt_odd(cfg['LAY_LIVE_MAX'])}"]}
    lay = live.get("lay_home") if und == "home" else live.get("lay_away")
    if lay is None:
        lay = one_tick_above(back)
        base["lay_estimated"] = True
    base["lay_price"] = lay
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

def parse_h2h_book(book, home, away, market="h2h"):
    for mk in book.get("markets") or []:
        if mk.get("key") != market:
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
        prices = parse_h2h_book(book, home, away, market="h2h")
        if not prices:
            continue
        prices["book_key"] = key
        prices["book_label"] = "Betfair" if key == ODDS_BOOK_PREFERRED else "book europeo"
        lay = parse_h2h_book(book, home, away, market="h2h_lay")
        if lay:
            prices["lay_home"] = lay["home"]
            prices["lay_draw"] = lay["draw"]
            prices["lay_away"] = lay["away"]
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
    parsed["event_id"] = ev.get("id")
    parsed["sport"] = sport
    return parsed, "ok"

def fetch_lay_prices(live, home, away, state):
    """Seconda chiamata, solo h2h_lay, e solo se il back è già nel range. Costa 1 credito in più."""
    if not live or not live.get("event_id") or not live.get("sport") or quota_low(state):
        return live
    if live.get("lay_home") is not None and live.get("lay_away") is not None:
        return live
    try:
        data = odds_get(f"/sports/{live['sport']}/events/{live['event_id']}/odds",
                        {"regions": "eu", "markets": "h2h_lay", "oddsFormat": "decimal"})
    except Exception as e:
        log(f"   lay Odds API: {e}")
        return live
    cost = str(ODDS_STATUS.get("last_cost") or "")
    if cost and cost not in ("0", "1"):
        log(f"   attenzione: chiamata h2h_lay ha costo {cost}")
    books = {b.get("key"): b for b in (data.get("bookmakers") or [])}
    book = books.get(live.get("book_key"))
    if not book:
        return live
    prices = parse_h2h_book(book, home, away, market="h2h_lay")
    if not prices:
        return live
    live["lay_home"] = prices["home"]
    live["lay_draw"] = prices["draw"]
    live["lay_away"] = prices["away"]
    return live

# ---------------------------------------------------------------- statistiche FotMob
def extract_period(md, period):
    periods = (((md.get("content") or {}).get("stats") or {}).get("Periods") or {})
    block = periods.get(period) or {}
    raw = {}
    for grp in block.get("stats") or []:
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
    return {"sot": sot, "poss": poss, "box": box, "xg": xg, "red": red, "empty": empty, "period": period}

def extract_fh(md):
    return extract_period(md, "FirstHalf")

def stats_for_phase(md, phase):
    """All'intervallo il primo tempo. Dopo, il totale della partita fino al minuto corrente."""
    if phase == "HT":
        return extract_period(md, "FirstHalf"), "ht"
    got = extract_period(md, "All")
    if got.get("empty"):
        return extract_period(md, "FirstHalf"), "ht"
    return got, "match"

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

def _scope(info):
    return info.get("stats_scope") or "ht"

def _phase_of(info):
    if info.get("phase"):
        return info["phase"]
    if str(info.get("phase_label") or "").startswith("Intervallo"):
        return "HT"
    return "2T"

def _entry(info, cfg):
    if info.get("entry_minute") is not None:
        return int(info["entry_minute"])
    if _phase_of(info) == "HT":
        return int(cfg["STAT_REF_MINUTE"])
    if info.get("minute") is not None:
        return int(info["minute"])
    return int(cfg["STAT_REF_MINUTE"])

def alternative_text(stats, side, minute=None, cfg=None):
    cfg = cfg or CFG
    i = _side_idx(side)
    sot = stats.get("sot") or [None, None]
    xg = (stats.get("xg") or [None, None])[i]
    margin = None
    if sot[i] is not None and sot[1 - i] is not None:
        margin = sot[i] - sot[1 - i]
    ratio = _ratio_text(stats, side)
    late = minute is not None and int(minute) >= int(cfg["STAT_REF_MINUTE"]) + 15
    if late:
        return ("Prossimo gol della favorita, oppure Over 0,5 da questo minuto (basta un gol). "
                "Over 1,5 non è coerente con un ingresso così avanti.")
    when = "finora" if minute is not None and int(minute) > int(cfg["STAT_REF_MINUTE"]) else "nel primo tempo"
    if xg is not None and xg >= 1.0 and margin is not None and margin >= 3:
        core = (f"Over 1,5 solo se la favorita segna presto (xG {when} {fmt_stat(xg)}): "
                "è un'idea separata, non questa giocata.")
    elif ratio is not None and ratio >= 2:
        core = f"Over 0,5 secondo tempo, oppure il prossimo gol della favorita: il dominio territoriale {when} è netto."
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
    when = "nel primo tempo" if _scope(info) == "ht" else "finora"
    bits = []
    if info.get("prematch_price") is None:
        bits.append(f"{fav} è la favorita individuata senza quota prematch (prematch n.d.)")
    else:
        bits.append(f"{fav} era favorita in prematch a {fmt_odd(info['prematch_price'])}")
    if info.get("live_quota_nd"):
        bits.append("sullo 0-0 la quota live non è stata letta (quota live n.d.)")
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
        f"{when} {sot[i]} tiri in porta contro {sot[j]} di {opp}, "
        f"possesso {fmt_stat(poss[i])}%, tocchi in area avversaria {fmt_stat(box[i])}-{fmt_stat(box[j])} ({ratio_txt})"
    )
    if xg[i] is not None and xg[j] is not None:
        bits.append(f"xG {when} {fmt_stat(xg[i])}-{fmt_stat(xg[j])}")
    bits.append("nessun cartellino rosso alla favorita")
    return ". ".join(bits) + "."

def _exit_line(info, cfg):
    start, end = exit_window(_entry(info, cfg), cfg)
    cap = int(cfg["EXIT_HARD_CAP"])
    where = "Sei entrato all'intervallo" if _phase_of(info) == "HT" else f"Sei entrato al {int(_entry(info, cfg))}'"
    if start == end:
        clock = f"all'{start}' se è ancora 0-0, chiudi la posizione (perdita parziale)."
    else:
        clock = f"se tra il {start}' e il {end}' è ancora 0-0, chiudi la posizione (perdita parziale)."
    tail = "" if end >= cap else f" In ogni caso non restare oltre l'{cap}'."
    return f"• {where}: {clock}{tail}"

def _stats_block(st, scope):
    title = "Primo tempo" if scope == "ht" else "Fino a questo minuto"
    lines = [
        f"📡 <b>{title}</b> (casa - ospite)",
        f"   Tiri in porta: {fmt_stat(st['sot'][0])} - {fmt_stat(st['sot'][1])}",
        f"   Possesso: {fmt_stat(st['poss'][0])}% - {fmt_stat(st['poss'][1])}%",
        f"   Tocchi in area avversaria: {fmt_stat(st['box'][0])} - {fmt_stat(st['box'][1])}",
    ]
    xg = st.get("xg") or [None, None]
    if xg[0] is not None or xg[1] is not None:
        lines.append(f"   xG: {fmt_stat(xg[0])} - {fmt_stat(xg[1])}")
    lines.append(f"   Rossi: {fmt_stat(st['red'][0])} - {fmt_stat(st['red'][1])}")
    return lines

def _fotmob_link(info):
    if not info.get("match_id"):
        return []
    return ["", f'<a href="https://www.fotmob.com/match/{int(info["match_id"])}">Apri su FotMob</a>']

def _fmt_pct(v):
    if v is None:
        return "n.d."
    if abs(float(v) - round(float(v))) < 1e-9:
        return str(int(round(float(v))))
    return f"{float(v):.2f}".replace(".", ",")

def build_message(info, test=False, cfg=None):
    cfg = cfg or CFG
    minute = _entry(info, cfg)
    lo, hi = fav_live_band(minute, cfg)
    scope = _scope(info)
    L = []
    if test:
        L.append("🧪 <b>TEST</b> — messaggio di prova, non è un segnale operativo")
    L.append("⚽ <b>FAVORITA 0-0 LIVE</b>")
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
    L.append("")
    L.extend(_stats_block(info["stats"], scope))
    L.append("")
    L.append("🧠 <b>PERCHÉ la giocata</b>")
    L.append(esc(why_text(info, cfg)))
    L.append("")
    L.append("🎯 <b>COSA giocare</b>")
    L.append(f"Punta la favorita <b>{esc(info['favourite_team'])}</b>, Esito Finale / Match Odds (1X2).")
    band = (f"Quota minima consigliata: <b>{fmt_odd(lo)}</b>, e non entrare sopra {fmt_odd(hi)} "
            f"(fascia a questo minuto; all'intervallo è {fmt_odd(cfg['LIVE_MIN'])}-{fmt_odd(cfg['LIVE_MAX'])}, "
            "poi sale perché sullo 0-0 ogni minuto senza gol allunga la favorita).")
    if info.get("live_price") is None or info.get("live_quota_nd"):
        band += " Verifica la quota live prima di puntare: qui non è stata confermata."
    L.append(band)
    L.append(f"Stake: <b>{int(cfg['STAKE_PCT'])}% del capitale</b>. Una sola entrata, niente raddoppi.")
    L.append("")
    L.append("🚪 <b>COME e QUANDO uscire</b>")
    L.append(f"• Green up quando, dopo il gol della favorita, la quota scende a circa {fmt_odd(cfg['GREENUP_ODDS'])}.")
    L.append("• Stop loss se segna l'avversaria: chiudi subito.")
    L.append(_exit_line(info, cfg))
    L.append("• Esci prima se la favorita prende un rosso o se cala in modo netto la pressione.")
    L.append("")
    L.append("🔄 <b>GIOCATA ALTERNATIVA (facoltativa)</b>")
    alt_minute = None if _phase_of(info) == "HT" else minute
    L.append(esc(alternative_text(info["stats"], info["favourite_side"], minute=alt_minute, cfg=cfg)))
    L.append("È facoltativa e non va sommata allo stake. Nessun altro messaggio su questa partita.")
    L.extend(_fotmob_link(info))
    return "\n".join(L)

def _over_line(score_text):
    pair = parse_score_pair({"scoreStr": score_text}) if score_text else None
    if not pair:
        return "i gol attuali + 0,5"
    return f"{pair[0] + pair[1]},5"

def why_lay_text(info):
    fav, und = info["favourite_team"], info["underdog_team"]
    stats = info["stats"]
    i = _side_idx(info["favourite_side"])
    j = 1 - i
    sot, poss = stats["sot"], stats["poss"]
    ratio = _ratio_text(stats, info["favourite_side"])
    ratio_txt = "dominio pieno in area" if ratio == float("inf") else f"rapporto tocchi in area {fmt_stat(ratio)}"
    when = "nel primo tempo" if _scope(info) == "ht" else "finora"
    lead = int(info.get("lead") or 1)
    bits = [
        f"{fav} era favorita in prematch a {fmt_odd(info.get('prematch_price'))}",
        f"{und} conduce di {lead} gol ma {fav} domina {when}: "
        f"{fmt_stat(sot[i])}-{fmt_stat(sot[j])} tiri in porta, possesso {fmt_stat(poss[i])}%, {ratio_txt}",
        "nessun cartellino rosso alla favorita",
    ]
    if info.get("live_price") is not None:
        bits.append(f"la sfavorita è bancabile: back visto a {fmt_odd(info['live_price'])}")
    return ". ".join(bits) + "."

def build_lay_message(info, test=False, cfg=None):
    cfg = cfg or CFG
    lay = info.get("lay_price")
    liability = float(cfg["LAY_LIABILITY_PCT"])
    stake = lay_stake_pct(lay, liability) if lay else None
    lead = int(info.get("lead") or 1)
    L = []
    if test:
        L.append("🧪 <b>TEST</b> — messaggio di prova, non è un segnale operativo")
    L.append("🏦 <b>BANCA LA SFAVORITA</b>")
    L.append(f"🏆 {esc(info.get('league'))} ({esc(info.get('country'))})")
    L.append(f"⚽ <b>{esc(info['home'])} - {esc(info['away'])}</b>")
    L.append(f"⏱ {esc(info.get('phase_label') or '?')}   📊 {esc(info.get('score') or '?')}")
    L.append("")
    src = info.get("prematch_source") or "prematch"
    L.append(f"💰 Prematch {esc(info['favourite_team'])}: <b>{fmt_odd(info.get('prematch_price'))}</b> ({esc(src)})")
    if info.get("prematch_home") is not None:
        L.append("   1X2 prematch: "
                 + " - ".join(fmt_odd(info.get(k)) for k in ("prematch_home", "prematch_draw", "prematch_away")))
    book = info.get("live_book") or "live"
    L.append(f"💰 Back {esc(info['underdog_team'])} ({esc(book)}): <b>{fmt_odd(info.get('live_price'))}</b>")
    if info.get("lay_estimated"):
        L.append(f"💰 Lay: non è nel feed. Di solito è circa un tick sopra il back, quindi <b>{fmt_odd(lay)}</b>.")
    else:
        L.append(f"💰 Lay {esc(info['underdog_team'])}: <b>{fmt_odd(lay)}</b>")
    L.append("")
    L.extend(_stats_block(info["stats"], _scope(info)))
    L.append("")
    L.append("🧠 <b>PERCHÉ la giocata</b>")
    L.append(esc(why_lay_text(info)))
    L.append("")
    L.append("🎯 <b>COSA giocare</b>")
    L.append(f"BANCA la sfavorita <b>{esc(info['underdog_team'])}</b>, Esito Finale / Match Odds.")
    L.append(f"Responsabilità fissa: <b>{_fmt_pct(liability)}% del capitale</b>. "
             "Non si perde di più di questa cifra se la sfavorita vince la partita.")
    L.append("Stake = responsabilità / (quota lay − 1).")
    if lay is not None and stake is not None:
        L.append(f"Con quota lay {fmt_odd(lay)}: {_fmt_pct(liability)} / ({fmt_odd(lay)} − 1) = "
                 f"<b>{_fmt_pct(stake)}% del capitale</b>.")
    L.append(f"Range back accettato: {fmt_odd(cfg['LAY_LIVE_MIN'])}-{fmt_odd(cfg['LAY_LIVE_MAX'])}.")
    L.append("")
    L.append("🚪 <b>COME e QUANDO uscire</b>")
    if lead >= 2:
        L.append("• Green up quando la favorita segna e dimezza il gap (per esempio dal 2-0 al 2-1): "
                 "la quota della sfavorita si allunga e si chiude in profitto.")
    else:
        L.append("• Green up al pareggio, quando la favorita segna e annulla il vantaggio: "
                 "la quota della sfavorita si allunga.")
    L.append("• Stop se la sfavorita segna ancora: la responsabilità è fissa, non inseguire.")
    L.append(f"• Se all'{int(cfg['LAY_EXIT_MINUTE'])}' il gol della favorita non è arrivato, chiudi "
             "(anche in perdita parziale).")
    L.append("")
    L.append("🔄 <b>GIOCATA ALTERNATIVA (facoltativa)</b>")
    line = _over_line(info.get("score"))
    L.append(esc(
        f"Over {line} (i gol attuali più 0,5), oppure punta la favorita {info['favourite_team']} "
        "in Esito Finale o il pareggio / DNB."
    ))
    L.append("È facoltativa e non va sommata allo stake. Nessun altro messaggio su questa partita.")
    L.extend(_fotmob_link(info))
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

def _stats_payload(stats, side):
    ratio = _ratio_text(stats, side)
    return {
        "sot": stats["sot"], "poss": stats["poss"], "box": stats["box"], "xg": stats.get("xg"),
        "red": stats["red"], "box_ratio": None if ratio == float("inf") else ratio,
        "favourite_index": _side_idx(side), "scope": stats.get("scope"),
    }

def _common_record(info, strategy):
    minute = info.get("entry_minute") if info.get("entry_minute") is not None else info.get("minute")
    return {
        "id": f"{info['match_id']}-{strategy}",
        "strategy": strategy,
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
        "minute": minute,
        "score": info.get("score"),
        "url": f"https://www.fotmob.com/match/{int(info['match_id'])}",
    }

def signal_record(info):
    strategy = info.get("strategy") or "favht"
    if strategy == "lay_sfav":
        rec = _common_record(info, "lay_sfav")
        rec["underdog"] = info.get("underdog_side")
        rec["underdogTeam"] = info.get("underdog_team")
        rec["lead"] = info.get("lead")
        rec["odds"] = {
            "prematch": {
                "home": info.get("prematch_home"), "draw": info.get("prematch_draw"), "away": info.get("prematch_away"),
                "favourite": info.get("prematch_price"), "source": info.get("prematch_source"),
            },
            "live": {
                "home": info.get("live_home"), "draw": info.get("live_draw"), "away": info.get("live_away"),
                "underdog": info.get("live_price"), "book": info.get("live_book"),
            },
            "lay": None if info.get("lay_price") is None else {
                "price": info.get("lay_price"), "estimated": bool(info.get("lay_estimated")),
            },
            "liability_pct": info.get("liability_pct"),
            "stake_pct": info.get("stake_pct"),
        }
        rec["stats"] = _stats_payload(info["stats"], info["favourite_side"])
        return rec
    rec = _common_record(info, "favht")
    live = None
    if not info.get("live_quota_nd") and info.get("live_price") is not None:
        live = {
            "home": info.get("live_home"), "draw": info.get("live_draw"), "away": info.get("live_away"),
            "favourite": info.get("live_price"), "book": info.get("live_book"),
        }
        if info.get("live_min") is not None:
            live["min"] = info.get("live_min")
            live["max"] = info.get("live_max")
    rec["odds"] = {
        "prematch": None if info.get("prematch_price") is None else {
            "home": info.get("prematch_home"), "draw": info.get("prematch_draw"), "away": info.get("prematch_away"),
            "favourite": info.get("prematch_price"), "source": info.get("prematch_source"),
        },
        "live": live,
        "live_note": "quota live n.d." if info.get("live_quota_nd") else None,
    }
    rec["stats"] = _stats_payload(info["stats"], info["favourite_side"])
    return rec

def _base_info(md, decision, pre, live, phase, status, stats, scope):
    g = md.get("general") or {}
    home = (g.get("homeTeam") or {}).get("name") or ""
    away = (g.get("awayTeam") or {}).get("name") or ""
    fav = decision.get("favourite") or "home"
    pre_u = usable_prematch(pre)
    stats = dict(stats or {})
    stats["scope"] = scope
    return {
        "match_id": int(g.get("matchId") or 0),
        "home": home, "away": away,
        "league": g.get("leagueName") or "",
        "country": g.get("countryCode") or "",
        "league_id": decision.get("league_id"),
        "phase": phase,
        "phase_label": phase_label(status, phase),
        "minute": parse_minute((status.get("liveTime") or {}).get("short")) if isinstance(status, dict) else None,
        "entry_minute": played_minute(phase, status if isinstance(status, dict) else None),
        "score": (status or {}).get("scoreStr") or "0 - 0" if isinstance(status, dict) else "0 - 0",
        "favourite_side": fav,
        "favourite_team": home if fav == "home" else away,
        "opponent": away if fav == "home" else home,
        "stats_scope": scope,
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

def make_info(md, decision, pre, live, phase, status, stats=None, scope="ht"):
    info = _base_info(md, decision, pre, live, phase, status, stats or decision.get("stats") or extract_fh(md), scope)
    info["strategy"] = "favht"
    info["live_min"] = decision.get("live_min")
    info["live_max"] = decision.get("live_max")
    return info

def make_lay_info(md, decision, pre, live, phase, status, stats, scope, score):
    info = _base_info(md, decision, pre, live, phase, status, stats, scope)
    info["strategy"] = "lay_sfav"
    und = decision.get("underdog") or ("away" if info["favourite_side"] == "home" else "home")
    info["underdog_side"] = und
    info["underdog_team"] = info["home"] if und == "home" else info["away"]
    info["lead"] = decision.get("lead")
    info["lay_price"] = decision.get("lay_price")
    info["lay_estimated"] = bool(decision.get("lay_estimated"))
    info["liability_pct"] = CFG["LAY_LIABILITY_PCT"]
    info["stake_pct"] = None if decision.get("lay_price") is None else lay_stake_pct(decision["lay_price"], CFG["LAY_LIABILITY_PCT"])
    if score:
        info["score"] = f"{int(score[0])} - {int(score[1])}"
    return info

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

def strategy_key(mid, strategy):
    return f"{int(mid)}:{strategy}"

def normalize_state_keys(state):
    """Le chiavi vecchie erano solo l'id partita e valevano per favht."""
    for bucket in ("sent", "done"):
        data = state.setdefault(bucket, {})
        for k in list(data):
            if ":" not in str(k) and str(k).isdigit():
                data.setdefault(f"{k}:favht", data[k])
                del data[k]

def strategy_closed(state, mid, strategy):
    key = strategy_key(mid, strategy)
    return key in state.get("sent", {}) or key in state.get("done", {})

def listed_score(m):
    return parse_score_pair(m.get("status"), (m.get("home") or {}).get("score"), (m.get("away") or {}).get("score"))

def list_candidates(matches, state):
    cands = []
    for mid, m in matches.items():
        st = m.get("status") or {}
        if not teams_ok((m.get("home") or {}).get("name"), (m.get("away") or {}).get("name")):
            continue
        ok, _lid, _sport, _why = covered_league(m["_league"]["name"], m["_league"]["ids"], m["_league"]["ccode"])
        if not ok:
            continue
        score = listed_score(m)
        fav_open = in_window(st, CFG["FAV_MAX_MINUTE"])[0] and score == (0, 0) and not strategy_closed(state, mid, "favht")
        lay_open = (in_window(st, CFG["LAY_MAX_MINUTE"])[0] and score is not None and score[0] != score[1]
                    and not strategy_closed(state, mid, "lay_sfav"))
        if fav_open or lay_open:
            cands.append(m)
    cands.sort(key=lambda m: (m["_league"]["name"], (m.get("home") or {}).get("name") or ""))
    return cands

def mark_done(state, mid, strategy, reason):
    state["done"][strategy_key(mid, strategy)] = {"t": int(ts()), "r": reason[:180], "strategy": strategy}
    log(f"   chiusa {strategy}: {reason}")

def odds_too_soon(state, mid, strategy, minute):
    rec = (state.get("odds_check") or {}).get(strategy_key(mid, strategy))
    if not isinstance(rec, dict) or rec.get("minute") is None:
        return False
    return float(minute) - float(rec["minute"]) < float(CFG["ODDS_RECHECK_MIN"]) - 1e-9

def note_odds_check(state, mid, strategy, minute, price):
    state.setdefault("odds_check", {})[strategy_key(mid, strategy)] = {
        "t": int(ts()), "minute": int(minute), "price": price,
    }

def reseed_sent(state, signals):
    state.setdefault("sent", {})
    state.setdefault("done", {})
    state.setdefault("prematch", {})
    state.setdefault("odds_check", {})
    normalize_state_keys(state)
    for s in signals or []:
        mid = s.get("matchId")
        if not mid:
            continue
        strategy = s.get("strategy") or "favht"
        state["sent"].setdefault(strategy_key(mid, strategy), {
            "t": s.get("ts") or 0, "time": s.get("time"), "strategy": strategy,
        })

def _load_prematch(state, mid, lid, home, away, ko):
    cached = state["prematch"].get(str(mid))
    pre = usable_prematch(cached)
    if pre is not None:
        log(f"   prematch in cache {fmt_odd(pre['home'])} / {fmt_odd(pre.get('draw'))} / {fmt_odd(pre['away'])} ({pre.get('source')})")
        return pre
    if (cached or {}).get("unavailable"):
        log("   prematch n.d. (già cercato)")
        return None
    parsed, confirmed = lookup_prematch(lid, home, away, ko, mid)
    if parsed:
        parsed["ts"] = int(ts())
        parsed["league_id"] = lid
        state["prematch"][str(mid)] = parsed
        log(f"   prematch {fmt_odd(parsed['home'])} / {fmt_odd(parsed.get('draw'))} / {fmt_odd(parsed['away'])} ({parsed['source']})")
        return parsed
    if confirmed:
        state["prematch"][str(mid)] = {"unavailable": True, "ts": int(ts()), "source": "none"}
        log("   prematch n.d.")
    else:
        log("   prematch non letto (rete), riprovo al giro dopo")
    return None

def _window_open(state, mid, strategy, status, max_minute):
    win, phase = in_window(status, max_minute)
    if win:
        return True, phase
    minute = parse_minute((status.get("liveTime") or {}).get("short")) or 0
    if status.get("finished") or minute > max_minute:
        mark_done(state, mid, strategy, "fuori finestra")
    else:
        log(f"   {strategy}: fuori finestra, riprovo")
    return False, ""

def _publish(state, signals, info, msg):
    if not send_telegram(msg):
        log("   Telegram non inviato, non segno la partita")
        return False
    strategy = info.get("strategy") or "favht"
    state["sent"][strategy_key(info["match_id"], strategy)] = {
        "t": int(ts()), "time": rome_str(), "strategy": strategy,
    }
    signals.append(signal_record(info))
    save_json(STATE_F, state)
    save_json(SIGNALS_F, signals)
    return True

def _finish_reject(state, mid, strategy, minute, decision):
    log(f"   {strategy}: send={decision['send']} {'; '.join(decision['reasons'])}")
    if decision.get("live_status") == "ok" and not decision.get("final"):
        note_odds_check(state, mid, strategy, minute, decision.get("live_price"))
    if decision.get("final"):
        mark_done(state, mid, strategy, "; ".join(decision["reasons"]) or "scartata")

def _consider_fav(md, state, signals, dry, mid, home, away, status, pre, lid, sport, ko):
    if strategy_closed(state, mid, "favht"):
        return False
    win, phase = _window_open(state, mid, "favht", status, CFG["FAV_MAX_MINUTE"])
    if not win:
        return False
    if parse_score_pair(status) != (0, 0):
        mark_done(state, mid, "favht", "non è 0-0")
        return False
    minute = played_minute(phase, status)
    stats, scope = stats_for_phase(md, phase)
    label = "1T" if scope == "ht" else f"fino al {minute}'"
    log(f"   favht {label} SoT {stats['sot']} poss {stats['poss']} box {stats['box']} xG {stats['xg']} rossi {stats['red']}")
    gate = pre_live_gate(stats, pre, minute=minute)
    if gate["status"] == "reject":
        _finish_reject(state, mid, "favht", minute, {
            "send": False, "final": gate.get("final", True), "reasons": gate["reasons"],
            "live_status": "pre", "live_price": None,
        })
        return False
    log(f"   favht gate {gate['status']} favorita={gate.get('favourite') or 'da quote live'} minuto={minute}")
    if odds_too_soon(state, mid, "favht", minute):
        log("   favht: quota live già letta, riprovo più avanti")
        return False
    if dry:
        log("   favht dry-run: filtri ok, niente Odds API e niente Telegram")
        return False
    if not os.environ.get("ODDS_API_KEY"):
        log("   ODDS_API_KEY assente, riprovo")
        return False
    live, live_status = fetch_live_h2h(sport, home, away, ko, state)
    if ODDS_STATUS:
        state["odds_api"] = dict(ODDS_STATUS)
    decision = assess(stats, pre, live, quota_blocked=(live_status == "quota"), live_status=live_status, minute=minute)
    decision["stats"] = stats
    decision["league_id"] = lid
    if not decision["send"]:
        _finish_reject(state, mid, "favht", minute, decision)
        return False
    info = make_info(md, decision, pre, live, phase, status, stats=stats, scope=scope)
    log(f"   favht invio {info['favourite_team']} live {fmt_odd(info.get('live_price'))}")
    return _publish(state, signals, info, build_message(info))

def _consider_lay(md, state, signals, dry, mid, home, away, status, pre, lid, sport, ko):
    if strategy_closed(state, mid, "lay_sfav"):
        return False
    win, phase = _window_open(state, mid, "lay_sfav", status, CFG["LAY_MAX_MINUTE"])
    if not win:
        return False
    score = parse_score_pair(status)
    if score is None or score[0] == score[1]:
        log("   lay_sfav: punteggio in parità, la banca non si applica")
        return False
    minute = played_minute(phase, status)
    stats, scope = stats_for_phase(md, phase)
    label = "1T" if scope == "ht" else f"fino al {minute}'"
    log(f"   lay_sfav {label} {score[0]}-{score[1]} SoT {stats['sot']} poss {stats['poss']} box {stats['box']} rossi {stats['red']}")
    gate = lay_gate(stats, pre, score, minute=minute)
    if gate["status"] == "reject":
        _finish_reject(state, mid, "lay_sfav", minute, {
            "send": False, "final": gate.get("final", True), "reasons": gate["reasons"],
            "live_status": "pre", "live_price": None,
        })
        return False
    log(f"   lay_sfav gate need_live sfavorita={gate.get('underdog')} vantaggio={gate.get('lead')} minuto={minute}")
    if odds_too_soon(state, mid, "lay_sfav", minute):
        log("   lay_sfav: quota live già letta, riprovo più avanti")
        return False
    if dry:
        log("   lay_sfav dry-run: filtri ok, niente Odds API e niente Telegram")
        return False
    if not os.environ.get("ODDS_API_KEY"):
        log("   ODDS_API_KEY assente, riprovo")
        return False
    live, live_status = fetch_live_h2h(sport, home, away, ko, state)
    if ODDS_STATUS:
        state["odds_api"] = dict(ODDS_STATUS)
    decision = assess_lay(stats, pre, score, live, quota_blocked=(live_status == "quota"),
                          live_status=live_status, minute=minute)
    if decision.get("send") and decision.get("lay_estimated"):
        fetch_lay_prices(live, home, away, state)
        if ODDS_STATUS:
            state["odds_api"] = dict(ODDS_STATUS)
        decision = assess_lay(stats, pre, score, live, live_status="ok", minute=minute)
    decision["league_id"] = lid
    if not decision["send"]:
        _finish_reject(state, mid, "lay_sfav", minute, decision)
        return False
    info = make_lay_info(md, decision, pre, live, phase, status, stats, scope, score)
    note = " stimata" if info.get("lay_estimated") else ""
    log(f"   lay_sfav invio banca {info['underdog_team']} lay {fmt_odd(info.get('lay_price'))}{note}")
    return _publish(state, signals, info, build_lay_message(info))

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
    if not ok or not teams_ok((g.get("homeTeam") or {}).get("name"), (g.get("awayTeam") or {}).get("name")):
        for strategy in ("favht", "lay_sfav"):
            if not strategy_closed(state, mid, strategy):
                mark_done(state, mid, strategy, why or "squadre escluse")
        return False
    ko = g.get("matchTimeUTCDate") or (m.get("status") or {}).get("utcTime")
    pre = _load_prematch(state, mid, lid, home, away, ko)
    sent = _consider_fav(md, state, signals, dry, mid, home, away, status, pre, lid, sport, ko)
    if _consider_lay(md, state, signals, dry, mid, home, away, status, pre, lid, sport, ko):
        sent = True
    return bool(sent)

def prune_state(state):
    now = ts()
    for key, ttl in (("done", 2 * 86400), ("prematch", 3 * 86400), ("odds_check", 2 * 86400)):
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
    hi = max(CFG["FAV_MAX_MINUTE"], CFG["LAY_MAX_MINUTE"])
    log(f"candidate in finestra HT–{hi}': {len(cands)}")
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
    teams = teams_ok(home, away)
    pre, confirmed = lookup_prematch(lid, home, away, g.get("matchTimeUTCDate"), mid)
    phase, minute, stats, scope, score, historic, ht = _eval_snapshot(md, status)
    win_now = in_window(status, max(CFG["FAV_MAX_MINUTE"], CFG["LAY_MAX_MINUTE"]))[0]
    log(f"{g.get('countryCode')} | {g.get('leagueName')} | coperto: {ok} {why}")
    log(f"{home} - {away}  punteggio {status.get('scoreStr')}  finestra ora: {win_now}")
    if historic:
        log("valutazione sulla foto dell'intervallo (la partita non è in finestra adesso)")
    log(f"gol primo tempo (da eventi): {ht}  punteggio usato: {score}  minuto soglie: {minute}")
    log(f"stats {scope} SoT {stats['sot']} poss {stats['poss']} box {stats['box']} xG {stats['xg']} rossi {stats['red']}")
    if pre:
        log(f"prematch {fmt_odd(pre['home'])} / {fmt_odd(pre.get('draw'))} / {fmt_odd(pre['away'])} ({pre.get('source')})")
    else:
        log("prematch n.d." + ("" if confirmed else " (lettura non riuscita)"))
    fav_gate = pre_live_gate(stats, pre, minute=minute)
    lay_g = lay_gate(stats, pre, score, minute=minute)
    log(f"favht: {fav_gate['status']} favorita={fav_gate.get('favourite')} motivi={fav_gate.get('reasons')}")
    log(f"lay_sfav: {lay_g['status']} favorita={lay_g.get('favourite')} vantaggio={lay_g.get('lead')} motivi={lay_g.get('reasons')}")
    log("0-0: " + ("SI" if score == (0, 0) else "NO"))
    if not ok:
        log(f"non sarebbe un segnale: campionato escluso ({why})")
    elif not teams:
        log("non sarebbe un segnale: squadra riserve/giovanile/femminile")
    state = {"odds_api": {}}
    ko = g.get("matchTimeUTCDate")
    if ok and teams and use_odds and fav_gate["status"] != "reject" and score == (0, 0):
        live, live_status = fetch_live_h2h(sport, home, away, ko, state)
        log(f"favht quote: status={live_status} crediti={ODDS_STATUS}")
        decision = assess(stats, pre, live, quota_blocked=(live_status == "quota"), live_status=live_status, minute=minute)
        decision["league_id"] = lid
        log(f"favht decisione: send={decision['send']} {decision['reasons']}")
        if decision.get("send"):
            info = make_info(md, decision, pre, live, phase, status, stats=stats, scope=scope)
            if historic:
                info["score"] = "0 - 0"
                info["phase"] = "HT"
                info["phase_label"] = "Intervallo (ricostruito)"
            msg = build_message(info, test=True)
            log("\n--- messaggio favht ---\n" + msg)
            if send:
                send_telegram(msg)
    elif ok and teams and fav_gate["status"] != "reject" and score == (0, 0) and fav_gate.get("favourite"):
        log("favht: filtri superati, in produzione qui parte 1 chiamata h2h")
        fake = {"favourite": fav_gate["favourite"], "pre_price": fav_gate.get("pre_price"), "live_price": None,
                "live_status": "ok", "league_id": lid}
        info = make_info(md, fake, pre, None, "HT" if historic else phase, status, stats=stats, scope=scope)
        if historic:
            info["phase"] = "HT"
            info["phase_label"] = "Intervallo (ricostruito)"
            info["entry_minute"] = int(CFG["STAT_REF_MINUTE"])
            info["score"] = "0 - 0"
        info["live_price"] = None
        info["live_quota_nd"] = False
        msg = build_message(info, test=True)
        log("\n--- anteprima favht (quota live non confermata) ---\n" + msg)
        if send:
            send_telegram(msg)
    elif ok and teams and score == (0, 0):
        log("favht non sarebbe un segnale: " + "; ".join(fav_gate.get("reasons") or []))
    if ok and teams and use_odds and lay_g["status"] != "reject":
        live, live_status = fetch_live_h2h(sport, home, away, ko, state)
        decision = assess_lay(stats, pre, score, live, quota_blocked=(live_status == "quota"),
                              live_status=live_status, minute=minute)
        if decision.get("send") and decision.get("lay_estimated"):
            fetch_lay_prices(live, home, away, state)
            decision = assess_lay(stats, pre, score, live, live_status="ok", minute=minute)
        decision["league_id"] = lid
        log(f"lay_sfav decisione: send={decision['send']} {decision['reasons']} crediti={ODDS_STATUS}")
        if decision.get("send"):
            info = make_lay_info(md, decision, pre, live, phase, status, stats, scope, score)
            if historic:
                info["phase"] = "HT"
                info["phase_label"] = "Intervallo (ricostruito)"
            msg = build_lay_message(info, test=True)
            log("\n--- messaggio lay_sfav ---\n" + msg)
            if send:
                send_telegram(msg)
    elif ok and teams and lay_g["status"] != "reject":
        log("lay_sfav: filtri superati, in produzione 1 credito h2h e, se il back è nel range, 1 credito h2h_lay")
    elif ok and teams:
        log("lay_sfav non sarebbe un segnale: " + "; ".join(lay_g.get("reasons") or []))
    log(f"richieste HTTP: {NREQ[0]}")

def _eval_snapshot(md, status):
    """Partita in finestra: numeri di adesso. Altrimenti la foto dell'intervallo."""
    ht = ht_score(md)
    win, phase = in_window(status, max(CFG["FAV_MAX_MINUTE"], CFG["LAY_MAX_MINUTE"]))
    if win:
        minute = played_minute(phase, status)
        stats, scope = stats_for_phase(md, phase)
        return phase, minute, stats, scope, parse_score_pair(status), False, ht
    stats = extract_fh(md)
    score = ht if ht is not None else parse_score_pair(status)
    return "HT", int(CFG["STAT_REF_MINUTE"]), stats, "ht", score, True, ht

def main(argv=None):
    ap = argparse.ArgumentParser(description="Scanner live: favorita sullo 0-0 e banca della sfavorita")
    ap.add_argument("--dry-run", action="store_true", help="valuta le live senza Telegram, senza salvare e senza Odds API")
    ap.add_argument("--test", nargs="?", const="auto", default=None,
                    help="valuta un matchId FotMob (senza valore: una conclusa recente). Niente storico.")
    ap.add_argument("--send", action="store_true", help="con --test invia un messaggio Telegram di prova")
    ap.add_argument("--odds", action="store_true",
                    help="con --test legge la quota live (1 credito h2h; la banca ne spende un altro solo se il back è nel range)")
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
