# Come si tiene sveglio il sito

Il sito sta su piani gratuiti, e i piani gratuiti si addormentano. Questo file
spiega com'è impostata la sveglia, e soprattutto **perché** è impostata così:
ogni scelta qui dentro è la cicatrice di qualcosa che si è rotto.

## I due sonni

Sono due, con scadenze lontanissime fra loro.

**Render spegne il servizio dopo 15 minuti senza richieste.** Da spento non
consuma ore. Per tenerlo su basta che una richiesta arrivi: conta il fatto
stesso che arrivi.

**Supabase mette in pausa il database dopo 7 giorni senza query.** Non è una
scadenza fissa, è inattività. Qui rispondere non basta: bisogna toccare
davvero il database.

Quindici minuti contro sette giorni. Se si trattassero allo stesso modo si
finirebbe per interrogare il database centoquaranta volte al giorno per evitare
una pausa che scatta dopo una settimana.

## Gli indirizzi: `/health` e `/health/db`

Il codice sta in `app/main.py`, cercare `def health`.

**`/health`** è il bersaglio della sveglia. Risponde **sempre `200`** finché il
processo è vivo:

| Corpo della risposta | Significato |
|---|---|
| `ok` | sito in piedi, database raggiungibile |
| `ok (database non raggiungibile)` | sito in piedi, database no |

**`/health/db`** è per il monitoraggio: risponde `503` quando il database non
risponde.

Sembra una duplicazione inutile. Non lo è, ed è la prima delle due lezioni care.

### Perché `/health` non deve mai rispondere 503

La prima versione rispondeva `503` quando la query al database falliva. Sembrava
sensato: il monitoraggio deve sapere che qualcosa non va.

Ma cron-job.org conta ogni `503` come esecuzione fallita e, dopo una serie
consecutiva, **disattiva il job da solo**. È successo il 9 maggio 2026: 26
fallimenti, job spento, e da quel momento nessuno teneva più sveglio il sito.
Che è rimasto giù ogni notte finché non ce ne siamo accorti.

Il risultato era autodistruttivo: un problema temporaneo del database spegneva
in modo permanente ciò che tiene sveglio il sito.

Da qui la separazione. Su `/health` un errore è pericoloso, perché quella rotta
governa la sveglia. Su `/health/db` è informazione utile, perché nessuno la usa
per pingare.

### Cosa fa dentro

Ogni ping tiene sveglio Render. Il database viene interrogato con una `SELECT 1`
**al massimo una volta all'ora** (`INTERVALLO_PING_DB`). Con i ping ogni dieci
minuti sono una ventina di query al giorno contro una soglia che scatta dopo
sette giorni a zero: abbondante.

Il contatore viene aggiornato **prima** del tentativo, non dopo. Così, se il
database è irraggiungibile, non si riprova a ogni ping: si aspetta comunque
un'ora. Un guasto non deve diventare un martello su un database già in
difficoltà.

Due dettagli che sembrano piccoli e non lo sono. La risposta ha
`Cache-Control: no-store`, altrimenti una cache lungo la strada risponderebbe al
posto del sito e il ping non arriverebbe mai fino a Render — il servizio si
spegnerebbe lo stesso, con il monitor tutto verde. E ha `X-Robots-Tag: noindex`,
perché `/health` non è una pagina e non deve finire nei risultati di ricerca.
In più `robots.txt` contiene `Disallow: /health` per entrambi i gruppi
user-agent, e l'indirizzo non compare nella sitemap.

Entrambe le rotte rispondono sia a **GET** sia a **HEAD**.

## L'avvio non deve toccare il database

Questa è la seconda lezione cara, ed è la meno intuitiva.

`init_db()` crea le tabelle e tenta le migrazioni. Veniva chiamata **durante
l'import del modulo**, quindi uvicorn non apriva la porta finché non aveva
finito di parlare con Supabase.

Il 7 luglio 2026 il sito ha smesso di risvegliarsi la mattina. I ping fallivano
tutti con `503` dalle 06:00 in poi, e il servizio tornava su solo quando
qualcuno lo apriva col browser.

La ragione, misurata: `init_db()` con database irraggiungibile impiega **8
secondi**, e quegli 8 secondi precedevano l'apertura della porta. Su una
partenza a freddo di Render si sommano all'avvio, e il servizio resta
irraggiungibile più a lungo di quanto un ping sia disposto ad aspettare —
**cron-job.org chiude a 30 secondi**. Il browser invece aspetta, ed è per questo
che il browser lo risvegliava e il ping no.

Ora `init_db()` gira in un **thread daemon**: il server apre la porta subito e
risponde mentre il database si prepara. L'import completa in circa 3 secondi.
Le sole rotte che richiedono le tabelle sono ordini e admin, e arrivano
comunque dopo.

C'è anche un `connect_timeout` di 8 secondi (`TIMEOUT_DB`) su tutte le
connessioni: senza, un database in pausa lascia la connessione appesa fino al
timeout di rete, molto più lungo.

**Regola generale:** niente che possa essere lento deve stare sul percorso di
avvio. Il tempo di apertura della porta è il tempo entro cui il sito deve
risvegliarsi.

## cron-job.org — la sveglia

