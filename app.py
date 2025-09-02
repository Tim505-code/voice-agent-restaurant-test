from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import re
from datetime import datetime, timedelta

# ----------- CONFIG & FAKE KNOWLEDGE BASE (pour la démo) -----------
LANG = os.getenv("APP_LANG", "fr-FR")
VOICE = "alice"

RESTAURANT = {
    "name": "Bistro Nova",
    "address": "Rue du Marché 24, 1003 Lausanne (à 3 min de Flon)",
    "phone": "+41 21 555 12 34",
    "email": "contact@bistronova.example",
    "hours": "Ouvert du mardi au dimanche : 11h30–14h30 et 18h30–22h30. Fermé le lundi.",
    "parking": "Parking St-François et Parking du Rôtillon à moins de 5 minutes à pied.",
    "price_range": "Entrées 10–18 CHF, plats 22–42 CHF, desserts 9–14 CHF.",
    "delivery": "Pas de livraison. À emporter midi et soir sur commande.",
    "allergens": "Options sans gluten et végétariennes disponibles. Allergènes indiqués sur la carte.",
    "specials": "Plats du jour du mardi au vendredi midi. Brunch le dimanche (11h30–14h30).",
    "menu": {
        "entrees": ["Tartare de thon au yuzu", "Velouté de potimarron", "Salade de chèvre chaud"],
        "plats": ["Filet de dorade, fenouil rôti", "Entrecôte de boeuf, sauce poivre", "Risotto aux champignons"],
        "desserts": ["Fondant chocolat", "Tarte citron meringuée", "Assortiment de glaces artisanales"]
    },
    # Politiques fictives démo
    "policy": {
        "reservation_hold": "Nous gardons la table 15 minutes.",
        "cancellation": "Annulation gratuite jusqu'à 2h avant l'heure prévue.",
        "group": "Groupes à partir de 8 personnes : menu unique conseillé, nous appeler."
    }
}

# Mémoire simple en RAM : CallSid -> état (slots)
STATE = {}

# ----------- UTILITAIRES TWIML -----------
def say(vr: VoiceResponse, text: str):
    vr.say(text, language=LANG, voice=VOICE)

def twxml(vr: VoiceResponse) -> Response:
    return Response(str(vr), mimetype="text/xml")

def get_state(call_sid: str) -> dict:
    if call_sid not in STATE:
        STATE[call_sid] = {
            "intent": None,
            "slots": {
                "people": None,
                "date": None,   # ISO yyyy-mm-dd
                "time": None,   # HH:MM
                "name": None,
                "phone": None,
                "notes": None
            },
            "last_question": None
        }
    return STATE[call_sid]

# ----------- NLU (2 niveaux : OpenAI si dispo, sinon heuristique règles) -----------
import json
import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # facultatif

