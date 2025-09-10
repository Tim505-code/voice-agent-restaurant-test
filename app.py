# app.py — Agent vocal FR (voix naturelle + prénoms) + Airtable

import os, re
from datetime import datetime, timedelta
from urllib.parse import quote, unquote
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from pyairtable import Table

app = Flask(__name__)

# ---------- Config ----------
LANG  = "fr-FR"
VOICE = "Polly.Celine"   # voix FR plus naturelle (AWS Polly via Twilio)

AIRTABLE_API_KEY   = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID   = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_RES = os.getenv("AIRTABLE_TABLE_RES", "Reservations")
AIRTABLE_TABLE_CAP = os.getenv("AIRTABLE_TABLE_CAP", "Capacity")
DEFAULT_CAPACITY   = int(os.getenv("DEFAULT_CAPACITY", "40"))

# Faux resto (démo)
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

# ---------- Utilitaires voix/XML ----------
def say_fr(vr, text): vr.say(text, language=LANG, voice=VOICE)
def xml(vr): return Response(str(vr), mimetype="text/xml")

# ---------- Airtable ----------
def tbl_res(): 
    if not (AIRTABLE_API_KEY and AIRTABLE_BASE_ID and AIRTABLE_TABLE_RES):
        raise RuntimeError("Airtable RES non configuré.")
    return Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_RES)

def tbl_cap():
    if not (AIRTABLE_API_KEY and AIRTABLE_BASE_ID and AIRTABLE_TABLE_CAP):
        raise RuntimeError("Airtable CAP non configuré.")
    return Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_CAP)

def count_reservations(date_iso, time_hhmm):
    formula = f"AND({{Date}}='{date_iso}', {{Time}}='{time_hhmm}')"
    recs = tbl_res().all(formula=formula, fields=["People"])
    total = 0
    for r in recs:
        p = r["fields"].get("People")
        try: total += int(p)
        except: pass
    return total

def capacity_for(date_iso, time_hhmm):
    formula = f"AND({{Date}}='{date_iso}', {{Time}}='{time_hhmm}')"
    recs = tbl_cap().all(formula=formula, fields=["Capacity"])
    if recs:
        try: return int(recs[0]["fields"].get("Capacity"))
        except: return DEFAULT_CAPACITY
    return DEFAULT_CAPACITY

def has_capacity(people, date_iso, time_hhmm):
    cap = capacity_for(date_iso, time_hhmm)
    already = count_reservations(date_iso, time_hhmm)
    return (already + int(people)) <= cap, cap, already

def save_reservation(name, phone, people, date_iso, time_hhmm, notes):
    payload = {
        "Name": name,
        "Phone": phone or "",
        "People": int(people),
        "Date": date_iso,     # YYYY-MM-DD
        "Time": time_hhmm,    # HH:MM
        "Notes": notes or ""
    }
    print("[Airtable] create payload:", payload)
    rec = tbl_res().create(payload)
    print("[Airtable] Reservation saved:", rec.get("id"))
    return rec

# ---------- Parsing FR ----------
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
    # jour de semaine (prochain)
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

    m = re.search(r"\b(\d{1,2})\s*h\s*(\d{1,2})?\b", t)     # 19h / 19h30 / 19 h 30
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
    if any(k in t for k in ["adresse","où","ou se trouve","ou êtes"]):
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
        return "Nous avons des options sans gluten, signalez toute allergie."
    if any(k in t for k in ["végétar","vegetar"]):
        return "Options végétariennes disponibles."
    if any(k in t for k in ["végan","vegan","végane","vegane"]):
        return "Options véganes : linguine tomate basilic, légumes rôtis."
    if any(k in t for k in ["allerg","arachide","lactose","noix","crustacé","crustace"]):
        return "Nous indiquons les allergènes principaux. Signalez vos allergies."
    if any(k in t for k in ["anniversaire","bougie","gâteau","gateau"]):
        return "Pour un anniversaire, nous pouvons ajouter bougies et message."
    if any(k in t for k in ["contact","email","mail","téléphone","telephone"]):
        return f"Contact : {RESTAURANT['phone_public']} ou {RESTAURANT['email']}."
    return None

# ---------- Accueil ----------
@app.route("/voice", methods=["POST"])
def voice():
    vr = VoiceResponse()
    g = Gather(input="speech", speech_timeout="auto", language=LANG, action="/route", method="POST")
    say_fr(g, f"Bonjour, {RESTAURANT['name']} à l'appareil. Que puis-je faire pour vous ?")
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu. Merci, au revoir.")
    vr.hangup()
    return xml(vr)

