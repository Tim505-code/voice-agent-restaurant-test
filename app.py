# app.py — Agent vocal FR (voix naturelle + prénoms confirmés/épellation) + Airtable + logs

import os, re
from datetime import datetime, timedelta
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from pyairtable import Table

app = Flask(__name__)

# ---------- Config ----------
LANG = "fr-FR"
VOICE = "Polly.Celine"  # essaie aussi Polly.Lea / Polly.Mathieu

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

# ---------- Helpers Voix / SSML ----------
def ssml(txt: str) -> str:
    return f"""
<speak>
  <prosody rate="95%" pitch="+0%">
    {txt}
  </prosody>
</speak>
""".strip()

def say_fr(vr: VoiceResponse, text: str):
    vr.say(ssml(text), language=LANG, voice=VOICE)

def xml(vr: VoiceResponse):
    return Response(str(vr), mimetype="text/xml")

def gather_speech(action_url: str, hints: list[str] | None = None, timeout="auto") -> Gather:
    return Gather(
        input="speech",
        action=action_url,
        method="POST",
        language=LANG,
        speech_timeout=timeout,
        hints=",".join(hints) if hints else None,
        # Si dispo sur ton compte Twilio :
        # enhanced=True,
        # speechModel="phone_call",
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

def has_capacity(people, date_iso, time_hhmm):
    cap = capacity_for(date_iso, time_hhmm)
    already = count_reservations(date_iso, time_hhmm)
    return (already + int(people)) <= cap, cap, already

def save_reservation(name, phone, people, date_iso, time_hhmm, notes):
    rec = tbl_res().create({
        "Name": name,
        "Phone": phone or "",
        "People": int(people),
        "Date": date_iso,     # YYYY-MM-DD
        "Time": time_hhmm,    # HH:MM
        "Notes": notes or ""
    })
    print("[Airtable] Reservation saved:", rec.get("id"))
    return rec

# ---------- Hints prénoms dynamiques (optionnel, petit set) ----------
def last_name_hints(max_items=50):
    try:
        names = set()
        for rec in tbl_res().all(fields=["Name"], max_records=200):
            n = (rec["fields"].get("Name") or "").strip()
            if 2 <= len(n) <= 20:
                names.add(n.split()[0].capitalize())
        base = ["Tim","Tom","Théo","Theo","Léo","Léa","Emma","Noah","Lucas","Marie","Sarah","Lina","Hugo"]
        out = list(names)[:max_items] + base
        seen, uniq = set(), []
        for x in out:
            if x not in seen:
                seen.add(x); uniq.append(x)
        return uniq[:max_items]
    except Exception:
        return ["Tim","Tom","Theo","Léo","Léa","Emma","Noah"]

# ---------- Parsing naturel ----------
def parse_people(text):
    if not text:
        return None
    m = re.search(r"\b(\d{1,2})\b", text)
    return int(m.group(1)) if m else None

def parse_date(text):
    if not text:
        return None
    t = text.lower()
    now = datetime.now()
    if "aujourd" in t:
        return now.strftime("%Y-%m-%d")
    if "demain" in t:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    if "après-demain" in t or "apres-demain" in t:
        return (now + timedelta(days=2)).strftime("%Y-%m-%d")
    m = re.search(r"\b(\d{1,2})[\/\-\.](\d{1,2})\b", t)  # jj/mm
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            return datetime(now.year, mo, d).strftime("%Y-%m-%d")
        except:
            return None
    days = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    for i, name in enumerate(days):
        if name in t:
            delta = (i - now.weekday()) % 7
            delta = 7 if delta == 0 else delta
            return (now + timedelta(days=delta)).strftime("%Y-%m-%d")
    return None

def parse_time_to_hhmm(text):
    """Respecte l'heure dite (pas de suggestion)."""
    if not text:
        return None
    t = text.lower().strip()
    t = t.replace("heures", "h").replace("heure", "h")
    t = re.sub(r"\s+", " ", t)

    m = re.search(r"\b(\d{1,2})\s*h\s*(\d{1,2})?\b", t)     # 19h / 19h30
    if m:
        hh = int(m.group(1)); mm = int(m.group(2)) if m.group(2) else 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"

    m = re.search(r"\b(\d{1,2})\s*[: ]\s*(\d{2})\b", t)     # 19:30 / 19 30
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"

    m = re.search(r"\b(\d{3,4})\b", t)                      # 1930 / 730
    if m:
        raw = m.group(1)
        if len(raw) == 4:
            hh, mm = int(raw[:2]), int(raw[2:])
        else:
            hh, mm = int(raw[0]),  int(raw[1:])
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"

    m = re.search(r"\b(\d{1,2})\b", t)                      # 19 -> 19:00
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23:
            return f"{hh:02d}:00"
    return None

# ---------- FAQ naturelle ----------
def faq_answer(speech):
    t = (speech or "").lower()
    if any(k in t for k in ["horaire","ouvert"]):
        return RESTAURANT["hours"]
    if any(k in t for k in ["adresse","où","ou se trouve","ou êtes","ou etes"]):
        return f"{RESTAURANT['address']}. {RESTAURANT['parking']}"
    if "parking" in t:
        return RESTAURANT["parking"]
    if any(k in t for k in ["payer","paiement","carte","twint","espèces","especes"]):
        return RESTAURANT["payment"]
    if "terrasse" in t:
        return RESTAURANT["terrace"]
    if any(k in t for k in ["emporter","livraison","delivery","take away"]):
        return RESTAURANT["delivery"]
    if any(k in t for k in ["accessible","pmr","fauteuil"]):
        return RESTAURANT["access"]
    if any(k in t for k in ["enfant","bebe","bébé","chaise haute"]):
        return "Nous accueillons les enfants et avons des chaises hautes."
    if any(k in t for k in ["chien","animal"]):
        return RESTAURANT["pets"]
    if "gluten" in t:
        return "Nous avons des options sans gluten comme risotto aux champignons et poisson vapeur. Signalez toute allergie."
    if any(k in t for k in ["végétar","vegetar"]):
        return "Options végétariennes disponibles (raviolis ricotta, risotto, salade burrata)."
    if any(k in t for k in ["végan","vegan","végane","vegane"]):
        return "Options véganes : linguine tomate basilic, légumes rôtis au quinoa."
    if any(k in t for k in ["allerg","arachide","lactose","noix","crustacé","crustace"]):
        return "Nous indiquons les allergènes (gluten, lactose, fruits à coque, crustacés). Signalez toujours vos allergies."
    if any(k in t for k in ["anniversaire","bougie","gâteau","gateau"]):
        return "Pour un anniversaire, nous pouvons ajouter bougies et message. Dites-le lors de la réservation."
    if any(k in t for k in ["contact","email","mail","téléphone","telephone"]):
        return f"Contact : {RESTAURANT['phone_public']} ou {RESTAURANT['email']}."
    return None

# ---------- Flux prénom : confirmation → épellation ----------
LETTER_FR = {
    "A": ["a","ah","à"],
    "B": ["b","bé","bay","be"],
    "C": ["c","cé","say","ce"],
    "D": ["d","dé","day","de"],
    "E": ["e","eu","œ"],
    "F": ["f","èf","ef"],
    "G": ["g","gé","jay","je"],
    "H": ["h","ache"],
    "I": ["i","y","i grec","y grec"],
    "J": ["j","ji","jii","jé"],
    "K": ["k","ka","car"],
    "L": ["l","èl","el"],
    "M": ["m","èm","em","aime"],
    "N": ["n","èn","en","aîne"],
    "O": ["o","eau","au"],
    "P": ["p","pé","pay","pe"],
    "Q": ["q","ku","cou"],
    "R": ["r","èr","air"],
    "S": ["s","ès","es"],
    "T": ["t","té","tay","te"],
    "U": ["u","û","hu"],
    "V": ["v","vé","vey","ve"],
    "W": ["w","double v","doublevé","double vé"],
    "X": ["x","iks"],
    "Y": ["y","i grec","y grec"],
    "Z": ["z","zède","zed"],
}
PHONEME_TO_LETTER = {}
for L, vars in LETTER_FR.items():
    for v in vars:
        PHONEME_TO_LETTER[v] = L

def cleanup_spelling(raw: str) -> str:
    t = (raw or "").lower()
    parts = re.split(r"[,\.;\-]+|\bet\b", t)
    letters = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        p = p.split("comme")[0].strip()
        if "double v" in p or "doublevé" in p or "double vé" in p:
            letters.append("W"); continue
        token = re.split(r"[^a-zàâäéèêëîïôöùûüç]+", p)
        token = [x for x in token if x]
        if not token:
            continue
        w = token[0]
        if w in PHONEME_TO_LETTER:
            letters.append(PHONEME_TO_LETTER[w]); continue
        if len(w) == 1 and w.isalpha():
            letters.append(w.upper()); continue
    if not letters:
        return (raw or "").strip().title()
    return "".join(letters).title()

@app.route("/name/start", methods=["POST"])
def name_start():
    vr = VoiceResponse()
    hints = last_name_hints()
    g = gather_speech("/name/confirm", hints=hints)
    g.say(ssml("Quel est votre prénom ? Ne dites que votre prénom, s'il vous plaît."),
          voice=VOICE, language=LANG)
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu. Répétons.")
    vr.redirect("/name/start")
    return xml(vr)

@app.route("/name/confirm", methods=["POST"])
def name_confirm():
    vr = VoiceResponse()
    speech = (request.values.get("SpeechResult") or "").strip()
    candidate = speech.capitalize() if speech else ""
    if not candidate:
        say_fr(vr, "Je n'ai rien compris. Répétons.")
        vr.redirect("/name/start"); return xml(vr)
    g = gather_speech(f"/name/check?candidate={candidate}")
    g.say(ssml(f"J'ai compris {candidate}. Est-ce correct ? Répondez par oui ou non."),
          voice=VOICE, language=LANG)
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu. Répétons.")
    vr.redirect("/name/confirm")
    return xml(vr)

@app.route("/name/check", methods=["POST"])
def name_check():
    vr = VoiceResponse()
    candidate = request.args.get("candidate", "")
    answer = (request.values.get("SpeechResult") or "").lower()
    if "oui" in answer or "c'est ça" in answer or "c est ca" in answer or "correct" in answer:
        say_fr(vr, f"Parfait, merci {candidate}.")
        ret = request.args.get("return", "/voice")
        vr.redirect(ret + f"&name={candidate}")
        return xml(vr)
    g = gather_speech("/name/spell", timeout="auto")
    g.say(ssml("Très bien. Pouvez-vous épeler votre prénom ? "
               "Par exemple : T comme Thomas, I comme Irène, M comme Marie."),
          voice=VOICE, language=LANG)
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu. Essayons encore.")
    vr.redirect("/name/check")
    return xml(vr)

@app.route("/name/spell", methods=["POST"])
def name_spell():
    vr = VoiceResponse()
    spelled = (request.values.get("SpeechResult") or "").strip()
    final_name = cleanup_spelling(spelled)
    say_fr(vr, f"Merci {final_name}.")
    ret = request.args.get("return", "/voice")
    vr.redirect(ret + f"&name={final_name}")
    return xml(vr)

# ---------- Webhooks Twilio ----------
@app.route("/voice", methods=["POST"])
def voice():
    vr = VoiceResponse()
    g = gather_speech("/route", timeout="auto")
    g.say(ssml(f"Bonjour, {RESTAURANT['name']} à l'appareil. Que puis-je faire pour vous ?"),
          voice=VOICE, language=LANG)
    vr.append(g)
    say_fr(vr, "Je n'ai pas entendu. Merci, au revoir.")
    vr.hangup()
    return xml(vr)

@app.route("/route", methods=["POST"])
def route():
    vr = VoiceResponse()
    speech = request.form.get("SpeechResult") or ""
    print("[ROUTE] Heard:", speech)

    # FAQ (réponse directe)
    msg = faq_answer(speech)
    if msg:
        say_fr(vr, msg); vr.hangup(); return xml(vr)

    # Intention réservation (aucune suggestion ici)
    t = speech.lower()
    if any(k in t for k in ["réserv", "reser", "table"]):
        vr.redirect("/resa?step=people"); return xml(vr)

    say_fr(vr, "Je peux vous aider pour une réservation ou répondre à vos questions.")
    vr.redirect("/voice"); return xml(vr)

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
            g = gather_speech("/resa?step=people", timeout="auto")
            g.say(ssml("Pour combien de personnes ?"), voice=VOICE, language=LANG)
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
            g = gather_speech(f"/resa?step=date&people={people}", timeout="auto")
            g.say(ssml("Quel jour souhaitez-vous venir ?"), voice=VOICE, language=LANG)
            vr.append(g); return xml(vr)
        date_iso = parse_date(speech)
        if not date_iso:
            say_fr(vr, "Je n'ai pas saisi le jour. Pouvez-vous répéter ?")
            vr.redirect(f"/resa?step=date&people={people}"); return xml(vr)
        vr.redirect(f"/resa?step=time&people={people}&date={date_iso}"); return xml(vr)

    # 3) heure (respectée telle quelle)
    if step == "time":
        people = request.args.get("people")
        date_iso = request.args.get("date")
        if not speech.strip():
            g = gather_speech(f"/resa?step=time&people={people}&date={date_iso}", timeout="auto")
            g.say(ssml("À quelle heure ?"), voice=VOICE, language=LANG)
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

        # → Nouveau : déléguer la capture du prénom au flux /name/*
        ret = f"/resa?step=notes&people={people}&date={date_iso}&time={time_hhmm}"
        vr.redirect(f"/name/start?return={ret}")
        return xml(vr)

    # 4) notes + enregistrement Airtable
    if step == "notes":
        people   = request.args.get("people")
        date_iso = request.args.get("date")
        time_hhmm= request.args.get("time")
        # récupère le nom confirmé par le flux prénom
        name     = request.args.get("name") or "Client"
        notes    = speech.strip()

        try:
            rec = save_reservation(name=name, phone=from_n, people=people, date_iso=date_iso, time_hhmm=time_hhmm, notes=notes)
            say_fr(vr, f"Parfait {name}. J'enregistre : {people} personne(s) le {date_iso} à {time_hhmm}.")
            if notes:
                say_fr(vr, f"J'ai noté : {notes}.")
            say_fr(vr, "C'est confirmé. Merci et à bientôt.")
            print("[OK] Reservation confirmed:", rec.get("id"))
        except Exception as e:
            print("[ERR] Airtable save error:", repr(e))
            say_fr(vr, "Désolé, un souci technique est survenu. Merci de réessayer dans un instant.")
        vr.hangup(); return xml(vr)

    vr.redirect("/resa?step=people"); return xml(vr)

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
    # Render lit $PORT, sinon local 5000
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
