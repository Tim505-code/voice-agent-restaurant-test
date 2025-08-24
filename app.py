# app.py — IVR Restaurant (menu interactif) prêt à l'emploi
# Routes :
#   /voice  -> accueil + menu (1 réserver, 2 horaires, 3 adresse, 0 parler à qqn)
#   /route  -> dispatch des choix
#   /resa   -> petit flux de réservation (personnes -> heure -> confirmation)
#   /health -> check simple

from flask import Flask, request, Response
from urllib.parse import urlencode, quote_plus
from twilio.twiml.voice_response import VoiceResponse, Gather, Pause

app = Flask(__name__)

# 🛠️  A PERSONNALISER pour ton resto :
RESTAURANT_NAME = "La Truffe d'Or"
HOURS_TEXT = (
    "Nous sommes ouverts du mardi au dimanche, "
    "de 11 heures 30 à 14 heures 30, et de 18 heures 30 à 22 heures 30. "
    "Fermé le lundi."
)
ADDRESS_TEXT = "Rue de la Gare 10, 1003 Lausanne. Entrée par la cour intérieure."
RESERVATION_LINE = "+4121xxxxxxx"  # juste lu à l'appelant si besoin, pas d'appel sortant ici.

def xml(resp: VoiceResponse) -> Response:
    return Response(str(resp), mimetype="text/xml")

def say_fr(resp: VoiceResponse, text: str):
    resp.say(text, language="fr-FR", voice="alice")

@app.route("/voice", methods=["GET", "POST"])
def voice():
    """Accueil + menu principal"""
    vr = VoiceResponse()

    # Message d'accueil
    say_fr(vr, f"Bienvenue au restaurant {RESTAURANT_NAME}.")

    # Menu avec Gather (DTMF + speech)
    g = Gather(
        input="speech dtmf",
        num_digits=1,
        timeout=6,
        language="fr-FR",
        action="/route",
        method="POST"
    )
    say_fr(g, "Pour une réservation, appuyez sur 1. "
              "Pour les horaires, appuyez sur 2. "
              "Pour notre adresse, appuyez sur 3. "
              "Pour parler à quelqu'un, appuyez sur 0.")
    vr.append(g)

    # Si rien saisi, on répète une fois puis on raccroche poliment
    say_fr(vr, "Je n'ai pas reçu de saisie. Je répète.")
    vr.redirect("/voice")
    return xml(vr)

@app.route("/route", methods=["GET", "POST"])
def route():
    """Décode le choix et redirige"""
    digits = request.form.get("Digits")
    speech = (request.form.get("SpeechResult") or "").lower()

    vr = VoiceResponse()

    choice = None
    if digits in {"0", "1", "2", "3"}:
        choice = digits
    else:
        if "reserv" in speech:
            choice = "1"
        elif "horaire" in speech or "ouvert" in speech:
            choice = "2"
        elif "adress" in speech or "où" in speech or "ou se trouve" in speech:
            choice = "3"
        elif "parler" in speech or "humain" in speech or "serveur" in speech:
            choice = "0"

    if choice == "1":
        # Aller au flux réservation (étape 1 : nombre de personnes)
        vr.redirect("/resa?step=people")
        return xml(vr)
    elif choice == "2":
        say_fr(vr, HOURS_TEXT)
        say_fr(vr, "Merci pour votre appel. À bientôt !")
        vr.hangup()
        return xml(vr)
    elif choice == "3":
        say_fr(vr, f"Notre adresse est : {ADDRESS_TEXT}")
        say_fr(vr, "Nous vous attendons avec plaisir. À bientôt !")
        vr.hangup()
        return xml(vr)
    elif choice == "0":
        # Ici on pourrait <Dial> vers la ligne du restaurant. On lit juste le numéro.
        say_fr(vr, f"Veuillez appeler directement la ligne du restaurant : {RESERVATION_LINE}.")
        say_fr(vr, "Merci pour votre appel. À bientôt !")
        vr.hangup()
        return xml(vr)

    # Choix non compris -> retour menu
    say_fr(vr, "Je n'ai pas compris. Réessayons.")
    vr.redirect("/voice")
    return xml(vr)