@app.route("/route", methods=["POST"])
def route():
    vr = VoiceResponse()
    speech = request.form.get("SpeechResult") or ""
    print("[ROUTE] Heard:", speech)

    msg = faq_answer(speech)
    if msg:
        say_fr(vr, msg); vr.hangup(); return xml(vr)

    t = speech.lower()
    if any(k in t for k in ["réserv", "reser", "table"]):
        vr.redirect("/resa?step=people"); return xml(vr)

    say_fr(vr, "Je peux vous aider pour une réservation ou répondre à vos questions.")
    vr.redirect("/voice"); return xml(vr)

# ---------- Réservation ----------
@app.route("/resa", methods=["POST"])
def resa():
    vr = VoiceResponse()
    step   = request.args.get("step", "people")
    speech = request.form.get("SpeechResult") or ""
    from_n = request.form.get("From") or ""
    print(f"[RESA] step={step} heard='{speech}' from={from_n}")

    # 1) personnes
    if step == "people":
        if not speech.strip():
            g = Gather(input="speech", speech_timeout="auto", language=LANG, action="/resa?step=people", method="POST")
            say_fr(g, "Pour combien de personnes ?")
            vr.append(g); return xml(vr)
        people = parse_people(speech)
        if not people:
            say_fr(vr, "Désolé, je n'ai pas compris. Dites un nombre, par exemple deux ou trois.")
            vr.redirect("/resa?step=people"); return xml(vr)
        vr.redirect(f"/resa?step=date&people={people}"); return xml(vr)

    # 2) date
    if step == "date":
        people = request.args.get("people")
        if not speech.strip():
            g = Gather(input="speech", speech_timeout="auto", language=LANG, action=f"/resa?step=date&people={people}", method="POST")
            say_fr(g, "Quel jour souhaitez-vous venir ?")
            vr.append(g); return xml(vr)
        date_iso = parse_date(speech)
        if not date_iso:
            say_fr(vr, "Je n'ai pas saisi le jour. Pouvez-vous répéter ?")
            vr.redirect(f"/resa?step=date&people={people}"); return xml(vr)
        vr.redirect(f"/resa?step=time&people={people}&date={date_iso}"); return xml(vr)

    # 3) heure
    if step == "time":
        people = request.args.get("people")
        date_iso = request.args.get("date")
        if not speech.strip():
            g = Gather(input="speech", speech_timeout="auto", language=LANG, action=f"/resa?step=time&people={people}&date={date_iso}", method="POST")
            say_fr(g, "À quelle heure ?")
            vr.append(g); return xml(vr)
        time_hhmm = parse_time_to_hhmm(speech)
        if not time_hhmm:
            say_fr(vr, "Je n'ai pas saisi l'heure. Pouvez-vous redire l'heure simplement ?")
            vr.redirect(f"/resa?step=time&people={people}&date={date_iso}"); return xml(vr)

        ok, cap, already = has_capacity(people, date_iso, time_hhmm)
        print(f"[CAP] {date_iso} {time_hhmm} cap={cap} already={already} ask={people} ok={ok}")
        if not ok:
            remaining = max(0, cap - already)
            say_fr(vr, f"Désolé, il ne reste que {remaining} place(s) à cette heure. Souhaitez-vous un autre horaire ?")
            vr.redirect(f"/resa?step=time&people={people}&date={date_iso}")
            return xml(vr)

        # Va au flux "nom" dédié (avec routes séparées, pour éviter 404 Twilio)
        ret = quote(f"/resa?step=notes&people={people}&date={date_iso}&time={time_hhmm}")
        vr.redirect(f"/name/start?return={ret}")
        return xml(vr)

    # 5) notes & enregistrement — (appelé après /name/*)
    if step == "notes":
        people   = request.args.get("people")
        date_iso = request.args.get("date")
        time_hhmm= request.args.get("time")
        name     = request.args.get("name")  # fourni par /name/confirm
        notes    = speech.strip()
        from_n   = request.form.get("From") or ""

        if not name:
            # sécurité : si pas de nom, relancer flux nom
            ret = quote(f"/resa?step=notes&people={people}&date={date_iso}&time={time_hhmm}")
            vr.redirect(f"/name/start?return={ret}")
            return xml(vr)

        try:
            rec = save_reservation(name=name, phone=from_n, people=people, date_iso=date_iso, time_hhmm=time_hhmm, notes=notes)
            say_fr(vr, f"Parfait {name}. J'enregistre : {people} personne(s) le {date_iso} à {time_hhmm}.")
            if notes: say_fr(vr, f"J'ai noté : {notes}.")
            say_fr(vr, "C'est confirmé. Merci et à bientôt.")
            print("[OK] Reservation confirmed:", rec.get("id"))
        except Exception as e:
            print("[ERR] Airtable save error:", repr(e))
            say_fr(vr, "Désolé, un souci technique est survenu. Merci de réessayer dans un instant.")
        vr.hangup(); return xml(vr)

    # fallback
    vr.redirect("/resa?step=people"); return xml(vr)

