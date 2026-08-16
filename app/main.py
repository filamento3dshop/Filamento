import os
import secrets
import uuid
import threading
import resend
import stripe
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, date
from fastapi import Depends, FastAPI, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Filamento")

OLD_DOMAINS = {"toto3d.it", "www.toto3d.it"}
NEW_DOMAIN = "www.filamentoshop.it"


@app.middleware("http")
async def redirect_old_domain(request: Request, call_next):
    host = request.url.hostname
    if host in OLD_DOMAINS:
        new_url = request.url.replace(scheme="https", netloc=NEW_DOMAIN)
        return RedirectResponse(url=str(new_url), status_code=301)
    return await call_next(request)


app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

PREZZI_DIM = {"20": 29.90, "30": 39.90}
PREZZI_DECO = {0: 0.0, 1: 8.0, 2: 15.0, 3: 20.0, 4: 25.0}
PREZZI_SCRITTA = {
    "5":  {"corto": 15.90, "lungo": 19.90},
    "10": {"corto": 25.90, "lungo": 29.90},
}
SPEDIZIONE = 3.0

GALLERY = [
    {
        "img": "/static/img/gallery/vittorio-spazio.jpg",
        "lettera": "2",
        "dimensione": "30",
        "colore_lettera": "Azzurro",
        "colore_scritta": "Bianco",
        "tema": "Spazio",
    },
    {
        "img": "/static/img/gallery/uno-safari-corallo.jpg",
        "lettera": "1",
        "dimensione": "30",
        "colore_lettera": "Corallo",
        "colore_scritta": "Bianco",
        "tema": "Safari",
    },
    {
        "img": "/static/img/gallery/g-lilla.jpg",
        "lettera": "G",
        "dimensione": "30",
        "colore_lettera": "Lilla",
        "colore_scritta": "Bianco",
        "tema": "",
    },
    {
        "img": "/static/img/gallery/b-salmone-mare.jpg",
        "lettera": "B",
        "dimensione": "20",
        "colore_lettera": "Salmone",
        "colore_scritta": "Bianco",
        "tema": "Mare",
    },
    {
        "img": "/static/img/gallery/leonardo-bluette-sport.jpg",
        "lettera": "Leonardo",
        "dimensione": "30",
        "colore_lettera": "Bluette",
        "colore_scritta": "Bianco",
        "tema": "Sport",
    },
]

CONFIG = {
    "nome_negozio": "Filamento",
    "stripe_publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
    "paypal_client_id": os.getenv("PAYPAL_CLIENT_ID", ""),
}

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
NEGOZIO_EMAIL = os.getenv("NEGOZIO_EMAIL", "filamento3d.shop@gmail.com")
EMAIL_FROM = os.getenv("EMAIL_FROM", "Filamento <noreply@filamentoshop.it>")

security = HTTPBasic()


def get_db():
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    return conn


