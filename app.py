# app.py — Agent vocal FR (voix naturelle) + réservation fluide + Airtable robustifié

import os, re
from datetime import datetime, timedelta
from urllib.parse import quote, unquote
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from pyairtable import Table

app = Flask(__name__)

# ---------- Config ----------
LANG  = "fr-FR"
VOICE = "Polly.Celine"   # voix FR naturelle via Twilio <Say> (AWS Polly)

AIRTABLE_API_KEY   = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID   = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_RES = os.getenv("AIRTABLE_TABLE_RES", "Reservations")
AIRTABLE_TABLE_CAP = os.getenv("AIRTABLE_TABLE_CAP", "Capacity")
DEFAULT_CAPACITY   = int(os.getenv("DEFAULT_CAPACITY", "40"))

# ---------- Infos démo resto ----------
RESTAURANT = {
    "name": "La Truffe d'Or",
    "address": "Rue du Marché 24, 1003 Lausanne",
    "phone_public": "+41 21 555 12 34",
    "email": "contact@latruffedor.example",
    "hours": "Mardi à dimanche, 11h30–14h30 et 18h30–22h30. Fermé le lundi.",
    "parking": "Parkings St-François et Rôtillon à proximité.",
    "payment": "Cartes (Visa, MC, Amex), Twint et espèces.",
    "terrace": "Terrasse dès les beaux jours.",
    "access": "Accès PMR, chaise bébé disponible.",
    "delivery": "À emporter et livraison via partenaires.",
    "pets": "Chiens calmes acceptés en terrasse.",
}

# ---------- Voix & SSML ----------
def ssml(text: str) -> str:
    # Parle un peu plus lentement, micro-pauses pour son plus humain
    return f"""
<speak>
  <prosody rate="94%" pitch="+0%">
    {text}
  </prosody>
</speak>
""".strip()

def say_fr(vr: VoiceResponse, text: str):
    vr.say(ssml(text), language=LANG, voice=VOICE)

def xml(vr: VoiceResponse):
    return Response(str(vr), mimetype="text/xml")

def gather_speech(action_url: str, timeout="auto", hints: list[str] | None = None) -> Gather:
    return Gather(
        input="speech",
        language=LANG,
        speech_timeout=timeout,
        action=action_url,
        method="POST",
        hints=",".join(hints) if hints else None,
    )

# ---------- Airtable ----------
def tbl_res():
    if not (AIRTABLE_API_KEY and AIRTABLE_BASE_ID and AIRTABLE_TABLE_RES):
        raise RuntimeError("Airtable RES non configuré.")
    return Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_RES)

def tbl_cap():
    if not (AIRTABLE_API_KEY and AIRTABLE_BASE_ID and AIRTABLE_TABLE_CAP):
        raise RuntimeError("Airtable CAP non configuré.")
    return Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_CAP)

def save_reservation(payload: dict):
    print("[Airtable] create payload:", payload)
    rec = tbl_res().create(payload)
    print("[Airtable] Reservation saved:", rec.get("id"))
    return rec

def count_reservations(date_iso, time_hhmm):
    formula = f"AND({{Date}}='{date_iso}', {{Time}}='{time_hhmm}')"
    recs = tbl_res().all(formula=formula, fields=["People"])
    total = 0
    for r in recs:
        p = r["fields"].get("People")
        try:
            total += int(p)
        except:
            pass
    return total

def capacity_for(date_iso, time_hhmm):
    formula = f"AND({{Date}}='{date_iso}', {{Time}}='{time_hhmm}')"
    recs = tbl_cap().all(formula=formula, fields=["Capacity"])
    if recs:
        try:
            return int(recs[0]["fields"].get("Capacity"))
        except:
            return DEFAULT_CAPACITY
    return DEFAULT_CAPACITY

def has_capacity(people_int, date_iso, time_hhmm):
    cap = capacity_for(date_iso, time_hhmm)
    already = count_reservations(date_iso, time_hhmm)
    return (already + people_int) <= cap, cap, already

# ---------- Utils parsing & validation ----------
def to_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None

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
    m = re.search(r"\b(\d{1,2})[\/\-\.](\d{1,2})\b", t)  # jj/mm
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            return datetime(now.year, mo, d).strftime("%Y-%m-%d")
        except:
            return None
    days = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    for i,name in enumerate(days):
        if name in t:
            delta = (i - now.weekday()) % 7
            delta = 7 if delta == 0 else delta
            return (now + timedelta(days=delta)).strftime("%Y-%m-%d")
    return None

