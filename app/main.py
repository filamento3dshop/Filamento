import os
import uuid
import stripe
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
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
