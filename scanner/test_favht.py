#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test di filtri, parser e messaggio. Nessuna rete."""
import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan

CFG = scan.CFG

def stats(sot, poss, box, red=(0, 0), xg=(None, None)):
    return {"sot": list(sot), "poss": list(poss), "box": list(box), "red": list(red), "xg": list(xg), "empty": False}

def strong(**kw):
    base = stats((5, 1), (64, 36), (24, 10), xg=(1.2, 0.3))
    base.update(kw)
    return base

def pre(h, d, a):
    return {"home": h, "draw": d, "away": a, "source": "test"}

def live(h, d, a, book="Betfair"):
    return {"home": h, "draw": d, "away": a, "book_label": book, "book_key": "betfair_ex_eu"}

def status(short, short_key="", long_key="", max_time=45, base=45, started=True, finished=False,
           second="", penalties=None):
    return {
        "started": started, "finished": finished, "cancelled": False,
        "liveTime": {"short": short, "shortKey": short_key, "longKey": long_key,
                     "maxTime": max_time, "basePeriod": base, "penalties": penalties},
        "halfs": {"secondHalfStarted": second},
    }


class TestWindow(unittest.TestCase):
    def test_ht(self):
        ok, phase = scan.in_window(status("HT", "halftime_short", "pause_match", 45, 45))
        self.assertTrue(ok)
        self.assertEqual(phase, "HT")

    def test_second_half_edge(self):
        ok50, _ = scan.in_window(status("50\u200e’\u200e", max_time=90, base=90, second="27.10.2026 18:34:12"))
        ok51, _ = scan.in_window(status("51'", max_time=90, base=90, second="x"))
        self.assertTrue(ok50)
        self.assertFalse(ok51)

    def test_first_half_and_finished(self):
        self.assertFalse(scan.in_window(status("19'", max_time=45))[0])
        self.assertFalse(scan.in_window(status("HT", finished=True))[0])
        self.assertFalse(scan.in_window(status("HT", started=False))[0])
        self.assertFalse(scan.in_window(status("105'", max_time=120, base=120, second="x"))[0])
        self.assertFalse(scan.in_window(status("HT", penalties=True))[0])

    def test_minute_invisible(self):
        self.assertEqual(scan.parse_minute("19\u200e’\u200e"), 19)
        self.assertEqual(scan.parse_minute("45+2"), 45)
        self.assertIsNone(scan.parse_minute("HT"))

    def test_score(self):
        self.assertTrue(scan.is_score_00({"scoreStr": "0 - 0"}))
        self.assertTrue(scan.is_score_00(home_score=0, away_score=0))
        self.assertFalse(scan.is_score_00({"scoreStr": "1 - 0"}))