def init_db():
    if not DATABASE_URL:
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            payment_method TEXT,
            email TEXT, nome TEXT, cognome TEXT, telefono TEXT,
            lettera TEXT, nome_bimbo TEXT,
            colore_lettera TEXT, colore_scritta TEXT,
            dimensione TEXT, tema TEXT, decorazioni_scelte TEXT, note TEXT,
            codice_fiscale TEXT,
            indirizzo_spedizione TEXT,
            totale TEXT,
            codice_sconto TEXT,
            sconto_applicato TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS codici_sconto (
            codice TEXT PRIMARY KEY,
            percentuale INTEGER NOT NULL,
            usato BOOLEAN NOT NULL DEFAULT FALSE,
            usato_da TEXT,
            usato_in TEXT,
            creato_at TEXT NOT NULL
        )
    """)
    # migrazione colonne per DB esistenti
    for col, typedef in [("codice_sconto", "TEXT"), ("sconto_applicato", "TEXT")]:
        try:
            cur.execute(f"ALTER TABLE orders ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    conn.commit()
    cur.close()
    conn.close()


init_db()


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin non configurato.")
    valid_user = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    valid_pass = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenziali non valide.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


METODO_LABEL = {"stripe": "Carta di credito", "paypal": "PayPal", "bonifico": "Bonifico bancario"}


def _send_email_bg(to: str, subject: str, html: str):
    if not RESEND_API_KEY:
        print("[EMAIL] RESEND_API_KEY non configurata")
        return
    try:
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html})
        print(f"[EMAIL] OK — inviata a {to}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


def invia_email(to: str, subject: str, html: str):
    threading.Thread(target=_send_email_bg, args=(to, subject, html), daemon=True).start()


def email_cliente(order: dict, payment_method: str) -> str:
    metodo = METODO_LABEL.get(payment_method, payment_method)
    bonifico_note = ""
    if payment_method == "bonifico":
        bonifico_note = f"""
        <div style="background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:16px;margin:16px 0;">
          <strong>Istruzioni bonifico:</strong><br>
          Intestato a: <strong>FOCUS 3D di Camilla Campani</strong><br>
          IBAN: <strong>IT42G03268223000EMH02590789</strong><br>
          Causale: <strong>Ordine Filamento #{order['id']} — {order['nome']} {order['cognome']}</strong><br>
          <small>Il tuo ordine verrà messo in lavorazione alla ricezione del pagamento.</small>
        </div>"""
    sconto_riga = f"<tr><td>Sconto applicato</td><td><strong>{order.get('sconto_applicato','')}</strong></td></tr>" if order.get('sconto_applicato') else ""
    return f"""
    <div style="font-family:Georgia,serif;max-width:580px;margin:0 auto;color:#1A1714;">
      <div style="background:#1A1714;padding:24px 32px;">
        <h1 style="color:#fff;font-size:22px;margin:0;">Filamento</h1>
      </div>
      <div style="padding:32px;">
        <h2 style="font-size:18px;">Grazie per il tuo ordine, {order['nome']}! 🎉</h2>
        <p>Abbiamo ricevuto il tuo ordine <strong>#{order['id']}</strong>. Lo realizziamo con cura e te lo spediamo in 7–10 giorni lavorativi.</p>
        {bonifico_note}
        <table style="width:100%;border-collapse:collapse;margin:20px 0;font-size:14px;">
          <tr style="background:#f5f5f5;"><td style="padding:8px 12px;"><strong>Lettera</strong></td><td style="padding:8px 12px;">{order['lettera']} ({order['dimensione']} cm)</td></tr>
          <tr><td style="padding:8px 12px;">Nome sul pezzo</td><td style="padding:8px 12px;">{order.get('nome_bimbo','—')}</td></tr>
          <tr style="background:#f5f5f5;"><td style="padding:8px 12px;">Pagamento</td><td style="padding:8px 12px;">{metodo}</td></tr>
          {sconto_riga}
          <tr><td style="padding:8px 12px;"><strong>Totale</strong></td><td style="padding:8px 12px;"><strong>€{order['totale']}</strong></td></tr>
          <tr style="background:#f5f5f5;"><td style="padding:8px 12px;">Spedizione a</td><td style="padding:8px 12px;">{order.get('indirizzo_spedizione','—')}</td></tr>
        </table>
        <p style="color:#888;font-size:13px;">Per qualsiasi domanda scrivi a <a href="mailto:{NEGOZIO_EMAIL}">{NEGOZIO_EMAIL}</a></p>
      </div>
    </div>"""


def email_negozio(order: dict, payment_method: str) -> str:
    metodo = METODO_LABEL.get(payment_method, payment_method)
    return f"""
    <div style="font-family:monospace;max-width:580px;margin:0 auto;color:#1A1714;">
      <h2>Nuovo ordine #{order['id']} — {metodo}</h2>
      <p><strong>Cliente:</strong> {order['nome']} {order['cognome']} ({order['email']})<br>
      <strong>Tel:</strong> {order.get('telefono','—')}</p>
      <p><strong>Lettera:</strong> {order['lettera']} {order['dimensione']}cm<br>
      <strong>Nome:</strong> {order.get('nome_bimbo','—')}<br>
      <strong>Colore lettera:</strong> {order.get('colore_lettera','—')}<br>
      <strong>Colore scritta:</strong> {order.get('colore_scritta','—')}<br>
      <strong>Tema:</strong> {order.get('tema','—')}</p>
      <p><strong>Spedizione:</strong> {order.get('indirizzo_spedizione','—')}</p>
      <p><strong>Totale:</strong> €{order['totale']}{' (sconto ' + order['sconto_applicato'] + ')' if order.get('sconto_applicato') else ''}</p>
      <p><a href="https://www.filamentoshop.it/admin/ordine/{order['id']}">→ Vedi ordine in admin</a></p>
    </div>"""


def calcola_totale(dimensione: str, num_deco: int, sconto_perc: int = 0) -> float:
    base = PREZZI_DIM.get(dimensione, 29.90)
    extra = PREZZI_DECO.get(min(num_deco, 4), 0.0)
    subtotal = base + extra + SPEDIZIONE
    if sconto_perc > 0:
        subtotal = round(subtotal * (1 - sconto_perc / 100), 2)
    return round(subtotal, 2)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "config": CONFIG})


@app.get("/ordina", response_class=HTMLResponse)
async def ordina_get(request: Request, tema: str = None):
    return templates.TemplateResponse("ordina.html", {
        "request": request,
        "config": CONFIG,
        "form": {"tema": tema or ""},
        "error": None,
    })


@app.get("/api/codice-sconto/{codice}")
async def verifica_codice(codice: str):
    if not DATABASE_URL:
        return JSONResponse({"valido": False, "errore": "DB non disponibile"})
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT percentuale, usato FROM codici_sconto WHERE codice = %s", (codice.upper().strip(),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return JSONResponse({"valido": False, "errore": "Codice non valido"})
    if row["usato"]:
        return JSONResponse({"valido": False, "errore": "Codice già utilizzato"})
    return JSONResponse({"valido": True, "percentuale": row["percentuale"]})


@app.post("/ordina", response_class=HTMLResponse)
async def ordina_post(
    request: Request,
    # step 1
    lettera: str = Form(...),
    nome_bimbo: str = Form(...),
    colore_lettera: str = Form("corallo"),
    colore_scritta: str = Form("bianco"),
    dimensione: str = Form("20"),
    note: Optional[str] = Form(None),
    tema: str = Form("nessuno"),
    num_deco: int = Form(0),
    decorazioni_scelte: Optional[str] = Form(None),
    # step 2
    nome: str = Form(...),
    cognome: str = Form(...),
    codice_fiscale: str = Form(...),
    email: str = Form(...),
    telefono: str = Form(...),
    res_indirizzo: str = Form(...),
    res_citta: str = Form(...),
    res_cap: str = Form(...),
    res_provincia: Optional[str] = Form(None),
    spedizione_diversa: Optional[str] = Form(None),
    indirizzo: Optional[str] = Form(None),
    citta: Optional[str] = Form(None),
    cap: Optional[str] = Form(None),
    provincia: Optional[str] = Form(None),
    # step 3
    payment_method: str = Form("stripe"),
    stripe_token: Optional[str] = Form(None),
    paypal_order_id: Optional[str] = Form(None),
    codice_sconto: Optional[str] = Form(None),
):
    form_data = {
        "lettera": lettera, "nome_bimbo": nome_bimbo,
        "colore_lettera": colore_lettera, "colore_scritta": colore_scritta,
        "dimensione": dimensione, "note": note, "tema": tema,
        "decorazioni_scelte": decorazioni_scelte,
        "nome": nome, "cognome": cognome, "codice_fiscale": codice_fiscale,
        "email": email, "telefono": telefono,
        "res_indirizzo": res_indirizzo, "res_citta": res_citta,
        "res_cap": res_cap, "res_provincia": res_provincia,
        "spedizione_diversa": spedizione_diversa,
        "indirizzo": indirizzo, "citta": citta, "cap": cap, "provincia": provincia,
    }

    # verifica codice sconto
    sconto_perc = 0
    codice_sconto_valido = None
    if codice_sconto and DATABASE_URL:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT percentuale, usato FROM codici_sconto WHERE codice = %s", (codice_sconto.upper().strip(),))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and not row["usato"]:
            sconto_perc = row["percentuale"]
            codice_sconto_valido = codice_sconto.upper().strip()

    totale = calcola_totale(dimensione, num_deco, sconto_perc)
    totale_centesimi = int(totale * 100)
    order_id = str(uuid.uuid4())[:8].upper()

    try:
        if payment_method == "stripe":
            if not stripe_token:
                raise ValueError("Token Stripe mancante.")
            stripe.PaymentIntent.create(
                amount=totale_centesimi,
                currency="eur",
                payment_method=stripe_token,
                confirm=True,
                description=f"Filamento #{order_id} — Lettera {lettera} ({colore_lettera}) per {nome_bimbo}",
                receipt_email=email,
            )

        elif payment_method == "paypal":
            if not paypal_order_id:
                raise ValueError("ID ordine PayPal mancante.")

    except stripe.error.CardError as e:
        return templates.TemplateResponse("ordina.html", {
            "request": request, "config": CONFIG, "form": form_data,
            "error": f"Pagamento rifiutato: {e.user_message}",
        })
    except Exception as e:
        return templates.TemplateResponse("ordina.html", {
            "request": request, "config": CONFIG, "form": form_data,
            "error": f"Errore durante il pagamento: {str(e)}",
        })

    order = {
        "id": order_id,
        "email": email,
        "nome": nome,
        "cognome": cognome,
        "lettera": lettera,
        "nome_bimbo": nome_bimbo,
        "colore_lettera": colore_lettera,
        "colore_scritta": colore_scritta,
        "dimensione": dimensione,
        "tema": tema,
        "decorazioni_scelte": decorazioni_scelte,
        "nome": nome,
        "cognome": cognome,
        "codice_fiscale": codice_fiscale,
        "res_indirizzo": res_indirizzo,
        "res_citta": res_citta,
        "res_cap": res_cap,
        "indirizzo_spedizione": (f"{indirizzo}, {citta} {cap}" if spedizione_diversa and indirizzo else f"{res_indirizzo}, {res_citta} {res_cap}"),
        "totale": f"{totale:.2f}",
    }

    if DATABASE_URL:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO orders (
                id, created_at, payment_method, email, nome, cognome, telefono,
                lettera, nome_bimbo, colore_lettera, colore_scritta, dimensione,
                tema, decorazioni_scelte, note, codice_fiscale, indirizzo_spedizione, totale,
                codice_sconto, sconto_applicato
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                order["id"], datetime.utcnow().isoformat(), payment_method, email, nome, cognome, telefono,
                lettera, nome_bimbo, colore_lettera, colore_scritta, dimensione,
                tema, decorazioni_scelte, note, codice_fiscale, order["indirizzo_spedizione"], order["totale"],
                codice_sconto_valido, f"{sconto_perc}%" if sconto_perc else None,
            ),
        )
        if codice_sconto_valido:
            cur.execute(
                "UPDATE codici_sconto SET usato = TRUE, usato_da = %s, usato_in = %s WHERE codice = %s",
                (email, order["id"], codice_sconto_valido),
            )
        conn.commit()
        cur.close()
        conn.close()

    order["sconto_applicato"] = f"{sconto_perc}%" if sconto_perc else ""
    invia_email(email, f"Conferma ordine #{order_id} — Filamento", email_cliente(order, payment_method))
    invia_email(NEGOZIO_EMAIL, f"Nuovo ordine #{order_id} — {nome} {cognome}", email_negozio(order, payment_method))

    return templates.TemplateResponse("conferma.html", {
        "request": request, "config": CONFIG, "order": order,
    })


@app.get("/scritte", response_class=HTMLResponse)
async def scritte_get(request: Request):
    return templates.TemplateResponse("scritte.html", {
        "request": request,
        "config": CONFIG,
        "form": {},
        "error": None,
    })


@app.post("/scritte", response_class=HTMLResponse)
async def scritte_post(
    request: Request,
    testo: str = Form(...),
    testo_maiuscolo: str = Form(""),
    font_style: str = Form("moderno"),
    dimensione: str = Form("10"),
    colore: str = Form("corallo"),
    note: Optional[str] = Form(None),
    nome: str = Form(...),
    cognome: str = Form(...),
    codice_fiscale: str = Form(...),
    email: str = Form(...),
    telefono: str = Form(...),
    res_indirizzo: str = Form(...),
    res_citta: str = Form(...),
    res_cap: str = Form(...),
    res_provincia: Optional[str] = Form(None),
    tipo_consegna: str = Form("spedizione"),
    spedizione_diversa: Optional[str] = Form(None),
    indirizzo: Optional[str] = Form(None),
    citta: Optional[str] = Form(None),
    cap: Optional[str] = Form(None),
    provincia: Optional[str] = Form(None),
    payment_method: str = Form("stripe"),
    stripe_token: Optional[str] = Form(None),
    paypal_order_id: Optional[str] = Form(None),
    codice_sconto: Optional[str] = Form(None),
):
    testo_clean = (testo_maiuscolo or testo).strip().upper()
    n_lettere = len(testo_clean.replace(" ", ""))
    fascia = "lungo" if n_lettere > 5 else "corto"
    usa_spedizione = tipo_consegna != "ritiro"

    form_data = {
        "testo": testo_clean, "font_style": font_style, "dimensione": dimensione,
        "colore": colore, "note": note,
        "nome": nome, "cognome": cognome, "codice_fiscale": codice_fiscale,
        "email": email, "telefono": telefono,
        "res_indirizzo": res_indirizzo, "res_citta": res_citta,
        "res_cap": res_cap, "res_provincia": res_provincia,
        "tipo_consegna": tipo_consegna,
        "spedizione_diversa": spedizione_diversa,
        "indirizzo": indirizzo, "citta": citta, "cap": cap, "provincia": provincia,
    }

    sconto_perc = 0
    codice_sconto_valido = None
    if codice_sconto and DATABASE_URL:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT percentuale, usato FROM codici_sconto WHERE codice = %s", (codice_sconto.upper().strip(),))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and not row["usato"]:
            sconto_perc = row["percentuale"]
            codice_sconto_valido = codice_sconto.upper().strip()

    prezzi_dim = PREZZI_SCRITTA.get(dimensione, PREZZI_SCRITTA["10"])
    prezzo_base = prezzi_dim[fascia]
    sped_cost = SPEDIZIONE if usa_spedizione else 0.0
    subtotal = prezzo_base + sped_cost
    if sconto_perc > 0:
        subtotal = round(subtotal * (1 - sconto_perc / 100), 2)
    totale = round(subtotal, 2)
    totale_centesimi = int(totale * 100)
    order_id = str(uuid.uuid4())[:8].upper()

    try:
        if payment_method == "stripe":
            if not stripe_token:
                raise ValueError("Token Stripe mancante.")
            stripe.PaymentIntent.create(
                amount=totale_centesimi,
                currency="eur",
                payment_method=stripe_token,
                confirm=True,
                description=f"Filamento Scritta #{order_id} — {testo_clean} ({colore}) {dimensione}cm",
                receipt_email=email,
            )
        elif payment_method == "paypal":
            if not paypal_order_id:
                raise ValueError("ID ordine PayPal mancante.")
    except stripe.error.CardError as e:
        return templates.TemplateResponse("scritte.html", {
            "request": request, "config": CONFIG, "form": form_data,
            "error": f"Pagamento rifiutato: {e.user_message}",
        })
    except Exception as e:
        return templates.TemplateResponse("scritte.html", {
            "request": request, "config": CONFIG, "form": form_data,
            "error": f"Errore durante il pagamento: {str(e)}",
        })

    indirizzo_spedizione = (
        f"{indirizzo}, {citta} {cap}" if spedizione_diversa and indirizzo
        else f"{res_indirizzo}, {res_citta} {res_cap}"
    )

    order = {
        "id": order_id,
        "email": email,
        "nome": nome,
        "cognome": cognome,
        "lettera": testo_clean,
        "nome_bimbo": f"Scritta: {testo_clean}",
        "colore_lettera": colore,
        "colore_scritta": font_style,
        "dimensione": dimensione,
        "tema": "scritta3d",
        "decorazioni_scelte": None,
        "codice_fiscale": codice_fiscale,
        "indirizzo_spedizione": indirizzo_spedizione,
        "totale": f"{totale:.2f}",
        "sconto_applicato": f"{sconto_perc}%" if sconto_perc else "",
    }

    if DATABASE_URL:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO orders (
                id, created_at, payment_method, email, nome, cognome, telefono,
                lettera, nome_bimbo, colore_lettera, colore_scritta, dimensione,
                tema, decorazioni_scelte, note, codice_fiscale, indirizzo_spedizione, totale,
                codice_sconto, sconto_applicato
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                order["id"], datetime.utcnow().isoformat(), payment_method, email, nome, cognome, telefono,
                testo_clean, order["nome_bimbo"], colore, font_style, dimensione,
                "scritta3d", None, note, codice_fiscale, indirizzo_spedizione, order["totale"],
                codice_sconto_valido, f"{sconto_perc}%" if sconto_perc else None,
            ),
        )
        if codice_sconto_valido:
            cur.execute(
                "UPDATE codici_sconto SET usato = TRUE, usato_da = %s, usato_in = %s WHERE codice = %s",
                (email, order["id"], codice_sconto_valido),
            )
        conn.commit()
        cur.close()
        conn.close()

    invia_email(email, f"Conferma ordine #{order_id} — Filamento", email_cliente(order, payment_method))
    invia_email(NEGOZIO_EMAIL, f"Nuovo ordine #{order_id} — {nome} {cognome}", email_negozio(order, payment_method))

    return templates.TemplateResponse("conferma.html", {
        "request": request, "config": CONFIG, "order": order,
    })


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request, "config": CONFIG})


@app.get("/gallery", response_class=HTMLResponse)
async def gallery(request: Request):
    return templates.TemplateResponse("gallery.html", {"request": request, "config": CONFIG, "gallery": GALLERY})


@app.get("/termini", response_class=HTMLResponse)
async def termini(request: Request):
    return templates.TemplateResponse("termini.html", {"request": request, "config": CONFIG})


@app.get("/spedizioni", response_class=HTMLResponse)
async def spedizioni(request: Request):
    return templates.TemplateResponse("spedizioni.html", {"request": request, "config": CONFIG})


from fastapi.responses import Response as FastAPIResponse

@app.get("/sitemap.xml")
async def sitemap():
    BASE = "https://www.filamentoshop.it"
    oggi = date.today().isoformat()

    # Immagini della gallery, dichiarate nella sitemap per l'indicizzazione su Google Images
    gallery_images = "".join(
        f'\n    <image:image><image:loc>{BASE}{item["img"]}</image:loc>'
        f'<image:title>Lettera {item["lettera"]} 3D personalizzata {item["dimensione"]} cm '
        f'{item["colore_lettera"]} tema {item.get("tema", "")}</image:title></image:image>'
        for item in GALLERY
    )

    # Immagini dei temi decorativi mostrate in homepage
    temi_images = "".join(
        f'\n    <image:image><image:loc>{BASE}/static/img/temi/{t}.jpg</image:loc>'
        f'<image:title>Decorazione 3D tema {t.capitalize()} per lettere personalizzate</image:title></image:image>'
        for t in ["safari", "spazio", "mare", "cielo", "principesse", "sport", "motori", "natura", "cibo"]
    )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url><loc>{BASE}/</loc><lastmod>{oggi}</lastmod><changefreq>weekly</changefreq><priority>1.0</priority>
    <image:image><image:loc>{BASE}/static/img/hero-esempio.jpg</image:loc><image:title>Composizione di lettere 3D personalizzate Filamento</image:title></image:image>{temi_images}
  </url>
  <url><loc>{BASE}/ordina</loc><lastmod>{oggi}</lastmod><changefreq>monthly</changefreq><priority>0.9</priority></url>
  <url><loc>{BASE}/scritte</loc><lastmod>{oggi}</lastmod><changefreq>monthly</changefreq><priority>0.9</priority></url>
  <url><loc>{BASE}/gallery</loc><lastmod>{oggi}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority>{gallery_images}
  </url>
  <url><loc>{BASE}/spedizioni</loc><lastmod>2026-06-01</lastmod><changefreq>yearly</changefreq><priority>0.4</priority></url>
  <url><loc>{BASE}/privacy</loc><lastmod>2026-06-01</lastmod><changefreq>yearly</changefreq><priority>0.3</priority></url>
  <url><loc>{BASE}/termini</loc><lastmod>2026-06-01</lastmod><changefreq>yearly</changefreq><priority>0.3</priority></url>
</urlset>"""
    return FastAPIResponse(content=xml, media_type="application/xml")

