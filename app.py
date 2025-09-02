# IVR 1–2–3 pour restaurant, FR, sans suggestions d'heures
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import re
from datetime import datetime, timedelta

app = Flask(__name__)

# --- Paramètres & Base de connaissances (fictive pour démo) ---
LANG = "fr-FR"
VOICE = "alice"
RESTAURANT = {
    "name": "Bistro Nova",
    "hours": "Ouvert du mardi au dimanche, 11h30 à 14h30 et 18h30 à 22h30. Fermé le lundi.",
    "address": "Rue du Marché 24, 1003 Lausanne.",
    "phone": "+41 21 555 12 34",
    "parking": "Parkings St-François et Rôtillon à 5 minutes à pied.",
    "payments": "Cartes Visa, Mastercard, Amex, Maestro. Twint et espèces acceptés.",
    "wifi": "Wi-Fi gratuit sur place.",
    "access": "Accès PMR disponible, toilettes adaptées.",
    "kids": "Chaises hautes et menus enfants disponibles.",
    "pets": "Chiens acceptés en terrasse, en salle selon affluence.",
    "terrace": "Terrasse ouverte aux beaux jours, sans réservation spécifique.",
    "delivery": "Pas de livraison. À emporter midi et soir sur commande.",
    "allergens": "Allergènes indiqués sur la carte. Options sans gluten et végétariennes.",
    "glutenfree": "Oui, plusieurs plats sans gluten. Demandez au service : nous adaptons si possible.",
    "vegetarian": "Oui, options végétariennes et un plat vegan du moment.",
    "halal": "Viandes non certifiées halal ; plats végétariens disponibles.",
    "alcohol": "Carte des vins et bières artisanales. Cocktails maison.",
    "price": "Entrées 10–18 CHF, plats 22–42 CHF, desserts 9–14 CHF."
}

def twxml(vr: VoiceResponse) -> Response:
    return Response(str(vr), mimetype="text/xml")

def say(vr: VoiceResponse, text: str):
    vr.say(text, language=LANG, voice=VOICE)

# --- Mémoire minimale par appel (en RAM) ---
STATE = {}  # CallSid -> dict

def st(call_sid):
    if call_sid not in STATE:
        STATE[call_sid] = {"resa": {"people": None, "date": None, "time": None, "name": None, "phone": None}}
    return STATE[call_sid]

# --- Helpers de parsing simple (sans suggestions) ---
def parse_people(text: str):
    m = re.search(r"\b(\d{1,2})\b", text)
    return int(m.group(1)) if m else None

def parse_time(text: str):
    t = text.lower()
    # 19h30 / 19:30 / 19 h 30 / 19h / 7h
    m = re.search(r"\b(\d{1,2})\s*[h:]\s*(\d{2})\b", t)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    m2 = re.search(r"\b(\d{1,2})\s*h\b", t)
    if m2:
        return f"{int(m2.group(1)):02d}:00"
    m3 = re.search(r"\b(\d{3,4})\b", t)
    if m3:
        raw = m3.group(1)
        return f"{raw[:2]}:{raw[2:]}" if len(raw) == 4 else f"{raw[0]}:{raw[1:]}"
    return None

