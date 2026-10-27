# Segnali HT — favorita sullo 0-0

Scanner live per una sola giocata: **puntare la favorita prematch all'intervallo (o nei primi minuti del secondo tempo) se è ancora 0-0 e ha dominato il primo tempo**. Gira su GitHub Actions, legge le partite da FotMob e manda **un solo messaggio Telegram** per partita. Non ci sono messaggi di follow-up né di risultato: uscita e stop sono scritti nel messaggio.

Solo Python 3.12, libreria standard (`scanner/scan.py`).

## Cosa fa

Ogni giro controlla le partite in corso. Entra in valutazione solo se:

- lo stato FotMob è l'intervallo (**HT**) oppure il secondo tempo è già iniziato e il minuto è **≤ 50**;
- il punteggio è **0-0**;
- il campionato è nazionale, maschile, senior, e coperto da The Odds API (niente coppe, giovanili, femminile, riserve);
- la favorita ha dominato il primo tempo (soglie sotto);
- la quota prematch della favorita, se trovata, è fra **1,25 e 1,60**;
- la quota live back della favorita è fra **1,55 e 1,85**.

La favorita è la squadra con la quota prematch più bassa. Se il prematch non c'è, si usa la quota live più bassa, ma solo dopo che almeno una squadra ha passato i filtri del primo tempo. Senza nessuna delle due quote il favorito non si indovina e il segnale non parte.

Una partita già notificata non viene segnalata di nuovo (`live/data/state.json`). Lo storico resta in `live/data/signals.json`.

## Soglie

Tutte le soglie sono costanti in cima a `scanner/scan.py` (`CFG`).

| Costante | Default | Significato |
|---|---|---|
| `SOT_MIN` | 3 | tiri in porta minimi della favorita nel primo tempo |
| `SOT_MARGIN` | 2 | favorita ≥ avversaria + questo margine |
| `BOX_RATIO_MIN` | 1,6 | rapporto tocchi in area avversaria (favorita / avversaria). Se l'avversaria è a zero e la favorita no, il filtro passa |
| `POSS_MIN` | 58 | possesso minimo della favorita, in % |
| `RED_MAX` | 0 | rossi massimi della favorita |
| `PRE_MIN` / `PRE_MAX` | 1,25 / 1,60 | quota prematch della favorita, estremi inclusi |
| `LIVE_MIN` / `LIVE_MAX` | 1,55 / 1,85 | quota live back della favorita, estremi inclusi |
| `WINDOW_2H_MAX` | 50 | ultimo minuto di secondo tempo ancora utile |
| `ODDS_MIN_REMAINING` | 60 | sotto questa soglia di crediti residui non si chiama più The Odds API |
| `LOW_QUOTA_ACTION` | `alert` | `alert` invia comunque con «quota live n.d.»; `skip` non invia |
| `STAKE_PCT` | 1 | stake indicato nel messaggio, in % del capitale |
| `GREENUP_ODDS` | 1,25 | quota di green up dopo il gol della favorita |
| `EXIT_MINUTE_FROM` / `EXIT_MINUTE_TO` | 70 / 75 | finestra per chiudere se è ancora 0-0 |

I tocchi in area avversaria sostituiscono gli «attacchi pericolosi», che FotMob non fornisce. L'xG del primo tempo, se c'è, entra nel messaggio e nella giocata alternativa, non nel filtro.

Per cambiarle: modifica `CFG`, fai commit e push su `main`. Il giro successivo usa i valori nuovi.

## Quote prematch (senza crediti Odds API)

Verificato il 27 ottobre 2026, senza login:

1. **Prima del calcio d'inizio**, FotMob `matchOdds?matchId=&ccode3=GBR` con `odds.oddsType = PreMatch`. Esempio reale Roma–Cagliari: 1,30 / 5,25 / 10,00 (Full Time Result, 1-X-2). Lo scanner salva questo snapshot in `state.json` appena vede la partita nelle ore prima del via.
2. **A partita iniziata** lo stesso endpoint FotMob diventa `Live` (Sassuolo–Lazio sul 1-3: 17,00 / 8,50 / 1,13) e **non** va usato come prematch.
3. In quel caso si legge il tab prematch di BetExplorer, ` /match-odds/{id}/1/1x2/odds/?lang=en `. Sulla stessa Sassuolo–Lazio, a gioco in corso, bet365.us era ancora 3,70 / 3,75 / 1,95, non la quota live. Su Roma–Cagliari, prima del via, bet365.us coincideva con FotMob (1,30 / 5,25 / 10,00).

Se nessuna delle due fonti risponde, il segnale può partire lo stesso sulle sole quote live e nel messaggio compare **prematch n.d.**