class TestFilters(unittest.TestCase):
    def test_dominates_boundaries(self):
        self.assertTrue(scan.dominates(stats((3, 1), (58, 42), (16, 10)), "home")[0])
        self.assertFalse(scan.dominates(stats((3, 2), (58, 42), (16, 10)), "home")[0])  # margine +2
        self.assertFalse(scan.dominates(stats((2, 0), (70, 30), (20, 5)), "home")[0])  # minimo 3
        self.assertFalse(scan.dominates(stats((4, 1), (57, 43), (20, 5)), "home")[0])  # possesso
        self.assertTrue(scan.dominates(stats((4, 1), (58, 42), (20, 5)), "home")[0])
        self.assertFalse(scan.dominates(stats((4, 1), (60, 40), (15, 10)), "home")[0])  # rapporto 1.5
        self.assertTrue(scan.dominates(stats((4, 1), (60, 40), (16, 10)), "home")[0])  # 1.6
        self.assertTrue(scan.dominates(stats((4, 0), (60, 40), (5, 0)), "home")[0])    # avversario a zero
        self.assertFalse(scan.dominates(stats((4, 0), (60, 40), (0, 0)), "home")[0])
        self.assertFalse(scan.dominates(stats((6, 1), (70, 30), (20, 8), red=(1, 0)), "home")[0])
        self.assertTrue(scan.dominates(stats((1, 5), (30, 70), (4, 18)), "away")[0])

    def test_favourite_side(self):
        self.assertEqual(scan.favourite_side(1.40, 6.5), "home")
        self.assertEqual(scan.favourite_side(4.2, 1.55), "away")
        self.assertIsNone(scan.favourite_side(2.0, 2.0))
        self.assertIsNone(scan.favourite_side(None, 1.4))

    def test_prematch_range_blocks_before_live(self):
        gate = scan.pre_live_gate(strong(), pre(1.70, 3.6, 5.0))
        self.assertEqual(gate["status"], "reject")
        self.assertIn("fuori range", gate["reasons"][0])
        gate = scan.pre_live_gate(strong(), pre(1.25, 5.5, 9.0))
        self.assertEqual(gate["status"], "need_live")
        gate = scan.pre_live_gate(strong(), pre(1.60, 3.8, 5.5))
        self.assertEqual(gate["status"], "need_live")
        self.assertEqual(gate["favourite"], "home")

    def test_favourite_must_be_the_one_who_dominates(self):
        # Casa favorita a 1.40 ma è l'ospite a dominare.
        st = stats((1, 6), (35, 65), (6, 22))
        gate = scan.pre_live_gate(st, pre(1.40, 4.5, 7.0))
        self.assertEqual(gate["status"], "reject")
        self.assertEqual(gate["favourite"], "home")

    def test_no_prematch_needs_live_to_pick(self):
        gate = scan.pre_live_gate(strong(), None)
        self.assertEqual(gate["status"], "need_live_pick")
        self.assertIsNone(gate["favourite"])
        self.assertEqual(gate["passers"], ["home"])

    def test_neither_side(self):
        gate = scan.pre_live_gate(stats((1, 1), (50, 50), (8, 8)), None)
        self.assertEqual(gate["status"], "reject")

    def test_empty_stats(self):
        gate = scan.pre_live_gate({"empty": True, "sot": [None, None], "poss": [None, None], "box": [None, None], "red": [0, 0]}, pre(1.4, 4, 6))
        self.assertEqual(gate["status"], "reject")

    def test_live_range_and_pick(self):
        ok = scan.assess(strong(), pre(1.40, 4.5, 6.0), live(1.70, 3.6, 5.2))
        self.assertTrue(ok["send"])
        self.assertEqual(ok["favourite"], "home")
        self.assertEqual(ok["live_price"], 1.70)
        low = scan.assess(strong(), pre(1.40, 4.5, 6.0), live(1.54, 4.0, 6.0))
        self.assertFalse(low["send"])
        self.assertTrue(low["final"])
        high = scan.assess(strong(), pre(1.40, 4.5, 6.0), live(1.86, 3.4, 4.8))
        self.assertFalse(high["send"])
        edges = scan.assess(strong(), pre(1.40, 4.5, 6.0), live(1.55, 3.8, 6.0))
        self.assertTrue(edges["send"])
        top = scan.assess(strong(), pre(1.40, 4.5, 6.0), live(1.85, 3.5, 4.4))
        self.assertTrue(top["send"])

    def test_live_picks_favourite_when_prematch_missing(self):
        # Senza prematch la favorita è la quota live più bassa, e deve aver dominato.
        picked = scan.assess(strong(), None, live(1.72, 3.4, 5.1))
        self.assertTrue(picked["send"])
        self.assertEqual(picked["favourite"], "home")
        self.assertIsNone(picked["pre_price"])
        rejected = scan.assess(strong(), None, live(3.2, 3.4, 1.70))
        self.assertFalse(rejected["send"])
        self.assertTrue(rejected["final"])
        self.assertEqual(rejected["favourite"], "away")

    def test_quota_guard(self):
        alert = scan.assess(strong(), pre(1.40, 4.2, 7.0), None, quota_blocked=True, action="alert")
        self.assertTrue(alert["send"])
        self.assertEqual(alert["live_status"], "quota")
        skip = scan.assess(strong(), pre(1.40, 4.2, 7.0), None, quota_blocked=True, action="skip")
        self.assertFalse(skip["send"])
        self.assertFalse(skip["final"])
        # Senza prematch non si indovina il favorito.
        blind = scan.assess(strong(), None, None, quota_blocked=True, action="alert")
        self.assertFalse(blind["send"])
        self.assertFalse(blind["final"])

    def test_live_error_retries_and_absent_is_final(self):
        err = scan.assess(strong(), pre(1.4, 4, 6), None, live_status="error")
        self.assertFalse(err["send"])
        self.assertFalse(err["final"])
        gone = scan.assess(strong(), pre(1.4, 4, 6), None, live_status="absent")
        self.assertFalse(gone["send"])
        self.assertTrue(gone["final"])

    def test_equal_prematch(self):
        gate = scan.pre_live_gate(strong(), pre(2.5, 3.2, 2.5))
        self.assertEqual(gate["status"], "reject")