def parse_date(text: str):
    t = text.lower()
    today = datetime.now()
    if "aujourd" in t: return today.strftime("%Y-%m-%d")
    if "demain" in t: return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if "après-demain" in t or "apres-demain" in t: return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    # jj/mm
    m = re.search(r"\b(\d{1,2})[\/\-\.](\d{1,2})\b", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            return datetime(datetime.now().year, mo, d).strftime("%Y-%m-%d")
        except: pass
    # jour de semaine
    days = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    for i, name in enumerate(days):
        if name in t:
            delta = (i - today.weekday()) % 7
            delta = 7 if delta == 0 else delta
            return (today + timedelta(days=delta)).strftime("%Y-%m-%d")
    return None

# --- IVR principal ---
@app.route("/voice", methods=["GET","POST"])
def voice():
    vr = VoiceResponse()
    say(vr, f"Bienvenue au {RESTAURANT['name']}.")
    g = Gather(input="dtmf", num_digits=1, timeout=6, action="/route", method="POST")
    say(g, "Pour réserver une table, appuyez sur 1. Pour nos horaires, appuyez sur 2. "
            "Pour poser une question, appuyez sur 3.")
    vr.append(g)
    say(vr, "Je n'ai pas reçu de saisie. Je répète.")
    vr.redirect("/voice")
    return twxml(vr)

@app.route("/route", methods=["POST"])
def route():
    vr = VoiceResponse()
    d = (request.form.get("Digits") or "").strip()
    if d == "1":
        vr.redirect("/resa?step=people"); return twxml(vr)
    if d == "2":
        say(vr, RESTAURANT["hours"]); say(vr, "Merci pour votre appel. À bientôt !"); vr.hangup(); return twxml(vr)
    if d == "3":
        # Questions libres (speech), mais SANS suggestions
        g = Gather(input="speech", speech_timeout="auto", language=LANG, action="/qa", method="POST")
        say(g, "Posez votre question, je vous écoute.")
        vr.append(g)
        say(vr, "Je n'ai pas entendu. Revenons au menu.")
        vr.redirect("/voice")
        return twxml(vr)

    say(vr, "Je n'ai pas compris. Revenons au menu.")
    vr.redirect("/voice")
    return twxml(vr)

# --- Flux réservation (sans jamais proposer de créneaux) ---
@app.route("/resa", methods=["GET","POST"])
def resa():
    vr = VoiceResponse()
    call_sid = request.form.get("CallSid","default")
    state = st(call_sid)["resa"]
    step = request.args.get("step","people")

    if step == "people":
        g = Gather(input="speech dtmf", num_digits=2, speech_timeout="auto", language=LANG,
                   action="/resa?step=people_captured", method="POST")
        say(g, "Pour combien de personnes ?")
        vr.append(g)
        say(vr, "Je n'ai pas reçu. Revenons au début de la réservation.")
        vr.redirect("/resa?step=people")
        return twxml(vr)

    if step == "people_captured":
        digits = request.form.get("Digits")
        speech = (request.form.get("SpeechResult") or "")
        val = int(digits) if digits and digits.isdigit() else parse_people(speech)
        if not val:
            vr.redirect("/resa?step=people"); return twxml(vr)
        state["people"] = val
        vr.redirect("/resa?step=date"); return twxml(vr)

    if step == "date":
        g = Gather(input="speech", speech_timeout="auto", language=LANG,
                   action="/resa?step=date_captured", method="POST")
        say(g, "Pour quel jour souhaitez-vous réserver ?")
        vr.append(g)
        say(vr, "Je n'ai pas reçu. Reprenons.")
        vr.redirect("/resa?step=date")
        return twxml(vr)

    if step == "date_captured":
        speech = (request.form.get("SpeechResult") or "")
        val = parse_date(speech)
        if not val:
            vr.redirect("/resa?step=date"); return twxml(vr)
        state["date"] = val
        vr.redirect("/resa?step=time"); return twxml(vr)

    if step == "time":
        g = Gather(input="speech dtmf", num_digits=4, speech_timeout="auto", language=LANG,
                   action="/resa?step=time_captured", method="POST")
        say(g, "À quelle heure souhaitez-vous réserver ?")
        vr.append(g)
        say(vr, "Je n'ai pas reçu. Reprenons.")
        vr.redirect("/resa?step=time")
        return twxml(vr)

    if step == "time_captured":
        digits = request.form.get("Digits")
        speech = (request.form.get("SpeechResult") or "")
        val = None
        if digits and (3 <= len(digits) <= 4):
            val = digits if len(digits)==4 else f"{digits[0]}{digits[1:]}0"
            val = f"{val[:2]}:{val[2:]}"
        else:
            val = parse_time(speech)
        if not val:
            vr.redirect("/resa?step=time"); return twxml(vr)
        state["time"] = val
        vr.redirect("/resa?step=name"); return twxml(vr)

    if step == "name":
        g = Gather(input="speech", speech_timeout="auto", language=LANG,
                   action="/resa?step=name_captured", method="POST")
        say(g, "À quel nom dois-je enregistrer la réservation ?")
        vr.append(g)
        say(vr, "Je n'ai pas reçu. Reprenons.")
        vr.redirect("/resa?step=name")
        return twxml(vr)

    if step == "name_captured":
        name = (request.form.get("SpeechResult") or "").strip()
        if not name:
            vr.redirect("/resa?step=name"); return twxml(vr)
        state["name"] = name
        vr.redirect("/resa?step=phone"); return twxml(vr)

    if step == "phone":
        g = Gather(input="speech dtmf", num_digits=12, speech_timeout="auto", language=LANG,
                   action="/resa?step=phone_captured", method="POST")
        say(g, "Quel numéro de téléphone pour confirmer ? Dites-le ou composez-le.")
        vr.append(g)
        say(vr, "Je n'ai pas reçu. Reprenons.")
        vr.redirect("/resa?step=phone")
        return twxml(vr)

    if step == "phone_captured":
        speech = (request.form.get("SpeechResult") or "")
        digits = request.form.get("Digits")
        phone = re.sub(r"\D","", digits or speech)
        if len(phone) < 6:
            vr.redirect("/resa?step=phone"); return twxml(vr)
        state["phone"] = phone
        # Récap final (sans proposer d'options)
        say(vr, f"Parfait. Réservation notée pour {state['people']} personnes, le {state['date']} à {state['time']}, au nom de {state['name']}.")
        say(vr, f"Nous vous recontacterons au {state['phone']} si nécessaire. Merci et à bientôt !")
        vr.hangup()
        return twxml(vr)

    # fallback
    vr.redirect("/resa?step=people")
    return twxml(vr)

# --- Questions libres (FAQ riche), SANS suggestions ---
@app.route("/qa", methods=["POST"])
def qa():
    vr = VoiceResponse()
    text = (request.form.get("SpeechResult") or "").lower()

    def ans(msg):
        say(vr, msg); say(vr, "Puis-je vous aider avec autre chose ?")
        vr.redirect("/voice")

    if any(k in text for k in ["horaire","ouvert","heures","opening"]):
        ans(RESTAURANT["hours"]); return twxml(vr)
    if "adresse" in text or "où" in text or "ou se trouve" in text or "localis" in text:
        ans(f"{RESTAURANT['address']} {RESTAURANT['parking']}"); return twxml(vr)
    if "gluten" in text:
        ans(RESTAURANT["glutenfree"]); return twxml(vr)
    if "végétar" in text or "vegetar" in text or "vegan" in text:
        ans(RESTAURANT["vegetarian"]); return twxml(vr)
    if "allerg" in text:
        ans(RESTAURANT["allergens"]); return twxml(vr)
    if "prix" in text or "tarif" in text or "cher" in text:
        ans(RESTAURANT["price"]); return twxml(vr)
    if "parking" in text or "garer" in text:
        ans(RESTAURANT["parking"]); return twxml(vr)
    if "paiement" in text or "payer" in text or "carte" in text or "twint" in text:
        ans(RESTAURANT["payments"]); return twxml(vr)
    if "terrasse" in text:
        ans(RESTAURANT["terrace"]); return twxml(vr)
    if "chien" in text or "animal" in text or "pet" in text:
        ans(RESTAURANT["pets"]); return twxml(vr)
    if "enfant" in text or "bébé" in text or "bebe" in text or "poussette" in text:
        ans(RESTAURANT["kids"]); return twxml(vr)
    if "wifi" in text:
        ans(RESTAURANT["wifi"]); return twxml(vr)
    if "pmr" in text or "fauteuil" in text or "handicap" in text or "access" in text:
        ans(RESTAURANT["access"]); return twxml(vr)
    if "livrais" in text or "ubereats" in text or "smood" in text:
        ans(RESTAURANT["delivery"]); return twxml(vr)
    if "alcool" in text or "vin" in text or "bière" in text or "biere" in text or "cocktail" in text:
        ans(RESTAURANT["alcohol"]); return twxml(vr)
    if "contact" in text or "téléphone" in text or "mail" in text:
        ans(f"Vous pouvez nous joindre au {RESTAURANT['phone']}."); return twxml(vr)

    # par défaut
    say(vr, "Je n'ai pas bien compris. Pour réserver, appuyez sur 1. Pour les horaires, appuyez sur 2. Sinon, posez votre question après le bip.")
    g = Gather(input="speech", speech_timeout="auto", language=LANG, action="/qa", method="POST")
    say(g, "Je vous écoute.")
    vr.append(g)
    return twxml(vr)

@app.route("/health", methods=["GET"])
def health():
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
