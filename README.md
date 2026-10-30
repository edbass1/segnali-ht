# Segnali live — favorita sullo 0-0 e banca della sfavorita

Scanner live con due giocate, ciascuna con **un solo messaggio Telegram** per partita. Non ci sono messaggi di follow-up né di risultato: uscita e stop sono scritti nel messaggio. Gira su GitHub Actions, legge le partite da FotMob.

Solo Python 3.12, libreria standard (`scanner/scan.py`).

## Cosa fa

Ogni giro guarda le partite in corso, dall'intervallo fino al minuto indicato. Due strategie, indipendenti:

1. **favht** — punta la favorita se è ancora **0-0** e sta dominando.
2. **lay_sfav** — banca la sfavorita se è in vantaggio e la favorita, molto corta in prematch, sta comunque dominando.

Una partita può ricevere al massimo un avviso per strategia. Si escludono coppe, giovanili, femminile, riserve e i campionati non coperti da The Odds API.

### Favorita sullo 0-0

Entra in valutazione se:

- lo stato FotMob è l'intervallo (**HT**) oppure il secondo tempo è iniziato e il minuto è **≤ `FAV_MAX_MINUTE`** (70);
- il punteggio è **0-0**;
- la favorita domina (soglie sotto);
- la quota prematch della favorita, se trovata, è fra **1,25 e 1,60**;
- la quota live back della favorita sta nella fascia del minuto.

La favorita è la squadra con la quota prematch più bassa. Se il prematch non c'è, si usa la quota live più bassa, ma solo dopo che almeno una squadra ha passato i filtri. Senza nessuna delle due quote il segnale non parte.

All'intervallo si usano le statistiche del **primo tempo**. Dopo l'intervallo si usano le statistiche dell'**intera partita fino a quel minuto** (periodo FotMob `All`). I tiri in porta sono un conteggio, quindi la soglia cresce in proporzione ai minuti giocati rispetto a 45. Possesso e rapporto dei tocchi in area sono già dei tassi e non si scalano.

### Banca della sfavorita

Entra in valutazione se:

- si è fra l'intervallo e **`LAY_MAX_MINUTE`** (70);
- la sfavorita conduce di **1 gol**, oppure di **2** solo se la favorita prematch è **≤ 1,25**;
- la favorita prematch è **≤ 1,45** (senza prematch questa giocata non parte);
- la favorita domina: differenza tiri in porta **≥ +3** su 45' (poi in proporzione), rapporto tocchi in area **≥ 2**, possesso **≥ 60%**, niente rosso alla favorita;
- la quota **back** live della sfavorita è fra **1,80 e 3,00**.

Si punta in lay sulla sfavorita (Match Odds), con responsabilità fissa. Se The Odds API restituisce `h2h_lay` si usa quel prezzo; altrimenti il messaggio dice che il lay sta circa un tick sopra il back.

## Soglie

Tutte le soglie sono costanti in cima a `scanner/scan.py` (`CFG`).

| Costante | Default | Significato |
|---|---|---|
| `SOT_MIN` | 3 | tiri in porta minimi della favorita su 45' (favht) |
| `SOT_MARGIN` | 2 | favorita ≥ avversaria + questo margine, su 45' |
| `BOX_RATIO_MIN` | 1,6 | rapporto tocchi in area (favorita / avversaria). Se l'avversaria è a zero e la favorita no, il filtro passa |
| `POSS_MIN` | 58 | possesso minimo della favorita, in % |
| `RED_MAX` | 0 | rossi massimi della favorita |
| `PRE_MIN` / `PRE_MAX` | 1,25 / 1,60 | quota prematch della favorita per favht |
| `LIVE_MIN` / `LIVE_MAX` | 1,55 / 1,85 | fascia live back all'intervallo |
| `LIVE_MIN_PER_MIN` / `LIVE_MAX_PER_MIN` | 0,02 / 0,03 | quanto sale la fascia per ogni minuto dopo il 45' |
| `STAT_REF_MINUTE` | 45 | minuti su cui sono calibrate le soglie dei tiri |
| `FAV_MAX_MINUTE` | 70 | ultimo minuto utile per favht |
| `EXIT_AFTER_MIN` / `EXIT_AFTER_MAX` | 10 / 15 | chiusura temporale, minuti dopo l'ingresso |
| `EXIT_HARD_CAP` | 80 | non restare sullo 0-0 oltre questo minuto |
| `GREENUP_ODDS` | 1,25 | green up dopo il gol della favorita |
| `STAKE_PCT` | 1 | stake della puntata secca, in % del capitale |
| `LAY_MAX_MINUTE` | 70 | ultimo minuto utile per la banca |
| `LAY_PRE_MAX` | 1,45 | favorita prematch massima per bancare la sfavorita |
| `LAY_PRE_TWO_GOAL` | 1,25 | sotto questa quota si accetta anche uno svantaggio di 2 gol |
| `LAY_SOT_MARGIN` | 3 | differenza tiri in porta su 45', poi proporzionale |
| `LAY_BOX_RATIO_MIN` | 2 | rapporto tocchi in area per la banca |
| `LAY_POSS_MIN` | 60 | possesso minimo per la banca |
| `LAY_LIVE_MIN` / `LAY_LIVE_MAX` | 1,80 / 3,00 | quota back della sfavorita |
| `LAY_LIABILITY_PCT` | 1 | responsabilità fissa della banca, in % del capitale |
| `LAY_EXIT_MINUTE` | 80 | chiusura se il gol della favorita non arriva |
| `ODDS_MIN_REMAINING` | 60 | sotto questa soglia non si chiama più The Odds API |
| `LOW_QUOTA_ACTION` | `alert` | per favht: `alert` invia con «quota live n.d.»; `skip` non invia. La banca non parte mai senza quota |
| `ODDS_RECHECK_MIN` | 10 | minuti di gioco fra due letture live della stessa giocata |