class TestParsers(unittest.TestCase):
    def test_fotmob_prematch_only(self):
        payload = {
            "persistentKey": "Bet365_UK CPM",
            "odds": {"oddsType": "PreMatch", "oddsTabMarkets": [{"markets": [
                {"header": "Full Time Result", "fotMobMarketTypeId": {"translationKey": "who_will_win"},
                 "selections": [
                     {"name": "1", "oddsDecimal": "1.30"},
                     {"name": "x", "oddsDecimal": "5.25"},
                     {"name": "2", "oddsDecimal": "10.00"},
                 ]},
                {"header": "Draw No Bet", "selections": [
                     {"name": "Roma", "oddsDecimal": "1.07"}, {"name": "Cagliari", "oddsDecimal": "9.00"},
                 ]},
            ]}]},
        }
        got = scan.parse_fotmob_prematch(payload)
        self.assertEqual(got["home"], 1.30)
        self.assertEqual(got["draw"], 5.25)
        self.assertEqual(got["away"], 10.00)
        self.assertTrue(got["source"].startswith("fotmob:"))
        payload["odds"]["oddsType"] = "Live"
        payload["odds"]["oddsTabMarkets"][0]["markets"][0]["selections"][0]["oddsDecimal"] = "19.00"
        self.assertIsNone(scan.parse_fotmob_prematch(payload))
        self.assertIsNone(scan.parse_fotmob_prematch(None))

    def test_betexplorer_cells(self):
        html = """
        <td data-odd="3.70" data-created="27,10,2026,17,44"" data-pos="1"" data-bookie="bet365.us"></td>
        <td data-odd="3.75" data-created="27,10,2026,18,17"" data-pos="0"" data-bookie="bet365.us"></td>
        <td data-odd="1.95" data-created="27,10,2026,17,44"" data-pos="2"" data-bookie="bet365.us"></td>
        <td data-odd="3.50" data-pos="1" data-bookie="betmgm.us"></td>
        <td data-odd="3.60" data-pos="0" data-bookie="betmgm.us"></td>
        <td data-odd="2.00" data-pos="2" data-bookie="betmgm.us"></td>
        """
        got = scan.parse_betexplorer_odds(html)
        self.assertEqual(got["home"], 3.70)
        self.assertEqual(got["draw"], 3.75)
        self.assertEqual(got["away"], 1.95)
        self.assertIn("bet365", got["source"])

    def test_betexplorer_events(self):
        page = '''
        <script type="application/ld+json">
        {"@type":"SportsEvent","name":"Sassuolo - Lazio","startDate":"2026-10-27T18:30:00+01:00",
         "url":"https://www.betexplorer.com/football/italy/serie-a/sassuolo-lazio/QgHKnTUq/"}
        </script>
        <a href="/football/italy/serie-a/as-roma-cagliari/UNVlhk7M/" class="in-match"><span><strong>AS Roma</strong></span> - <span>Cagliari</span></a>
        <a href="/football/spain/laliga/celta-vigo-betis/b9rGF2t9/" class="in-match"><span>Celta Vigo</span> - <span>Betis</span></a>
        '''
        events = scan.parse_betexplorer_events(page)
        ev = scan.pick_be_event(events, "Sassuolo", "Lazio", "2026-10-27T17:30:00.000Z")
        self.assertEqual(ev["id"], "QgHKnTUq")
        ev2 = scan.pick_be_event(events, "Roma", "Cagliari", "2026-10-27T19:45:00Z")
        self.assertEqual(ev2["id"], "UNVlhk7M")
        ev3 = scan.pick_be_event(events, "Celta Vigo", "Real Betis", "2026-10-25T14:15:00Z")
        self.assertEqual(ev3["id"], "b9rGF2t9")

    def test_h2h_prefers_betfair_then_fallback(self):
        def book(key, home_p, draw_p, away_p, home="Roma", away="Cagliari"):
            return {"key": key, "markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": home_p}, {"name": "Draw", "price": draw_p}, {"name": away, "price": away_p},
            ]}]}
        payload = {"bookmakers": [
            book("pinnacle", 1.40, 4.6, 8.0),
            book("betfair_ex_eu", 1.38, 4.7, 8.2),
        ]}
        got = scan.parse_event_odds(payload, "AS Roma", "Cagliari")
        self.assertEqual(got["book_key"], "betfair_ex_eu")
        self.assertEqual(got["book_label"], "Betfair")
        self.assertEqual(got["home"], 1.38)
        payload = {"bookmakers": [book("williamhill", 1.42, 4.4, 7.5), book("pinnacle", 1.41, 4.5, 7.6)]}
        got = scan.parse_event_odds(payload, "Roma", "Cagliari")
        self.assertEqual(got["book_key"], "pinnacle")
        self.assertEqual(got["book_label"], "book europeo")
        # Betfair senza draw completo ma con le due squadre resta valido; se manca una squadra si salta.
        broken = {"key": "betfair_ex_eu", "markets": [{"key": "h2h", "outcomes": [{"name": "Roma", "price": 1.5}]}]}
        payload = {"bookmakers": [broken, book("unibet_eu", 1.44, 4.2, 7.0)]}
        got = scan.parse_event_odds(payload, "Roma", "Cagliari")
        self.assertEqual(got["book_key"], "unibet_eu")

    def test_names_do_not_collapse_rivals(self):
        self.assertLess(scan.name_score("Manchester United", "Manchester City"), 0.6)
        self.assertGreaterEqual(scan.name_score("AS Roma", "Roma"), 0.6)
        self.assertGreaterEqual(scan.name_score("Internazionale", "Inter"), 0.6)