# ---------- Flux prénom/nom (4 routes) ----------
# /name/start : demande le nom (ou relance)
@app.route("/name/start", methods=["POST"])
def name_start():
    vr = VoiceResponse()
    ret = request.args.get("return") or quote("/voice")
    g = Gather(input="speech", speech_timeout="auto", language=LANG,
               action=f"/name/check?return={ret}", method="POST")
    say_fr(g, "À quel nom dois-je enregistrer la réservation ?")
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu. Réessayons.")
    vr.redirect(f"/name/start?return={ret}")
    return xml(vr)

# /name/check : reçoit le nom entendu et va à la confirmation
@app.route("/name/check", methods=["POST"])
def name_check():
    vr = VoiceResponse()
    ret = request.args.get("return") or quote("/voice")
    # Si Twilio reposte avec un param candidate dans l'URL (log que tu as vu), on l’utilise.
    candidate = request.args.get("candidate")
    if not candidate:
        candidate = (request.form.get("SpeechResult") or "").strip()
    candidate = candidate.title()
    if not candidate:
        vr.redirect(f"/name/start?return={ret}")
        return xml(vr)
    vr.redirect(f"/name/confirm?candidate={quote(candidate)}&return={ret}")
    return xml(vr)

# /name/confirm : “J’ai compris X, c’est bien ça ?”
@app.route("/name/confirm", methods=["POST"])
def name_confirm():
    vr = VoiceResponse()
    ret = request.args.get("return") or quote("/voice")
    candidate = unquote(request.args.get("candidate", "")).strip().title()

    last = (request.form.get("SpeechResult") or "").lower()
    if last:
        if any(w in last for w in ["oui","c'est bon","exact","correct"]):
            # renvoyer vers /resa?step=notes ... en ajoutant name=...
            # ret est une URL encodée : on ajoute &name=
            if "?" in unquote(ret): final = unquote(ret) + f"&name={quote(candidate)}"
            else:                   final = unquote(ret) + f"?name={quote(candidate)}"
            vr.redirect(final); return xml(vr)
        if any(w in last for w in ["non","pas","incorrect","faux"]):
            vr.redirect(f"/name/spell?return={ret}")
            return xml(vr)

    g = Gather(input="speech", speech_timeout="auto", language=LANG,
               action=f"/name/confirm?candidate={quote(candidate)}&return={ret}",
               method="POST")
    say_fr(g, f"J'ai compris {candidate}. Est-ce correct ? Dites oui ou non.")
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu.")
    vr.redirect(f"/name/confirm?candidate={quote(candidate)}&return={ret}")
    return xml(vr)

# /name/spell : épellation
@app.route("/name/spell", methods=["POST"])
def name_spell():
    vr = VoiceResponse()
    ret = request.args.get("return") or quote("/voice")
    last = (request.form.get("SpeechResult") or "").strip()

    # Si on vient d'entendre une épellation, on nettoie : on supprime “tiré”, espaces, etc.
    if last:
        t = last.lower()
        # remplace “tiré/trait d’union/espace” par rien
        t = t.replace("tiré", "").replace("tiret", "").replace("trait d'union","")
        t = t.replace("espace"," ").replace("-", " ")
        # enlève “lettre …” éventuels
        t = re.sub(r"lettre\s+", "", t)
        # supprime tout ce qui n’est pas lettre/space
        t = re.sub(r"[^a-zàâäéèêëîïôöùûüç\s\-']", "", t)
        # compact
        t = " ".join(part.capitalize() for part in t.split())
        if t:
            vr.redirect(f"/name/confirm?candidate={quote(t)}&return={ret}")
            return xml(vr)

    g = Gather(input="speech", speech_timeout="auto", language=LANG,
               action=f"/name/spell?return={ret}", method="POST")
    say_fr(g, "Pouvez-vous épeler votre nom, lettre par lettre ?")
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu.")
    vr.redirect(f"/name/spell?return={ret}")
    return xml(vr)

# ---------- Santé & test ----------
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
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
