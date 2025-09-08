# app.py — Agent vocal complet (Twilio + Airtable) — FR

import os, re
from datetime import datetime, timedelta
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from pyairtable import Table

app = Flask(__name__)

# ----- Config -----
LANG = "fr-FR"
VOICE = "alice"

AIRTABLE_API_KEY  = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID  = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_RES = os.getenv("AIRTABLE_TABLE_RES", "Reservations")
AIRTABLE_TABLE_CAP = os.getenv("AIRTABLE_TABLE_CAP", "Capacity")
DEFAULT_CAPACITY   = int(os.getenv("DEFAULT_CAPACITY", "40"))

# ----- Faux restaurant (données démo) -----
RESTAURANT = {
    "name": "La Truffe d'Or",
    "address": "Rue du Marché 24, 1003 Lausanne",
    "phone_public": "+41 21 555 12 34",
    "email": "contact@latruffedor.example",
    "hours": "Mardi–Dimanche : 11h30–14h30 et 18h30–22h30. Fermé le lundi.",
    "parking": "Parkings St-François et Rôtillon à 4 min à pied.",
    "payment": "Cartes (Visa, MC, Amex), Twint et espèces.",
    "terrace": "Terrasse ouverte quand il fait beau (18 places).",
    "access": "Accès PMR, chaise bébé disponible.",
    "delivery": "Livraison via partenaires locaux, à emporter possible.",
    "pets": "Chiens calmes acceptés en terrasse.",
}

MENU = {
    "gluten_free": ["Risotto aux champignons (GF)", "Filet de dorade, légumes vapeur (GF)"],
    "vegetarian":  ["Raviolis ricotta-épinards", "Risotto aux asperges", "Salade burrata"],
    "vegan":       ["Linguine sauce tomate basilic (V)", "Légumes rôtis et quinoa (V)"],
    "allergens_info": "Nous indiquons gluten, lactose, fruits à coque et crustacés. Signalez toute allergie à la commande."
}

# ----- Helpers voix/XML -----
def say_fr(vr, text): vr.say(text, language=LANG, voice=VOICE)
def xml(vr): return Response(str(vr), mimetype="text/xml")

# ----- Airtable helpers -----
def table_res(): 
    if not (AIRTABLE_API_KEY and AIRTABLE_BASE_ID and AIRTABLE_TABLE_RES):
        raise RuntimeError("Airtable (RES) non configuré.")
    return Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_RES)

def table_cap():
    if not (AIRTABLE_API_KEY and AIRTABLE_BASE_ID and AIRTABLE_TABLE_CAP):
        raise RuntimeError("Airtable (CAP) non configuré.")
    return Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_CAP)

def count_existing_reservations(date_iso, time_hhmm):
    # Filtre simple : champs Date (YYYY-MM-DD) ET Time (HH:MM)
    # Airtable formule (égalité stricte)
    formula = f"AND({{Date}}='{date_iso}', {{Time}}='{time_hhmm}')"
    recs = table_res().all(formula=formula, fields=["People"])
    total = 0
    for r in recs:
        p = r["fields"].get("People")
        try: total += int(p)
        except: pass
    return total

def get_capacity_for_slot(date_iso, time_hhmm):
    # Cherche une ligne en table Capacity
    formula = f"AND({{Date}}='{date_iso}', {{Time}}='{time_hhmm}')"
    recs = table_cap().all(formula=formula, fields=["Capacity"])
    if recs:
        cap = recs[0]["fields"].get("Capacity")
        try: return int(cap)
        except: return DEFAULT_CAPACITY
    return DEFAULT_CAPACITY

def has_capacity(people, date_iso, time_hhmm):
    cap = get_capacity_for_slot(date_iso, time_hhmm)
    already = count_existing_reservations(date_iso, time_hhmm)
    return (already + int(people)) <= cap, cap, already

def save_reservation(name, phone, people, date_iso, time_hhmm, notes):
    return table_res().create({
        "Name": name,
        "Phone": phone or "",
        "People": int(people),
        "Date": date_iso,     # YYYY-MM-DD
        "Time": time_hhmm,    # HH:MM (on respecte ce que le client dit)
        "Notes": notes or ""
    })

# ----- Parsing (FR) -----
def parse_people(text):
    if not text: return None
    m = re.search(r"\b(\d{1,2})\b", text)
    return int(m.group(1)) if m else None