def nlu_extract(text: str, state: dict) -> dict:
    """
    Retourne {"intent": str, "slots": {...}}.
    1) Tente via OpenAI (si clé dispo) pour une compréhension plus naturelle.
    2) Sinon parsers heuristiques FR basés mots-clés + regex.
    """
    if OPENAI_API_KEY:
        try:
            prompt = f"""
Tu es un NLU pour un restaurant. Analyse l'énoncé (français) et renvoie un JSON minimal:
- intent parmi: reservation, hours, address, menu, dish_info, price, parking, allergens, delivery, takeaway, contact, specials, cancel
- slots (si présents): people(int), date(YYYY-MM-DD), time(HH:MM), name, phone
- notes (libre)

Exemples:
"Une table demain à 19h pour 4 au nom de Marie" ->
{{"intent":"reservation","slots":{{"people":4,"date":"{(datetime.utcnow()+timedelta(days=1)).date()}","time":"19:00","name":"Marie"}}}}

Réponds seulement en JSON.
Énoncé: "{text}"
"""
            # Appel minimal REST (remplace si tu utilises une SDK)
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "Tu renvoies uniquement un JSON valide."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2
                },
                timeout=12
            )
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            # Normalisation rapide
            out = {"intent": parsed.get("intent"), "slots": parsed.get("slots", {}), "notes": parsed.get("notes")}
            return out
        except Exception:
            pass  # fallback heuristique

    # -------- Heuristique FR (fallback) --------
    t = text.lower()

    def grab_people(s: str):
        m = re.search(r"\b(\d{1,2})\b\s*(?:pers|personnes?)?", s)
        return int(m.group(1)) if m else None

    def grab_time(s: str):
        # 19h, 19:30, 1930
        m = re.search(r"\b(\d{1,2})\s*[h:]\s*(\d{2})\b", s)
        if m:
            hh, mm = m.group(1), m.group(2)
            return f"{int(hh):02d}:{int(mm):02d}"
        m2 = re.search(r"\b(\d{3,4})\b", s)
        if m2:
            raw = m2.group(1)
            if len(raw)==3:
                return f"{raw[0]}:{raw[1:]}"
            else:
                return f"{raw[:2]}:{raw[2:]}"
        m3 = re.search(r"\b(\d{1,2})\s*h\b", s)
        if m3:
            return f"{int(m3.group(1)):02d}:00"
        return None

    def grab_date(s: str):
        # demain, aujourd'hui, après-demain, vendredi, etc. (simplifié)
        today = datetime.now()
        if "aujourd" in s: return today.strftime("%Y-%m-%d")
        if "demain" in s: return (today + timedelta(days=1)).strftime("%Y-%m-%d")
        if "après-demain" in s or "apres-demain" in s: return (today + timedelta(days=2)).strftime("%Y-%m-%d")
        # numéro jj/mm
        m = re.search(r"\b(\d{1,2})[\/\-\.](\d{1,2})\b", s)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            year = today.year
            try:
                dt = datetime(year, mo, d)
                return dt.strftime("%Y-%m-%d")
            except: pass
        # jours de la semaine (prochaine occurrence)
        days = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
        for i, name in enumerate(days):
            if name in s:
                delta = (i - today.weekday()) % 7
                delta = 7 if delta == 0 else delta
                return (today + timedelta(days=delta)).strftime("%Y-%m-%d")
        return None

    intent = None
    if any(k in t for k in ["réserv", "reserv", "table", "book"]):
        intent = "reservation"
    elif any(k in t for k in ["horaire", "ouvert", "heures", "opening"]):
        intent = "hours"
    elif any(k in t for k in ["adresse", "où", "ou se trouve", "localis"]):
        intent = "address"
    elif "menu" in t or "carte" in t:
        intent = "menu"
    elif any(k in t for k in ["prix", "tarif", "cher"]):
        intent = "price"
    elif "parking" in t or "se garer" in t:
        intent = "parking"
    elif "allerg" in t or "sans gluten" in t or "végétar" in t or "vegetar" in t:
        intent = "allergens"
    elif "livrais" in t or "ubereats" in t or "smood" in t:
        intent = "delivery"
    elif "emporter" in t or "take away" in t or "à emporter" in t:
        intent = "takeaway"
    elif "spécial" in t or "plat du jour" in t or "brunch" in t:
        intent = "specials"
    elif "annul" in t or "cancel" in t:
        intent = "cancel"
    elif "contact" in t or "téléphone" in t or "mail" in t:
        intent = "contact"
    else:
        # peut-être une réservation implicite
        if grab_people(t) or grab_time(t) or grab_date(t):
            intent = "reservation"

    slots = {
        "people": grab_people(t),
        "time": grab_time(t),
        "date": grab_date(t)
    }
    return {"intent": intent, "slots": slots, "notes": None}