| Voce | Valore |
|---|---|
| URL | `https://www.filamentoshop.it/health` |
| Metodo | GET (HEAD va altrettanto bene) |
| Schedule | `*/10 0,1,6-23 * * *` |
| Fuso orario | Europe/Rome |
| Timeout | 30 s |
| Notifica fallimenti | dopo 5 |

**Perché ogni dieci minuti.** Lo impone Render con i suoi quindici minuti di
tolleranza. Dieci lascia margine: se un ping salta, il successivo arriva ancora
in tempo.

**Perché quelle ore.** `0,1,6-23` significa: si pinga a mezzanotte e all'una,
poi si ricomincia alle sei. Dalle 2:00 alle 5:59 nessuno chiama, quindi Render
spegne il servizio e quelle ore non vengono conteggiate. Senza la finestra
notturna il sito consuma circa **744 ore su 750**, con la finestra circa
**630**. Il fuso orario conta proprio per questo: in UTC la finestra cadrebbe
due ore prima.

**Perché la soglia di notifica è 5.** Il primo ping delle 06:00 può fallire
mentre il servizio riparte. È fisiologico. Con una soglia bassa quel fallimento
isolato genererebbe allarmi ogni mattina, e sommandosi porterebbe alla
disattivazione automatica del job.

Chi capita sul sito fra le 2 e le 6 aspetta una cinquantina di secondi al primo
caricamento. A quell'ora passa quasi solo Googlebot, che ritenta.

## Better Stack — l'allarme

Attualmente **in pausa**. cron-job.org sveglia e avvisa via email in caso di
fallimento, il che copre l'essenziale.

Se un giorno lo si riattiva, due regole non negoziabili:

**Deve controllare `/health/db`, non `/health` e non la homepage.** `/health`
risponde `200` anche col database giù, quindi non suonerebbe mai. La homepage
pure: da quando l'avvio non dipende più dal database, home, gallery, guida e
pagine legali rispondono lo stesso mentre ordini e admin sono fermi. Un monitor
puntato lì vedrebbe verde mentre nessuno può comprare.

**Deve avere la finestra di manutenzione 2:00–6:00.** Altrimenti terrebbe sveglio
il servizio per conto suo e la finestra notturna non servirebbe a niente: si
tornerebbe a 744 ore. In più suonerebbe ogni notte per un sito che dorme di
proposito.

## Cosa controllare quando qualcosa non torna

**Apri `/health` dal browser.** È il primo gesto:

- risponde `ok` → sito e database stanno bene;
- risponde `ok (database non raggiungibile)` → il sito è vivo, il database no:
  si guarda Supabase, di solito è in pausa e basta riattivarlo dalla dashboard;
- non risponde affatto o ci mette quasi un minuto → il servizio era spento e si
  sta riaccendendo. Se sono le tre di notte è normale; se sono le undici del
  mattino qualcosa non ha pingato.

**Se non ha pingato**, in ordine: su cron-job.org, l'ultima esecuzione e il suo
esito; **che il job sia ancora abilitato**; che l'URL sia ancora quello giusto.
Un job disabilitato non avvisa nessuno di esserlo — è precisamente come si è
rotto tutto a maggio.

**Se il sito non si risveglia la mattina ma di giorno funziona**, il sospetto è
sempre lo stesso: qualcosa ha rallentato l'avvio oltre i 30 secondi. Guardare
cosa è stato aggiunto sul percorso di import.

**Se il monte ore si consuma troppo in fretta**, la causa è quasi sempre una di
due: la finestra notturna non è attiva da qualche parte, oppure nel workspace
Render è comparso un secondo servizio. Le 750 ore sono **per workspace**, non
per servizio, e ci finiscono dentro anche le preview environment.

## Cosa non fare

- **Non far rispondere a `/health` un codice diverso da 200** quando il processo
  è vivo. Vedi sopra: cron-job.org disattiva il job e il sito resta giù.
- **Non mettere chiamate al database sul percorso di avvio.** Il risveglio deve
  stare dentro i 30 secondi del ping.
- **Non mettere altri servizi nel workspace Render.** Consumano dalle stesse 750
  ore. Ad agosto 2026 lo sforamento è stato di 2,93 ore — lo 0,4% — e il sito è
  rimasto irraggiungibile per sei giorni, perché la sospensione non si sblocca
  togliendo il carico: dura fino al periodo di fatturazione successivo. È il
  motivo per cui Filamento ha ora un account Render tutto suo, separato da
  Focus3D.
- **Non puntare il monitoraggio sulla homepage.** Non suonerebbe quando serve.
- **Non togliere `no-store` dalla risposta di `/health`.** Il ping smetterebbe di
  arrivare fino a Render, e non se ne accorgerebbe nessuno finché il sito non si
  spegne.
- **Non pingare più spesso** pensando di essere più sicuri: più ping non tengono
  il sito "più sveglio", consumano solo le ore che servono a stare dentro il
  piano gratuito.

## Dove sta cosa

| Cosa | Dove |
|---|---|
| Codice del sito | GitHub, `filamento3dshop/Filamento` |
| Hosting | Render, account dedicato a Filamento, piano Free |
| Database | Supabase, piano Free, progetto `filamento3dshop's Project` |
| Sveglia | cron-job.org, job "Filamento keep-alive" |
| Allarme | Better Stack, in pausa |
| Email transazionali | Resend |
| Pagamenti | Stripe e PayPal |

Il deploy è automatico: ogni merge su `main` fa ripartire Render da solo.