@app.get("/health")
@app.head("/health")
async def health():
    """Endpoint leggero per il monitoraggio uptime (Better Stack).

    Serve a impedire la sospensione del servizio su Render senza generare
    l'intera homepage a ogni ping. Risposta di pochi byte, senza template,
    query al database o accessi a disco.
    """
    return FastAPIResponse(
        content="ok",
        media_type="text/plain",
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


@app.get("/google19690eee5b182cfd.html")
async def google_verify():
    return FastAPIResponse(content="google-site-verification: google19690eee5b182cfd.html", media_type="text/html")

@app.get("/robots.txt")
async def robots():
    txt = """User-agent: *
Allow: /
Disallow: /admin
Disallow: /admin/
Disallow: /conferma
Disallow: /static/js/
Disallow: /static/models/
Disallow: /health
Crawl-delay: 2

User-agent: Googlebot
Allow: /
Disallow: /admin
Disallow: /conferma
Disallow: /health
Crawl-delay: 0

Sitemap: https://www.filamentoshop.it/sitemap.xml
"""
    return FastAPIResponse(content=txt, media_type="text/plain")


@app.get("/conferma", response_class=HTMLResponse)
async def conferma(request: Request):
    order = {
        "id": "—", "email": "—", "nome": "—", "cognome": "—",
        "lettera": "—", "nome_bimbo": "—",
        "colore_lettera": "—", "colore_scritta": "—",
        "dimensione": "—", "tema": "—",
        "indirizzo": "—", "citta": "—", "cap": "—", "totale": "—",
    }
    return templates.TemplateResponse("conferma.html", {
        "request": request, "config": CONFIG, "order": order,
    })


@app.get("/admin", response_class=HTMLResponse)
async def admin_orders(request: Request, username: str = Depends(verify_admin)):
    orders = []
    if DATABASE_URL:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders ORDER BY created_at DESC")
        orders = [dict(row) for row in cur.fetchall()]
        cur.close()
        conn.close()
    return templates.TemplateResponse("admin.html", {
        "request": request, "config": CONFIG, "orders": orders,
    })


@app.get("/admin/sconti", response_class=HTMLResponse)
async def admin_sconti(request: Request, username: str = Depends(verify_admin)):
    codici = []
    if DATABASE_URL:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM codici_sconto ORDER BY creato_at DESC")
        codici = [dict(row) for row in cur.fetchall()]
        cur.close()
        conn.close()
    return templates.TemplateResponse("admin_sconti.html", {
        "request": request, "config": CONFIG, "codici": codici,
    })


@app.post("/admin/sconti/crea", response_class=HTMLResponse)
async def admin_crea_codice(
    request: Request,
    codice: str = Form(...),
    percentuale: int = Form(...),
    username: str = Depends(verify_admin),
):
    if not DATABASE_URL:
        raise HTTPException(status_code=503)
    codice = codice.upper().strip()
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO codici_sconto (codice, percentuale, usato, creato_at) VALUES (%s, %s, FALSE, %s)",
            (codice, percentuale, datetime.utcnow().isoformat()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
    cur.close()
    conn.close()
    return RedirectResponse("/admin/sconti", status_code=303)


@app.post("/admin/sconti/elimina/{codice}", response_class=HTMLResponse)
async def admin_elimina_codice(codice: str, username: str = Depends(verify_admin)):
    if not DATABASE_URL:
        raise HTTPException(status_code=503)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM codici_sconto WHERE codice = %s", (codice,))
    conn.commit()
    cur.close()
    conn.close()
    return RedirectResponse("/admin/sconti", status_code=303)


@app.post("/admin/ordine/{order_id}/elimina", response_class=HTMLResponse)
async def admin_elimina_ordine(order_id: str, username: str = Depends(verify_admin)):
    if not DATABASE_URL:
        raise HTTPException(status_code=503)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM orders WHERE id = %s", (order_id,))
    conn.commit()
    cur.close()
    conn.close()
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/ordine/{order_id}", response_class=HTMLResponse)
async def admin_order_detail(request: Request, order_id: str, username: str = Depends(verify_admin)):
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Database non configurato.")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Ordine non trovato.")
    return templates.TemplateResponse("admin_order.html", {
        "request": request, "config": CONFIG, "order": dict(row),
    })