I tocchi in area avversaria sostituiscono gli «attacchi pericolosi», che FotMob non fornisce. L'xG, se c'è, entra nel messaggio e nella giocata alternativa, non nel filtro.

Per cambiarle: modifica `CFG`, fai commit e push su `main`. Il giro successivo usa i valori nuovi.

### Perché la fascia live sale col minuto

All'intervallo una favorita prematch 1,25–1,60 ancora sullo 0-0 quota di solito fra **1,55 e 1,85**: il primo tempo senza gol l'ha allungata, ma resta corta. Più si va avanti, più pesa il tempo che manca. A parità di dominio la probabilità di non segnare più sale, e il prezzo back sale con essa. La fascia si sposta quindi in su, di 0,02 sul minimo e di 0,03 sul massimo per ogni minuto dopo il 45':

- al 60' circa **1,85–2,30**;
- al 70' circa **2,05–2,60**.

Il tetto sale più del minimo, così la fascia si allarga: col poco tempo rimasto il prezzo «giusto» è meno preciso. Non si insegue una quota altissima. Sopra il tetto il mercato sta dicendo che il gol è poco probabile, e l'uscita temporale è vicina. Per la banca della sfavorita la fascia resta fissa (1,80–3,00): lì il prezzo è quello a cui si accetta il lay, non un drift da inseguire.

## Quote prematch (senza crediti Odds API)

Verificato il 27 ottobre 2026, senza login:

1. **Prima del calcio d'inizio**, FotMob `matchOdds?matchId=&ccode3=GBR` con `odds.oddsType = PreMatch`. Esempio reale Roma–Cagliari: 1,30 / 5,25 / 10,00 (Full Time Result, 1-X-2). Lo scanner salva questo snapshot in `state.json` appena vede la partita nelle ore prima del via.
2. **A partita iniziata** lo stesso endpoint FotMob diventa `Live` e **non** va usato come prematch.
3. In quel caso si legge il tab prematch di BetExplorer, `/match-odds/{id}/1/1x2/odds/?lang=en`. Su Sassuolo–Lazio, a gioco in corso sul 1-3, bet365.us era ancora 3,70 / 3,75 / 1,95, non la quota live. Su Roma–Cagliari, prima del via, bet365.us coincideva con FotMob (1,30 / 5,25 / 10,00). Anche le righe dei risultati, senza `<strong>`, vengono lette (Celta Vigo–Betis).

Per **favht**, se nessuna delle due fonti risponde, il segnale può partire lo stesso sulle sole quote live e nel messaggio compare **prematch n.d.** Per **lay_sfav** il prematch è obbligatorio: senza, non si sa chi è la favorita corta.

## Quote live e crediti

The Odds API si chiama **solo dopo** i filtri statistici e, se c'è, il filtro prematch. Elenco eventi `GET /sports/{sport}/events` non scala crediti.

- **favht:** `markets=h2h&regions=eu` — **1 credito**. Nella stessa risposta si prende **betfair_ex_eu**. Se manca, il primo book europeo della lista di fallback. Se il prezzo è fuori dalla fascia del minuto, si riprova non prima di `ODDS_RECHECK_MIN` minuti di gioco (la fascia intanto sale). Una partita che passa i filtri ma non il prezzo può costare 1–3 crediti fra intervallo e 70'.
- **lay_sfav:** la stessa chiamata `h2h` (1 credito) dopo i filtri. Solo se il back della sfavorita è fra 1,80 e 3,00 si fa una seconda chiamata `h2h_lay` (**1 credito in più**) per leggere il lay. Se il lay non c'è, il messaggio usa un tick sopra il back.