class TestMessage(unittest.TestCase):
    def info(self, **kw):
        base = {
            "match_id": 5749714, "home": "Inter", "away": "Fiorentina",
            "league": "Serie A", "country": "ITA", "league_id": 55,
            "phase_label": "Intervallo", "minute": None, "score": "0 - 0",
            "favourite_side": "home", "favourite_team": "Inter", "opponent": "Fiorentina",
            "prematch_price": 1.40, "prematch_home": 1.40, "prematch_draw": 4.6, "prematch_away": 7.5,
            "prematch_source": "fotmob:Bet365",
            "live_price": 1.72, "live_home": 1.72, "live_draw": 3.7, "live_away": 5.4,
            "live_book": "Betfair", "live_quota_nd": False,
            "stats": strong(), "time": "2026-10-27 18:50",
        }
        base.update(kw)
        return base

    def test_full_message(self):
        text = scan.build_message(self.info(stats=stats((5, 1), (64, 36), (24, 10), xg=(0.7, 0.2))))
        for bit in (
            "FAVORITA HT 0-0", "Inter - Fiorentina", "Serie A", "Intervallo", "0 - 0",
            "PERCHÉ la giocata", "salita", "COSA giocare", "Punta la favorita",
            "Esito Finale / Match Odds", "Quota minima consigliata", "1% del capitale",
            "COME e QUANDO uscire", "Green up", "1,25", "Stop loss", "70'-75'",
            "rosso", "pressione", "GIOCATA ALTERNATIVA (facoltativa)", "Over 0,5",
            "non va sommata allo stake", "Nessun altro messaggio",
        ):
            self.assertIn(bit, text, bit)
        self.assertNotIn("edbass1/segnali", text)
        self.assertNotIn("già segnalata", text)
        self.assertNotIn("prematch n.d.", text)
        self.assertNotIn("quota live n.d.", text)

    def test_missing_prematch_and_quota(self):
        text = scan.build_message(self.info(
            prematch_price=None, prematch_home=None, prematch_draw=None, prematch_away=None,
            prematch_source=None, live_price=None, live_home=None, live_draw=None, live_away=None,
            live_book=None, live_quota_nd=True,
        ))
        self.assertIn("prematch n.d.", text)
        self.assertIn("quota live n.d.", text)
        self.assertIn("Verifica la quota live", text)

    def test_record_is_json_and_dedup(self):
        info = self.info()
        rec = scan.signal_record(info)
        json.dumps(rec)
        self.assertEqual(rec["strategy"], "favht")
        self.assertEqual(rec["matchId"], 5749714)
        self.assertEqual(rec["odds"]["live"]["favourite"], 1.72)
        state, signals = {}, []
        scan.reseed_sent(state, [rec])
        self.assertIn("5749714", state["sent"])
        scan.reseed_sent(state, [rec])
        self.assertEqual(len(state["sent"]), 1)

    def test_alternative_mentions_over_when_xg_is_high(self):
        text = scan.alternative_text(stats((6, 1), (68, 32), (22, 8), xg=(1.4, 0.2)), "home")
        self.assertIn("Over 1,5", text)
        self.assertIn("facoltativa", scan.build_message(self.info(stats=stats((6, 1), (68, 32), (22, 8), xg=(1.4, 0.2)))).lower())


class TestQuotaState(unittest.TestCase):
    def test_remaining(self):
        self.assertFalse(scan.quota_low({}))
        self.assertFalse(scan.quota_low({"odds_api": {"remaining": "60"}}))
        self.assertTrue(scan.quota_low({"odds_api": {"remaining": "59"}}))
        self.assertFalse(scan.quota_low({"odds_api": {"remaining": None}}))


if __name__ == "__main__":
    unittest.main()