## Quote live e crediti

The Odds API si chiama **solo dopo** i filtri del primo tempo e, se c'è, il filtro prematch. Una chiamata per partita:

- elenco eventi `GET /sports/{sport}/events` — non scala crediti;
- poi `GET /sports/{sport}/events/{id}/odds?regions=eu&markets=h2h&oddsFormat=decimal` — **1 credito** (un mercato × una region).

Nella stessa risposta si prende **betfair_ex_eu**. Se manca, il primo book europeo disponibile nella lista di fallback (Pinnacle, Unibet, William Hill, …). Non si fa una seconda chiamata.

Lo stato tiene `x-requests-remaining`. Sotto `ODDS_MIN_REMAINING` (60) la chiamata a pagamento non parte. Con `LOW_QUOTA_ACTION=alert` il messaggio esce lo stesso con **quota live n.d.**, ma solo se la favorita è già nota dal prematch. Con `skip` non esce nulla e si riprova finché la partita è in finestra.

Stima: nelle giornate piene passano i filtri poche partite, spesso **0-5 crediti**, raramente verso **8-10** se tanti 0-0 all'intervallo sono dominati da una favorita nel range. Molte giornate restano a zero. Il tetto di sicurezza lascia almeno ~60 crediti nel mese.

## Messaggio Telegram

Titolo `⚽ FAVORITA HT 0-0`, poi partita, campionato, minuto, punteggio, quote, tiri in porta, possesso, tocchi in area, xG se c'è. In fondo, in italiano e calato sui numeri della partita:

- **PERCHÉ** (dominio del primo tempo, e se la quota è salita rispetto al prematch);
- **COSA** (punta la favorita, Esito Finale / Match Odds, quota minima, stake 1% del capitale);
- **COME e QUANDO uscire** (green up verso 1,25 dopo il gol della favorita, stop loss se segna l'avversaria, chiusura al 70'-75' se è ancora 0-0, uscita prima su rosso o calo di pressione);
- **GIOCATA ALTERNATIVA** facoltativa (Over 0,5 secondo tempo, prossimo gol, oppure Over 1,5 se la favorita segna presto). Non va sommata allo stake.

## File di stato

| File | Ruolo |
|---|---|
| `live/data/state.json` | partite già notificate, scartate, quote prematch in cache, crediti residui, soglie dell'ultimo giro |
| `live/data/signals.json` | storico dei segnali inviati (id partita, squadre, campionato, orario, quote, statistiche) |

Il workflow fa commit di questi due file su `main`. Non committare altro.

## Test

```bash
python scanner/test_favht.py
python scanner/scan.py --dry-run
python scanner/scan.py --test
python scanner/scan.py --test 5749714
python scanner/scan.py --test 5749714 --send
python scanner/scan.py --test 5749714 --odds
```

- `--dry-run` guarda le partite live nella finestra, senza Telegram, senza salvare e senza The Odds API.
- `--test` (anche senza id) valuta una partita conclusa recente, o l'id indicato, e stampa numeri e anteprima. Non scrive lo storico.
- `--send` aggiunge un messaggio Telegram marcato TEST.
- `--odds` fa la chiamata live (1 credito): usarla solo quando serve.

Su GitHub: Actions → «Scanner favorita HT» → Run workflow.

## Workflow

`.github/workflows/scan.yml` è schedulato ogni 5 minuti. Su GitHub il cron spesso parte molto meno spesso: ogni giro resta acceso circa **55 minuti** e scansiona ogni **4 minuti**, con un solo run alla volta (`concurrency`, senza annullare quello in corso). Poi fa commit di stato e storico.

Servono i secret `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ODDS_API_KEY`, e in Settings → Actions → General il permesso di scrittura sul repository.

## Limiti

- FotMob e BetExplorer sono endpoint pubblici non ufficiali: se cambia il formato, il prematch va rifatto. Il filtro non usa una quota live spacciata per prematch.
- Se lo snapshot prematch manca e BetExplorer non ha la partita, si va avanti con «prematch n.d.» e si decide solo sulla quota live.
- Sotto i 60 crediti, con l'azione `alert`, la favorita deve essere già nota dal prematch: senza prematch e senza chiamata live il segnale non parte.
- Coppe, femminile, giovanili, riserve e campionati non listati in `ODDS_SPORT` sono fuori.
- Non c'è un secondo messaggio di uscita: il piano di uscita è solo nel testo del segnale.
- La quota Betfair è il prezzo back restituito da The Odds API per il mercato `h2h`, non il lay e non la liquidità.
- xG e corner non sono filtri: i corner non sono nella regola, l'xG spesso manca nei campionati minori.