def parse_time_to_hhmm(text):
    """Respecte l'heure dite (pas de suggestion)."""
    if not text: return None
    t = text.lower().strip().replace("heures","h").replace("heure","h")
    t = re.sub(r"\s+", " ", t)

    m = re.search(r"\b(\d{1,2})\s*h\s*(\d{1,2})?\b", t)     # 19h / 19h30
    if m:
        hh = int(m.group(1)); mm = int(m.group(2)) if m.group(2) else 0
        if 0 <= hh <= 23 and 0 <= mm <= 59: return f"{hh:02d}:{mm:02d}"

    m = re.search(r"\b(\d{1,2})\s*[: ]\s*(\d{2})\b", t)     # 19:30 / 19 30
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59: return f"{hh:02d}:{mm:02d}"

    m = re.search(r"\b(\d{3,4})\b", t)                      # 1930 / 730
    if m:
        raw = m.group(1)
        if len(raw) == 4: hh, mm = int(raw[:2]), int(raw[2:])
        else:             hh, mm = int(raw[0]),  int(raw[1:])
        if 0 <= hh <= 23 and 0 <= mm <= 59: return f"{hh:02d}:{mm:02d}"

    m = re.search(r"\b(\d{1,2})\b", t)                      # 19 -> 19:00
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23: return f"{hh:02d}:00"
    return None

# ---------- FAQ ----------
def faq_answer(speech):
    t = (speech or "").lower()
    if any(k in t for k in ["horaire","ouvert"]):         return RESTAURANT["hours"]
    if any(k in t for k in ["adresse","où","ou se trouve","ou êtes","ou etes"]):
        return f"{RESTAURANT['address']}. {RESTAURANT['parking']}"
    if "parking" in t:                                    return RESTAURANT["parking"]
    if any(k in t for k in ["payer","paiement","carte","twint","espèces","especes"]):
        return RESTAURANT["payment"]
    if "terrasse" in t:                                   return RESTAURANT["terrace"]
    if any(k in t for k in ["emporter","livraison","delivery","take away"]):
        return RESTAURANT["delivery"]
    if any(k in t for k in ["accessible","pmr","fauteuil"]): return RESTAURANT["access"]
    if any(k in t for k in ["enfant","bebe","bébé","chaise haute"]):
        return "Nous accueillons les enfants et avons des chaises hautes."
    if any(k in t for k in ["chien","animal"]):           return RESTAURANT["pets"]
    if "gluten" in t:
        return "Nous avons des options sans gluten ; signalez toute allergie."
    if any(k in t for k in ["végétar","vegetar"]):
        return "Options végétariennes disponibles."
    if any(k in t for k in ["végan","vegan","végane","vegane"]):
        return "Options véganes : linguine tomate basilic, légumes rôtis."
    if any(k in t for k in ["allerg","arachide","lactose","noix","crustacé","crustace"]):
        return "Nous indiquons les allergènes principaux ; signalez toujours vos allergies."
    if any(k in t for k in ["anniversaire","bougie","gâteau","gateau"]):
        return "Pour un anniversaire, nous ajoutons bougies et message si souhaité."
    if any(k in t for k in ["contact","email","mail","téléphone","telephone"]):
        return f"Contact : {RESTAURANT['phone_public']} ou {RESTAURANT['email']}."
    return None

# ---------- Accueil & routage ----------
@app.route("/voice", methods=["POST"])
def voice():
    vr = VoiceResponse()
    g = gather_speech("/route")
    say_fr(g, f"Bonjour, {RESTAURANT['name']} à l'appareil. Que puis-je faire pour vous ?")
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu. Merci et à bientôt.")
    vr.hangup()
    return xml(vr)

@app.route("/route", methods=["POST"])
def route():
    vr = VoiceResponse()
    speech = (request.form.get("SpeechResult") or "").strip()
    print("[ROUTE] Heard:", speech)

    # FAQ directe
    ans = faq_answer(speech)
    if ans:
        say_fr(vr, ans); vr.hangup(); return xml(vr)

    # Intention réservation
    t = speech.lower()
    if any(k in t for k in ["réserv", "reser", "table"]):
        vr.redirect("/resa?step=people"); return xml(vr)

    say_fr(vr, "Je peux vous aider pour une réservation ou répondre à vos questions.")
    vr.redirect("/voice"); return xml(vr)