# ----------- DIALOG MANAGER -----------
def respond_intent(vr: VoiceResponse, call_sid: str, intent: str, slots: dict):
    s = get_state(call_sid)
    # fusion slots
    s["intent"] = intent or s["intent"]
    for k, v in (slots or {}).items():
        if v and not s["slots"].get(k):
            s["slots"][k] = v

    intent = s["intent"]

    if intent == "reservation":
        need = []
        for k in ["people", "date", "time", "name", "phone"]:
            if not s["slots"].get(k):
                need.append(k)
        if "people" in need:
            g = Gather(input="speech", language=LANG, speech_timeout="auto", action="/dialog?slot=people", method="POST")
            say(g, "Pour combien de personnes souhaitez-vous réserver ?")
            vr.append(g); return
        if "date" in need:
            g = Gather(input="speech", language=LANG, speech_timeout="auto", action="/dialog?slot=date", method="POST")
            say(g, "Pour quel jour souhaitez-vous réserver ? Par exemple, demain, vendredi, ou 24 slash 09.")
            vr.append(g); return
        if "time" in need:
            g = Gather(input="speech", language=LANG, speech_timeout="auto", action="/dialog?slot=time", method="POST")
            say(g, "À quelle heure ? Par exemple, dix-neuf trente, ou 19 30.")
            vr.append(g); return
        if "name" in need:
            g = Gather(input="speech", language=LANG, speech_timeout="auto", action="/dialog?slot=name", method="POST")
            say(g, "À quel nom dois-je enregistrer la réservation ?")
            vr.append(g); return
        if "phone" in need:
            g = Gather(input="speech dtmf", num_digits=10, language=LANG, speech_timeout="auto", action="/dialog?slot=phone", method="POST")
            say(g, "Pouvez-vous me laisser un numéro de téléphone pour confirmer ? Dites-le ou composez-le.")
            vr.append(g); return

        # Confirmation finale
        say(vr, f"Parfait. J'enregistre une table pour {s['slots']['people']} personnes, le {s['slots']['date']} à {s['slots']['time']}, au nom de {s['slots']['name']}.")
        say(vr, f"Nous vous recontacterons si besoin au {s['slots']['phone']}. À très bientôt au {RESTAURANT['name']} !")
        vr.hangup(); return

    elif intent == "hours":
        say(vr, RESTAURANT["hours"]); say(vr, "Puis-je vous aider avec autre chose ?")
        vr.redirect("/voice"); return
    elif intent == "address":
        say(vr, f"Notre adresse est : {RESTAURANT['address']}. {RESTAURANT['parking']}")
        say(vr, "Puis-je vous aider avec autre chose ?"); vr.redirect("/voice"); return
    elif intent == "menu":
        say(vr, f"Nos entrées incluent : {', '.join(RESTAURANT['menu']['entrees'])}.")
        say(vr, f"En plats: {', '.join(RESTAURANT['menu']['plats'])}. Et en desserts: {', '.join(RESTAURANT['menu']['desserts'])}.")
        say(vr, "Souhaitez-vous réserver ?"); vr.redirect("/voice"); return
    elif intent == "price":
        say(vr, RESTAURANT["price_range"]); say(vr, "Puis-je vous aider avec autre chose ?")
        vr.redirect("/voice"); return
    elif intent == "parking":
        say(vr, RESTAURANT["parking"]); say(vr, "Autre question ?"); vr.redirect("/voice"); return
    elif intent == "allergens":
        say(vr, RESTAURANT["allergens"]); say(vr, "Souhaitez-vous réserver ?"); vr.redirect("/voice"); return
    elif intent == "delivery":
        say(vr, RESTAURANT["delivery"]); say(vr, "Puis-je vous aider avec autre chose ?"); vr.redirect("/voice"); return
    elif intent == "takeaway":
        say(vr, "Oui, nous proposons l'emporté midi et soir. Commandez 30 minutes à l'avance.")
        say(vr, "Autre chose ?"); vr.redirect("/voice"); return
    elif intent == "specials":
        say(vr, RESTAURANT["specials"]); say(vr, "Souhaitez-vous une réservation ?"); vr.redirect("/voice"); return
    elif intent == "cancel":
        say(vr, "Pour annuler une réservation, dites-moi le nom et l'heure de la réservation, ou contactez-nous au " + RESTAURANT["phone"] + ".")
        say(vr, "Autre chose ?"); vr.redirect("/voice"); return
    elif intent == "contact":
        say(vr, f"Vous pouvez nous joindre au {RESTAURANT['phone']} ou par e-mail {RESTAURANT['email']}.")
        say(vr, "Puis-je vous aider avec autre chose ?"); vr.redirect("/voice"); return
    else:
        say(vr, "Désolé, je n'ai pas compris. Pouvez-vous reformuler ?")
        g = Gather(input="speech", language=LANG, speech_timeout="auto", action="/dialog", method="POST")
        say(g, "Que puis-je faire pour vous ?")
        vr.append(g); return

