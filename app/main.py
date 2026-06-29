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

PREZZI_PER_CHAR = {"10": 3.5, "15": 5.0, "20": 7.0, "25": 9.5}

CONFIG = {
    "nome_negozio": "Filamento",
    "stripe_publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
    "paypal_client_id": os.getenv("PAYPAL_CLIENT_ID", ""),
}

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")


def calcola_totale(testo: str, altezza: str, quantita: int) -> float:
    chars = len(testo.replace(" ", ""))
    prezzo_unit = PREZZI_PER_CHAR.get(altezza, 5.0)
    return round(chars * prezzo_unit * quantita, 2)


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
    nome: str = Form(...),
    cognome: str = Form(...),
    email: str = Form(...),
    telefono: Optional[str] = Form(None),
    indirizzo: str = Form(...),
    citta: str = Form(...),
    cap: str = Form(...),
    provincia: Optional[str] = Form(None),
    paese: str = Form("IT"),
    testo: str = Form(...),
    altezza: str = Form("15"),
    colore: str = Form("bianco"),
    tema: str = Form("classico"),
    quantita: int = Form(1),
    note: Optional[str] = Form(None),
    payment_method: str = Form("stripe"),
    stripe_token: Optional[str] = Form(None),
    paypal_order_id: Optional[str] = Form(None),
):
    form_data = {
        "nome": nome, "cognome": cognome, "email": email, "telefono": telefono,
        "indirizzo": indirizzo, "citta": citta, "cap": cap, "provincia": provincia,
        "testo": testo, "altezza": altezza, "colore": colore, "tema": tema,
        "quantita": str(quantita), "note": note,
    }

    totale = calcola_totale(testo, altezza, quantita)

    if totale <= 0:
        return templates.TemplateResponse("ordina.html", {
            "request": request, "config": CONFIG, "form": form_data,
            "error": "Inserisci almeno un carattere nel campo testo.",
        })

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
                description=f"Filamento #{order_id} — {testo}",
                receipt_email=email,
            )

        elif payment_method == "paypal":
            if not paypal_order_id:
                raise ValueError("ID ordine PayPal mancante.")
            # La verifica lato PayPal avviene tramite webhook o SDK server
            # Per ora accettiamo l'order_id restituito dal client

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
        "testo": testo,
        "altezza": altezza,
        "colore": colore,
        "tema": tema,
        "quantita": quantita,
        "indirizzo": indirizzo,
        "citta": citta,
        "cap": cap,
        "totale": f"{totale:.2f}",
    }

    request.session = getattr(request, "session", {})
    response = templates.TemplateResponse("conferma.html", {
        "request": request, "config": CONFIG, "order": order,
    })
    return response


@app.get("/conferma", response_class=HTMLResponse)
async def conferma(request: Request):
    order = {
        "id": "—", "email": "—", "nome": "—", "cognome": "—",
        "testo": "—", "altezza": "—", "colore": "—", "tema": "—",
        "quantita": "—", "indirizzo": "—", "citta": "—", "cap": "—",
        "totale": "—",
    }
    return templates.TemplateResponse("conferma.html", {
        "request": request, "config": CONFIG, "order": order,
    })