# ---------- Réservation (people → date → time → name → notes) ----------
@app.route("/resa", methods=["POST"])
def resa():
    vr      = VoiceResponse()
    step    = request.args.get("step", "people")
    speech  = (request.form.get("SpeechResult") or "").strip()
    from_n  = request.form.get("From") or ""
    print(f"[RESA] step={step} speech='{speech}' from={from_n} q={dict(request.args)}")

    # 1) NOMBRE DE PERSONNES
    if step == "people":
        if not speech:
            g = gather_speech("/resa?step=people")
            say_fr(g, "Pour combien de personnes ?")
            vr.append(g); return xml(vr)
        people = parse_people(speech)
        if not people or people <= 0:
            say_fr(vr, "Désolé, je n'ai pas compris. Dites un nombre, par exemple deux ou trois.")
            vr.redirect("/resa?step=people"); return xml(vr)
        vr.redirect(f"/resa?step=date&people={people}"); return xml(vr)

    # 2) DATE
    if step == "date":
        people = request.args.get("people")
        if not speech:
            g = gather_speech(f"/resa?step=date&people={people}")
            say_fr(g, "Quel jour souhaitez-vous venir ?")
            vr.append(g); return xml(vr)
        date_iso = parse_date(speech)
        if not date_iso:
            say_fr(vr, "Je n'ai pas saisi le jour. Pouvez-vous répéter ?")
            vr.redirect(f"/resa?step=date&people={people}"); return xml(vr)
        vr.redirect(f"/resa?step=time&people={people}&date={date_iso}"); return xml(vr)

    # 3) HEURE
    if step == "time":
        people = request.args.get("people")
        date_iso = request.args.get("date")
        if not speech:
            g = gather_speech(f"/resa?step=time&people={people}&date={date_iso}")
            say_fr(g, "À quelle heure ?")
            vr.append(g); return xml(vr)
        time_hhmm = parse_time_to_hhmm(speech)
        if not time_hhmm:
            say_fr(vr, "Je n'ai pas saisi l'heure. Dites par exemple dix-neuf ou dix-neuf trente.")
            vr.redirect(f"/resa?step=time&people={people}&date={date_iso}"); return xml(vr)

        people_int = to_int(people)
        if people_int is None:
            say_fr(vr, "Je reprends le nombre de personnes.")
            vr.redirect("/resa?step=people"); return xml(vr)

        ok, cap, already = has_capacity(people_int, date_iso, time_hhmm)
        print(f"[CAP] {date_iso} {time_hhmm} cap={cap} already={already} ask={people_int} ok={ok}")
        if not ok:
            remaining = max(0, cap - already)
            say_fr(vr, f"Désolé, il ne reste que {remaining} place(s) à cette heure. Voulez-vous un autre horaire ?")
            vr.redirect(f"/resa?step=time&people={people_int}&date={date_iso}"); return xml(vr)

        # On passe au flux NOM séparé. On encode le "retour" pour garder tous les paramètres.
        ret = quote(f"/resa?step=notes&people={people_int}&date={date_iso}&time={time_hhmm}", safe="")
        vr.redirect(f"/name/start?return={ret}")
        return xml(vr)

    # 5) NOTES & ENREGISTREMENT
    if step == "notes":
        people   = request.args.get("people")
        date_iso = request.args.get("date")
        time_hhmm= request.args.get("time")
        name     = request.args.get("name")  # injecté par /name/confirm
        notes    = speech

        people_int = to_int(people)
        if people_int is None or not date_iso or not time_hhmm or not name:
            say_fr(vr, "Je n’ai pas bien noté toutes les informations. Reprenons.")
            vr.redirect("/resa?step=people"); return xml(vr)

        payload = {
            "Name": name.strip().title(),
            "Phone": from_n,
            "People": people_int,
            "Date": date_iso,
            "Time": time_hhmm,
            "Notes": (notes or "").strip()
        }

        try:
            save_reservation(payload)
            say_fr(vr, f"Parfait {payload['Name']}. J'enregistre : {people_int} personne(s) le {date_iso} à {time_hhmm}.")
            if payload["Notes"]:
                say_fr(vr, f"J'ai noté : {payload['Notes']}.")
            say_fr(vr, "C'est confirmé. Merci et à bientôt.")
        except Exception as e:
            print("[ERR] Airtable save error:", repr(e))
            say_fr(vr, "Désolé, un souci technique est survenu pendant l'enregistrement. Merci de réessayer.")
        vr.hangup(); return xml(vr)

    # fallback
    vr.redirect("/resa?step=people"); return xml(vr)