def normalize_slot(slot: str, text: str):
    t = (text or "").lower()
    if slot == "people":
        m = re.search(r"\b(\d{1,2})\b", t); return int(m.group(1)) if m else None
    if slot == "time":
        m = re.search(r"\b(\d{1,2})\s*[h:]\s*(\d{2})\b", t)
        if m: return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
        m2 = re.search(r"\b(\d{3,4})\b", t)
        if m2:
            raw = m2.group(1)
            return f"{raw[:2]}:{raw[2:]}" if len(raw)==4 else f"{raw[0]}:{raw[1:]}"
        m3 = re.search(r"\b(\d{1,2})\s*h\b", t)
        if m3: return f"{int(m3.group(1)):02d}:00"
        return None
    if slot == "date":
        today = datetime.now()
        if "aujourd" in t: return today.strftime("%Y-%m-%d")
        if "demain" in t: return (today + timedelta(days=1)).strftime("%Y-%m-%d")
        if "après-demain" in t or "apres-demain" in t: return (today + timedelta(days=2)).strftime("%Y-%m-%d")
        m = re.search(r"\b(\d{1,2})[\/\-\.](\d{1,2})\b", t)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            try: return datetime(datetime.now().year, mo, d).strftime("%Y-%m-%d")
            except: return None
        days = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
        for i,name in enumerate(days):
            if name in t:
                delta = (i - today.weekday()) % 7
                delta = 7 if delta==0 else delta
                return (today + timedelta(days=delta)).strftime("%Y-%m-%d")
        return None
    if slot == "name":
        # simple: récupérer premiers mots significatifs
        t2 = re.sub(r"[^a-zàâçéèêëîïôûùüÿñæœ\s-]", " ", t).strip()
        words = [w for w in t2.split() if w not in {"je","m","appelle","c","est","le","la","l","de","du","mon"}]
        return words[0].capitalize() if words else None
    if slot == "phone":
        digits = re.sub(r"\D", "", t)
        if 6 <= len(digits) <= 12:
            return digits
        return None
    return None

# ----------- ROUTES -----------

app = Flask(__name__)

@app.route("/voice", methods=["GET", "POST"])
def voice():
    vr = VoiceResponse()
    say(vr, f"Bienvenue au {RESTAURANT['name']}. Que puis-je faire pour vous ?")
    g = Gather(input="speech", language=LANG, speech_timeout="auto", action="/dialog", method="POST")
    say(g, "Parlez librement. Par exemple : je voudrais réserver demain soir pour quatre.")
    vr.append(g)
    say(vr, "Je n'ai pas reçu de réponse. Je répète.")
    vr.redirect("/voice")
    return twxml(vr)

@app.route("/dialog", methods=["POST"])
def dialog():
    vr = VoiceResponse()
    call_sid = request.form.get("CallSid", "default")
    user = (request.form.get("SpeechResult") or "").strip()

    if not user:
        say(vr, "Désolé, je n'ai rien entendu.")
        vr.redirect("/voice")
        return twxml(vr)

    state = get_state(call_sid)
    # Si on attend un slot précis :
    slot_waited = request.args.get("slot")
    if slot_waited:
        val = normalize_slot(slot_waited, user)
        if val:
            state["slots"][slot_waited] = val
        else:
            # demande encore
            g = Gather(input="speech", language=LANG, speech_timeout="auto", action=f"/dialog?slot={slot_waited}", method="POST")
            msg = {
                "people": "Je n'ai pas saisi le nombre de personnes. Répétez s'il vous plaît.",
                "date": "Je n'ai pas compris la date. Dites par exemple demain, vendredi, ou 24 slash 09.",
                "time": "Je n'ai pas compris l'heure. Dites par exemple dix-huit trente, ou 18 30.",
                "name": "Je n'ai pas saisi votre nom. Pouvez-vous répéter ?",
                "phone": "Je n'ai pas saisi le numéro. Dites-le, ou composez-le au clavier."
            }.get(slot_waited, "Pouvez-vous répéter ?")
            say(g, msg)
            vr.append(g)
            return twxml(vr)

    # NLU
    nlu = nlu_extract(user, state)
    intent = nlu.get("intent")
    slots = nlu.get("slots", {})
    respond_intent(vr, call_sid, intent, slots)
    return twxml(vr)

@app.route("/health", methods=["GET"])
def health():
    return {"ok": True}
