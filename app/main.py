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

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

PREZZI_DIM = {"20": 29.90, "30": 39.90}
PREZZO_TEMA = 5.0
SPEDIZIONE = 3.0

CONFIG = {
    "nome_negozio": "Filamento",
    "stripe_publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
    "paypal_client_id": os.getenv("PAYPAL_CLIENT_ID", ""),
}

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")


def calcola_totale(dimensione: str, tema: str) -> float:
    base = PREZZI_DIM.get(dimensione, 29.90)
    extra = PREZZO_TEMA if tema and tema != "nessuno" else 0.0
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
    colore: str = Form("corallo"),
    dimensione: str = Form("20"),
    tipo_evento: str = Form(...),
    note: Optional[str] = Form(None),
    tema: str = Form("nessuno"),
    # step 2
    nome: str = Form(...),
    cognome: str = Form(...),
    email: str = Form(...),
    telefono: Optional[str] = Form(None),
    indirizzo: str = Form(...),
    citta: str = Form(...),
    cap: str = Form(...),
    provincia: Optional[str] = Form(None),
    # step 3
    payment_method: str = Form("stripe"),
    stripe_token: Optional[str] = Form(None),
    paypal_order_id: Optional[str] = Form(None),
):
    form_data = {
        "lettera": lettera, "nome_bimbo": nome_bimbo, "colore": colore,
        "dimensione": dimensione, "tipo_evento": tipo_evento, "note": note, "tema": tema,
        "nome": nome, "cognome": cognome, "email": email, "telefono": telefono,
        "indirizzo": indirizzo, "citta": citta, "cap": cap, "provincia": provincia,
    }

    totale = calcola_totale(dimensione, tema)
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
                description=f"Filamento #{order_id} — Lettera {lettera} per {nome_bimbo}",
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
        "colore": colore,
        "dimensione": dimensione,
        "tipo_evento": tipo_evento,
        "tema": tema,
        "indirizzo": indirizzo,
        "citta": citta,
        "cap": cap,
        "totale": f"{totale:.2f}",
    }

    return templates.TemplateResponse("conferma.html", {
        "request": request, "config": CONFIG, "order": order,
    })


@app.get("/conferma", response_class=HTMLResponse)
async def conferma(request: Request):
    order = {
        "id": "—", "email": "—", "nome": "—", "cognome": "—",
        "lettera": "—", "nome_bimbo": "—", "colore": "—", "dimensione": "—",
        "tipo_evento": "—", "tema": "—",
        "indirizzo": "—", "citta": "—", "cap": "—", "totale": "—",
    }
    return templates.TemplateResponse("conferma.html", {
        "request": request, "config": CONFIG, "order": order,
    })