Lo stato tiene `x-requests-remaining`. Sotto 60 la chiamata a pagamento non parte. Con `LOW_QUOTA_ACTION=alert` il messaggio favht esce lo stesso con **quota live n.d.**, ma solo se la favorita è già nota dal prematch. La banca, senza prezzo, non parte.

Stima: spesso **0–8 crediti al giorno**. Una giornata con parecchi 0-0 dominati e qualche banca può avvicinarsi a **12–15**. Molte giornate restano a zero. Il tetto lascia almeno ~60 crediti nel mese.

## Messaggi Telegram

**Favorita.** Titolo `⚽ FAVORITA 0-0 LIVE`, poi partita, campionato, minuto, 0-0, quote, tiri, possesso, tocchi in area, xG se c'è. In fondo:

- **PERCHÉ** (dominio fino a quel minuto, e se la quota è salita rispetto al prematch);
- **COSA** (punta la favorita, Esito Finale / Match Odds, fascia di quel minuto, stake 1%);
- **COME e QUANDO uscire** (green up verso 1,25 dopo il gol della favorita, stop se segna l'avversaria, chiusura 10–15 minuti dopo l'ingresso e comunque entro l'80', uscita prima su rosso o calo di pressione);
- **GIOCATA ALTERNATIVA** facoltativa. Presto: Over 0,5 secondo tempo o prossimo gol. Da circa il 60': prossimo gol o Over 0,5 da quel minuto.

**Banca.** Titolo `🏦 BANCA LA SFAVORITA`. Si spiega la responsabilità fissa (1% del capitale) e lo stake: **responsabilità / (quota lay − 1)**. Uscite: green up quando la favorita segna (pareggio, o gol che dimezza un gap da due); stop se la sfavorita segna ancora; chiusura intorno all'80' se il gol non arriva. Alternativa facoltativa: Over dei gol attuali + 0,5, oppure punta la favorita o il pareggio / DNB. Non va sommata allo stake.

## File di stato

| File | Ruolo |
|---|---|
| `live/data/state.json` | partite già notificate o scartate, per strategia, quote prematch in cache, crediti residui, soglie dell'ultimo giro |
| `live/data/signals.json` | storico dei segnali inviati (id partita, strategia, squadre, campionato, orario, quote, statistiche) |

La chiave di dedup è `idPartita:strategia`. Il workflow fa commit di questi due file su `main`. Non committare altro.

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
- `--test` (anche senza id) valuta una partita conclusa recente, o l'id indicato, sulle due strategie. Se la partita non è in finestra usa la foto dell'intervallo. Non scrive lo storico.
- `--send` aggiunge un messaggio Telegram marcato TEST.
- `--odds` fa la chiamata live (1 credito h2h; un secondo credito solo se la banca ha il back nel range). Usarla solo quando serve.

Su GitHub: Actions → «Scanner live» → Run workflow.

## Workflow

`.github/workflows/scan.yml` è schedulato ogni 5 minuti. Su GitHub il cron spesso parte molto meno spesso: ogni giro resta acceso circa **55 minuti** e scansiona ogni **4 minuti**, con un solo run alla volta (`concurrency`, senza annullare quello in corso). Poi fa commit di stato e storico.

Servono i secret `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ODDS_API_KEY`, e in Settings → Actions → General il permesso di scrittura sul repository.

## Limiti

- FotMob e BetExplorer sono endpoint pubblici non ufficiali: se cambia il formato, il prematch va rifatto. Il filtro non usa una quota live spacciata per prematch.
- Se lo snapshot prematch manca e BetExplorer non ha la partita, favht può andare avanti con «prematch n.d.». lay_sfav in quel caso non parte.
- Sotto i 60 crediti, con l'azione `alert`, la favorita di favht deve essere già nota dal prematch. La banca non esce senza quota live.
- La fascia live di favht è una retta voluta, non un modello del book. Va ritarata da `LIVE_MIN_PER_MIN` e `LIVE_MAX_PER_MIN` se i prezzi reali stanno altrove.
- I tiri richiesti crescono coi minuti. Possesso e rapporto area restano quelli di configurazione.
- Coppe, femminile, giovanili, riserve e campionati non listati in `ODDS_SPORT` sono fuori.
- Non c'è un secondo messaggio di uscita: il piano di uscita è solo nel testo del segnale.
- La quota Betfair di favht è il prezzo back di `h2h`. Per la banca, il lay è `h2h_lay` se la chiamata lo restituisce, altrimenti un tick sopra il back. Non è la liquidità.
- xG e corner non sono filtri.
- BetExplorer risponde a volte 429: un errore di rete non viene memorizzato come «prematch assente», si riprova al giro dopo.