def parse_date(text):
    if not text: return None
    t = text.lower()
    now = datetime.now()
    if "aujourd" in t: return now.strftime("%Y-%m-%d")
    if "demain" in t: return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    if "après-demain" in t or "apres-demain" in t: return (now + timedelta(days=2)).strftime("%Y-%m-%d")
    # jj/mm ou jj-mm
    m = re.search(r"\b(\d{1,2})[\/\-\.](\d{1,2})\b", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try: return datetime(now.year, mo, d).strftime("%Y-%m-%d")
        except: return None
    # mardi, mercredi, etc. (prochain)
    days = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    for i,name in enumerate(days):
        if name in t:
            delta = (i - now.weekday()) % 7
            delta = 7 if delta == 0 else delta
            return (now + timedelta(days=delta)).strftime("%Y-%m-%d")
    return None

def parse_time_to_hhmm(text):
    """
    Respecte l'heure dite :
      - "19h" / "19 heures" -> 19:00
      - "19h30" / "19 heures 30" -> 19:30
      - "20 15" / "20 heures 15" -> 20:15
      - "1930" / "19 30" -> 19:30
      - "7" -> 07:00
    """
    if not text: return None
    t = text.lower().strip()
    t = t.replace("heures", "h").replace("heure", "h")
    t = re.sub(r"\s+", " ", t)

    # 19h30 / 19 h 30 / 19h
    m = re.search(r"\b(\d{1,2})\s*h\s*(\d{1,2})?\b", t)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) else 0
        if 0 <= hh <= 23 and 0 <= mm <= 59: return f"{hh:02d}:{mm:02d}"

    # 19:30 / 19 30
    m = re.search(r"\b(\d{1,2})\s*[: ]\s*(\d{2})\b", t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59: return f"{hh:02d}:{mm:02d}"

    # 1930 / 730
    m = re.search(r"\b(\d{3,4})\b", t)
    if m:
        raw = m.group(1)
        if len(raw) == 4:
            hh, mm = int(raw[:2]), int(raw[2:])
        else:
            hh, mm = int(raw[0]), int(raw[1:])
        if 0 <= hh <= 23 and 0 <= mm <= 59: return f"{hh:02d}:{mm:02d}"

    # juste l'heure entière ("19")
    m = re.search(r"\b(\d{1,2})\b", t)
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23: return f"{hh:02d}:00"

    return None

# ----- Intent FAQ -----
def handle_faq(speech):
    t = (speech or "").lower()

    if any(k in t for k in ["horaire", "heures", "ouvert"]):
        return f"{RESTAURANT['hours']}"
    if any(k in t for k in ["adresse", "où", "ou se trouve", "ou êtes"]):
        return f"Notre adresse est {RESTAURANT['address']}. {RESTAURANT['parking']}"
    if any(k in t for k in ["parking", "se garer"]):
        return RESTAURANT["parking"]
    if any(k in t for k in ["paie", "payer", "paiement", "carte", "twint", "espèces", "especes"]):
        return RESTAURANT["payment"]
    if any(k in t for k in ["terrasse"]):
        return RESTAURANT["terrace"]
    if any(k in t for k in ["livraison", "delivery", "emporter", "take away", "à emporter", "a emporter"]):
        return RESTAURANT["delivery"]
    if any(k in t for k in ["accessible", "fauteuil", "pmr"]):
        return RESTAURANT["access"]
    if any(k in t for k in ["enfant", "bebe", "bébé", "chaise haute"]):
        return "Nous accueillons volontiers les enfants et avons des chaises hautes."
    if any(k in t for k in ["chien", "animal"]):
        return RESTAURANT["pets"]

    # nourriture
    if "gluten" in t:
        return "Plats sans gluten disponibles : " + ", ".join(MENU["gluten_free"]) + ". " + MENU["allergens_info"]
    if any(k in t for k in ["végétar", "vegetar"]):
        return "Plats végétariens : " + ", ".join(MENU["vegetarian"]) + ". " + MENU["allergens_info"]
    if any(k in t for k in ["végan", "vegan", "végane", "vegane"]):
        return "Options véganes : " + ", ".join(MENU["vegan"]) + ". " + MENU["allergens_info"]
    if any(k in t for k in ["allerg", "arachide", "lactose", "noix", "fruit à coque", "crustacé", "crustace"]):
        return MENU["allergens_info"]

    if any(k in t for k in ["anniversaire", "bougie", "bougies", "gateau", "gâteau"]):
        return "Pour un anniversaire, nous pouvons ajouter des bougies et un message sur assiette. Dites-le lors de la réservation ou dans les notes."

    if any(k in t for k in ["contact", "email", "mail", "téléphone", "telephone"]):
        return f"Vous pouvez nous joindre au {RESTAURANT['phone_public']} ou par email à {RESTAURANT['email']}."

    return None

# ----- Webhooks -----
@app.route("/voice", methods=["POST"])
def voice():
    vr = VoiceResponse()
    g = Gather(input="speech", speech_timeout="auto", language=LANG, action="/route", method="POST")
    say_fr(g, f"Bienvenue au {RESTAURANT['name']}. Que puis-je faire pour vous ?")
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu. Au revoir.")
    vr.hangup()
    return xml(vr)

@app.route("/route", methods=["POST"])
def route():
    vr = VoiceResponse()
    speech = request.form.get("SpeechResult") or ""

    # FAQ d'abord
    msg = handle_faq(speech)
    if msg:
        say_fr(vr, msg); vr.hangup(); return xml(vr)

    # Réservation
    t = speech.lower()
    if any(k in t for k in ["réserv", "reser", "table"]):
        vr.redirect("/resa?step=people"); return xml(vr)

    say_fr(vr, "Désolé, je n'ai pas compris. Pour réserver, dites par exemple je veux réserver une table.")
    vr.redirect("/voice")
    return xml(vr)

@app.route("/resa", methods=["POST"])
def resa():
    vr = VoiceResponse()
    step = request.args.get("step", "people")
    speech = request.form.get("SpeechResult") or ""
    caller = request.form.get("From") or ""

    # 1) personnes
    if step == "people":
        if not speech.strip():
            g = Gather(input="speech", speech_timeout="auto", language=LANG, action="/resa?step=people", method="POST")
            say_fr(g, "Pour combien de personnes souhaitez-vous réserver ?")
            vr.append(g); return xml(vr)
        people = parse_people(speech)
        if not people:
            say_fr(vr, "Je n'ai pas compris. Dites un nombre, par exemple deux, trois ou quatre.")
            vr.redirect("/resa?step=people"); return xml(vr)
        vr.redirect(f"/resa?step=date&people={people}"); return xml(vr)

    # 2) date
    if step == "date":
        people = request.args.get("people")
        if not speech.strip():
            g = Gather(input="speech", speech_timeout="auto", language=LANG, action=f"/resa?step=date&people={people}", method="POST")
            say_fr(g, "Pour quel jour ? Dites aujourd'hui, demain, vendredi, ou donnez une date comme 12 slash 9.")
            vr.append(g); return xml(vr)
        date_iso = parse_date(speech)
        if not date_iso:
            say_fr(vr, "Je n'ai pas compris la date. Répétez s'il vous plaît.")
            vr.redirect(f"/resa?step=date&people={people}"); return xml(vr)
        vr.redirect(f"/resa?step=time&people={people}&date={date_iso}"); return xml(vr)

    # 3) heure
    if step == "time":
        people = request.args.get("people")
        date_iso = request.args.get("date")
        if not speech.strip():
            g = Gather(input="speech", speech_timeout="auto", language=LANG, action=f"/resa?step=time&people={people}&date={date_iso}", method="POST")
            say_fr(g, "A quelle heure ? Par exemple, 19h, 19h30 ou 20 heures 15.")
            vr.append(g); return xml(vr)
        time_hhmm = parse_time_to_hhmm(speech)
        if not time_hhmm:
            say_fr(vr, "Je n'ai pas saisi l'heure. Dites par exemple dix-neuf heures trente.")
            vr.redirect(f"/resa?step=time&people={people}&date={date_iso}"); return xml(vr)

        # Vérification capacité
        ok, cap, already = has_capacity(people, date_iso, time_hhmm)
        if not ok:
            remaining = max(0, cap - already)
            say_fr(vr, f"Désolé, il ne reste que {remaining} place(s) à {time_hhmm} le {date_iso}.")
            say_fr(vr, "Voulez-vous un autre horaire le même jour, par exemple 18h30 ou 20h ?")
            vr.redirect(f"/resa?step=time&people={people}&date={date_iso}")
            return xml(vr)

        vr.redirect(f"/resa?step=name&people={people}&date={date_iso}&time={time_hhmm}"); return xml(vr)

    # 4) nom
    if step == "name":
        people = request.args.get("people")
        date_iso = request.args.get("date")
        time_hhmm = request.args.get("time")
        if not speech.strip():
            g = Gather(input="speech", speech_timeout="auto", language=LANG,
                       action=f"/resa?step=name&people={people}&date={date_iso}&time={time_hhmm}", method="POST")
            say_fr(g, "À quel nom dois-je enregistrer la réservation ?")
            vr.append(g); return xml(vr)
        name = speech.strip().title()
        vr.redirect(f"/resa?step=notes&people={people}&date={date_iso}&time={time_hhmm}&name={name}"); return xml(vr)

    # 5) notes + enregistrement
    if step == "notes":
        people = request.args.get("people")
        date_iso = request.args.get("date")
        time_hhmm = request.args.get("time")
        name = request.args.get("name")
        notes = speech.strip()

        try:
            save_reservation(name=name, phone=caller, people=people, date_iso=date_iso, time_hhmm=time_hhmm, notes=notes)
            say_fr(vr, f"Parfait {name}. Table pour {people} personne(s) le {date_iso} à {time_hhmm}.")
            if notes:
                say_fr(vr, f"J'ai noté : {notes}.")
            say_fr(vr, f"Adresse : {RESTAURANT['address']}. {RESTAURANT['parking']}")
            say_fr(vr, "Merci et à bientôt.")
        except Exception as e:
            say_fr(vr, "Désolé, une erreur est survenue lors de l'enregistrement. Veuillez réessayer un peu plus tard.")
        vr.hangup(); return xml(vr)

    # fallback
    vr.redirect("/resa?step=people"); return xml(vr)

# ----- Santé & diagnostic -----
@app.route("/health", methods=["GET"])
def health(): return {"ok": True}

@app.route("/airtable-ping", methods=["GET"])
def airtable_ping():
    try:
        t = table_res()
        rec = t.create({
            "Name": "Ping Test",
            "Phone": "+41000000000",
            "People": 2,
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "Time": "19:30",
            "Notes": "ping"
        })
        return {"ok": True, "id": rec.get("id")}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