# ---------- Flux NOM (confirmation simple + épellation optionnelle) ----------
@app.route("/name/start", methods=["POST"])
def name_start():
    vr = VoiceResponse()
    ret = request.args.get("return") or quote("/resa?step=people", safe="")
    g = gather_speech(f"/name/check?return={ret}")
    say_fr(g, "À quel nom dois-je enregistrer la réservation ?")
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu. Réessayons.")
    vr.redirect(f"/name/start?return={ret}")
    return xml(vr)

@app.route("/name/check", methods=["POST"])
def name_check():
    vr = VoiceResponse()
    ret = request.args.get("return") or quote("/resa?step=people", safe="")
    candidate = (request.form.get("SpeechResult") or "").strip().title()
    if not candidate:
        vr.redirect(f"/name/start?return={ret}"); return xml(vr)

    g = gather_speech(f"/name/confirm?candidate={quote(candidate, safe='')}&return={ret}")
    say_fr(g, f"J'ai compris {candidate}. Est-ce correct ? Dites oui ou non.")
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu.")
    vr.redirect(f"/name/confirm?candidate={quote(candidate, safe='')}&return={ret}")
    return xml(vr)

@app.route("/name/confirm", methods=["POST"])
def name_confirm():
    vr = VoiceResponse()
    ret = request.args.get("return") or quote("/resa?step=people", safe="")
    candidate = unquote(request.args.get("candidate", "")).strip().title()
    ans = (request.form.get("SpeechResult") or "").lower()

    if any(w in ans for w in ["oui","c'est bon","exact","correct","oui c'est ça","oui c est ca","oui c'est ca"]):
        base = unquote(ret)
        sep = "&" if "?" in base else "?"
        vr.redirect(f"{base}{sep}name={quote(candidate, safe='')}"); return xml(vr)

    if any(w in ans for w in ["non","pas","incorrect","faux"]):
        g = gather_speech(f"/name/spell?return={ret}")
        say_fr(g, "D'accord. Pouvez-vous épeler votre prénom, lettre par lettre ?")
        vr.append(g)
        say_fr(vr, "Je n'ai pas entendu.")
        vr.redirect(f"/name/spell?return={ret}")
        return xml(vr)

    # si pas compris, reposer la question
    vr.redirect(f"/name/check?return={ret}")
    return xml(vr)

@app.route("/name/spell", methods=["POST"])
def name_spell():
    vr = VoiceResponse()
    ret = request.args.get("return") or quote("/resa?step=people", safe="")
    spelled = (request.form.get("SpeechResult") or "").strip()

    # simplification : garder lettres et espaces, capitaliser
    t = re.sub(r"[^a-zA-ZÀ-ÖØ-öø-ÿ\s\-']", "", spelled)
    candidate = " ".join(part.capitalize() for part in t.split()).strip()
    if not candidate:
        g = gather_speech(f"/name/spell?return={ret}")
        say_fr(g, "Je n'ai pas compris. Épelez votre prénom, par exemple : T I M.")
        vr.append(g)
        say_fr(vr, "Je n'ai pas entendu.")
        vr.redirect(f"/name/spell?return={ret}")
        return xml(vr)

    base = unquote(ret)
    sep = "&" if "?" in base else "?"
    vr.redirect(f"{base}{sep}name={quote(candidate, safe='')}"); return xml(vr)

# ---------- Santé & ping ----------
@app.route("/health", methods=["GET"])
def health():
    return {"ok": True}

@app.route("/airtable-ping", methods=["GET"])
def airtable_ping():
    try:
        rec = tbl_res().create({
            "Name": "Ping Test",
            "Phone": "+41000000000",
            "People": 2,
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "Time": "19:30",
            "Notes": "ping"
        })
        print("[Airtable] Ping record:", rec.get("id"))
        return {"ok": True, "id": rec.get("id")}
    except Exception as e:
        print("[ERR] Airtable ping error:", repr(e))
        return {"ok": False, "error": str(e)}, 500

if __name__ == "__main__":
    # pour Render (lit $PORT), ou local 5000
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
