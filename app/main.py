import os
import secrets
import uuid
import stripe
import psycopg
from psycopg.rows import dict_row
from datetime import datetime
from fastapi import Depends, FastAPI, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
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
SPEDIZIONE = 3.0

CONFIG = {
    "nome_negozio": "Filamento",
    "stripe_publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
    "paypal_client_id": os.getenv("PAYPAL_CLIENT_ID", ""),
}

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

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
            totale TEXT
        )
    """)
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


def calcola_totale(dimensione: str, num_deco: int) -> float:
    base = PREZZI_DIM.get(dimensione, 29.90)
    extra = PREZZI_DECO.get(min(num_deco, 4), 0.0)
    return round(base + extra + SPEDIZIONE, 2)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "config": CONFIG})


@app.get("/ordina", response_class=HTMLResponse)
async def ordina_get(request: Request):
    return templates.TemplateResponse("ordina.html", {
        "request": request,
        "config": CONFIG,
        "form": {},
        "error": None,
    })


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

    totale = calcola_totale(dimensione, num_deco)
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
                tema, decorazioni_scelte, note, codice_fiscale, indirizzo_spedizione, totale
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                order["id"], datetime.utcnow().isoformat(), payment_method, email, nome, cognome, telefono,
                lettera, nome_bimbo, colore_lettera, colore_scritta, dimensione,
                tema, decorazioni_scelte, note, codice_fiscale, order["indirizzo_spedizione"], order["totale"],
            ),
        )
        conn.commit()
        cur.close()
        conn.close()

    return templates.TemplateResponse("conferma.html", {
        "request": request, "config": CONFIG, "order": order,
    })


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request, "config": CONFIG})


@app.get("/termini", response_class=HTMLResponse)
async def termini(request: Request):
    return templates.TemplateResponse("termini.html", {"request": request, "config": CONFIG})


@app.get("/spedizioni", response_class=HTMLResponse)
async def spedizioni(request: Request):
    return templates.TemplateResponse("spedizioni.html", {"request": request, "config": CONFIG})


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