@app.route("/resa", methods=["GET", "POST"])
def resa():
    """Petit flux stateless : personnes -> heure -> nom -> confirmation.
       On garde l'état dans les query params (people, time, name)."""
    step = request.args.get("step", "people")
    people = request.args.get("people")
    time = request.args.get("time")
    name = request.args.get("name")

    vr = VoiceResponse()

    # Etape : nombre de personnes
    if step == "people":
        g = Gather(
            input="speech dtmf",
            num_digits=2,
            timeout=6,
            language="fr-FR",
            action="/resa?step=people_captured",
            method="POST"
        )
        say_fr(g, "Pour combien de personnes ? Dites un nombre, ou tapez-le au clavier.")
        vr.append(g)
        say_fr(vr, "Je n'ai pas reçu de saisie.")
        vr.redirect("/resa?step=people")
        return xml(vr)

    if step == "people_captured":
        digits = request.form.get("Digits")
        speech = (request.form.get("SpeechResult") or "").lower()
        captured = None
        if digits:
            captured = digits
        else:
            # Extraire le premier nombre simple de la dictée (naïf mais efficace pour une démo)
            for token in speech.split():
                if token.isdigit():
                    captured = token
                    break
        if not captured:
            say_fr(vr, "Désolé, je n'ai pas compris le nombre de personnes.")
            vr.redirect("/resa?step=people")
            return xml(vr)

        q = urlencode({"step": "time", "people": captured}, quote_via=quote_plus)
        vr.redirect(f"/resa?{q}")
        return xml(vr)

    # Etape : heure souhaitée
    if step == "time":
        g = Gather(
            input="speech dtmf",
            num_digits=4,  # ex: 1930
            timeout=7,
            language="fr-FR",
            action=f"/resa?step=time_captured&people={quote_plus(people or '')}",
            method="POST"
        )
        say_fr(g, "À quelle heure souhaitez-vous réserver ? "
                  "Par exemple, dites dix neuf trente, ou tapez 1 9 3 0.")
        vr.append(g)
        say_fr(vr, "Je n'ai pas reçu de saisie.")
        vr.redirect(f"/resa?step=time&people={quote_plus(people or '')}")
        return xml(vr)

    if step == "time_captured":
        digits = request.form.get("Digits")
        speech = (request.form.get("SpeechResult") or "").lower()
        captured = None
        if digits and (3 <= len(digits) <= 4):
            captured = digits
        else:
            # Capture grossière d'une suite de chiffres dans la dictée
            num = "".join(ch for ch in speech if ch.isdigit())
            if 3 <= len(num) <= 4:
                captured = num
        if not captured:
            say_fr(vr, "Désolé, je n'ai pas compris l'heure souhaitée.")
            vr.redirect(f"/resa?step=time&people={quote_plus(people or '')}")
            return xml(vr)

        q = urlencode({"step": "name", "people": people or "", "time": captured}, quote_via=quote_plus)
        vr.redirect(f"/resa?{q}")
        return xml(vr)

    # Etape : nom
    if step == "name":
        g = Gather(
            input="speech",
            timeout=6,
            language="fr-FR",
            action=f"/resa?step=confirm&people={quote_plus(people or '')}&time={quote_plus(time or '')}",
            method="POST"
        )
        say_fr(g, "À quel nom dois-je enregistrer la réservation ?")
        vr.append(g)
        say_fr(vr, "Je n'ai pas reçu votre nom.")
        vr.redirect(f"/resa?step=name&people={quote_plus(people or '')}&time={quote_plus(time or '')}")
        return xml(vr)

    if step == "confirm":
        name_captured = (request.form.get("SpeechResult") or "").strip()
        if not name_captured:
            say_fr(vr, "Désolé, je n'ai pas saisi votre nom.")
            vr.redirect(f"/resa?step=name&people={quote_plus(people or '')}&time={quote_plus(time or '')}")
            return xml(vr)

        # Lecture de la récap
        # Formatage simple de l'heure (ex: 1930 -> 19 heures 30)
        pretty_time = time
        if time and len(time) in (3, 4):
            if len(time) == 3:
                pretty_time = f"{time[0]} heures {time[1:]}"
            else:
                pretty_time = f"{time[:2]} heures {time[2:]}"
        say_fr(vr, f"Parfait. Réservation notée pour {people} personnes, à {pretty_time}, au nom de {name_captured}.")
        say_fr(vr, "Un membre de notre équipe vous recontactera si nécessaire. Merci et à bientôt !")
        vr.hangup()
        return xml(vr)

    # Par défaut, retour au menu
    say_fr(vr, "Revenons au menu principal.")
    vr.redirect("/voice")
    return xml(vr)

@app.route("/health", methods=["GET"])
def health():
    return {"ok": True}

if __name__ == "__main__":
    # Port 5001 (tu as déjà ngrok en 5001)
    app.run(host="0.0.0.0", port=5001, debug=True)
