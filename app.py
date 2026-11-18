import os
import smtplib
import time
import csv
import re
import io
import uuid
from datetime import datetime
from pypdf import PdfReader
from fpdf import FPDF
from threading import Thread
from email.message import EmailMessage
from flask import Flask, render_template, request, jsonify, Response, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'cle_secrete_automailer_saas_2026')
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Configuration secrète pour la réception des signalements (invisible pour les utilisateurs)
SUPPORT_SENDER_EMAIL = "mohamedghabi549@gmail.com"
SUPPORT_SENDER_PASS = os.environ.get('SUPPORT_PASS')

campaigns = {}

def get_user_campaign():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    uid = session['user_id']
    if uid not in campaigns:
        campaigns[uid] = {
            "status": "idle",
            "batch_contacts": [],
            "remaining_contacts": [],
            "total_found": 0,
            "limit": 0,
            "sent": 0,
            "errors": 0,
            "current_target": "",
            "logs": [],
            "valid_emails": [],
            "invalid_emails": []
        }
    return campaigns[uid]

def clean_pdf_text(text):
    return str(text).encode('latin-1', 'replace').decode('latin-1')

def read_contacts_file(file_path, filename):
    contacts = []
    if filename.lower().endswith('.csv'):
        try:
            with open(file_path, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('email') and '@' in row['email']:
                        contacts.append(row)
        except Exception as e:
            print(f"Erreur CSV: {e}")
    elif filename.lower().endswith('.pdf'):
        try:
            reader = PdfReader(file_path)
            text = "".join(page.extract_text() + "\n" for page in reader.pages)
            pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            emails = list(set(re.findall(pattern, text)))
            for email in emails:
                domaine = email.split('@')[1].split('.')[0]
                company_guess = domaine.capitalize()
                if domaine.lower() in ['gmail', 'yahoo', 'hotmail', 'outlook']:
                    company_guess = "your company"
                contacts.append({
                    'email': email,
                    'Contact name': 'Hiring Team',
                    'Company Name': company_guess
                })
        except Exception as e:
            print(f"Erreur PDF: {e}")
    return contacts

def send_worker(state, cv_path, sender_email, sender_password, subject_template, body_template):
    counter = 0
    while len(state["batch_contacts"]) > 0:
        if state["status"] == "stopped":
            break
        if state["status"] == "paused":
            time.sleep(1)
            continue
        
        counter += 1
        contact = state["batch_contacts"].pop(0)
        email = contact.get('email', '').strip()
        name = contact.get('Contact name', 'Hiring Team').strip()
        company = contact.get('Company Name', 'your company').strip()
        
        state["current_target"] = f"#{counter}/{state['limit']} — {email} ({company})"

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(sender_email, sender_password)
                personalized_subject = subject_template.replace('{company}', company).replace('{name}', name)
                personalized_body = body_template.replace('{company}', company).replace('{name}', name)

                msg = EmailMessage()
                msg['Subject'] = personalized_subject
                msg['From'] = sender_email
                msg['To'] = email
                msg.set_content(personalized_body)

                if cv_path and os.path.exists(cv_path):
                    with open(cv_path, 'rb') as f:
                        msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=os.path.basename(cv_path))

                server.send_message(msg)
                state["sent"] += 1
                
                entry = {"num": state["sent"], "email": email, "company": company, "status": "success"}
                state["logs"].insert(0, entry)
                state["valid_emails"].append({"email": email, "company": company, "name": name})
                time.sleep(3)
        except Exception as e:
            err_msg = str(e)
            print(f"Erreur avec {email}: {err_msg}")
            state["errors"] += 1
            state["logs"].insert(0, {"num": "-", "email": email, "company": company, "status": "error"})
            state["invalid_emails"].append({"email": email, "company": company, "reason": err_msg[:80]})
            
    if state["status"] != "stopped":
        state["status"] = "finished"
    if cv_path and os.path.exists(cv_path):
        os.remove(cv_path)

# ================= ROUTES API =================

@app.route('/')
def index():
    get_user_campaign()
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_campaign():
    state = get_user_campaign()
    if state["status"] in ["running", "paused"]:
        return jsonify({"error": "Vous avez déjà une campagne en cours."}), 400

    sender_email = request.form.get('email')
    sender_password = request.form.get('password')
    subject = request.form.get('subject')
    template = request.form.get('template')
    
    requested_limit = int(request.form.get('limit', 300))
    effective_limit = min(requested_limit, 300)
    
    contacts_file = request.files.get('contacts_file')
    cv_file = request.files.get('cv_file')

    if not contacts_file or contacts_file.filename == '':
        return jsonify({"error": "Fichier de contacts manquant."}), 400

    unique_prefix = str(uuid.uuid4())[:8]
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{unique_prefix}_{secure_filename(contacts_file.filename)}")
    contacts_file.save(file_path)

    cv_path = None
    if cv_file and cv_file.filename != '':
        cv_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{unique_prefix}_{secure_filename(cv_file.filename)}")
        cv_file.save(cv_path)

    all_contacts = read_contacts_file(file_path, contacts_file.filename)
    os.remove(file_path)

    if not all_contacts:
        if cv_path and os.path.exists(cv_path):
            os.remove(cv_path)
        return jsonify({"error": "Aucun contact valide trouvé dans le fichier."}), 400

    batch = all_contacts[:effective_limit]
    surplus = all_contacts[effective_limit:]

    state.update({
        "status": "running",
        "batch_contacts": batch,
        "remaining_contacts": surplus,
        "total_found": len(all_contacts),
        "limit": len(batch),
        "sent": 0,
        "errors": 0,
        "current_target": "Initialisation de la connexion SMTP...",
        "logs": [],
        "valid_emails": [],
        "invalid_emails": []
    })

    thread = Thread(target=send_worker, args=(state, cv_path, sender_email, sender_password, subject, template))
    thread.start()

    return jsonify({"success": True})

@app.route('/status')
def get_status():
    state = get_user_campaign()
    total_left = len(state["batch_contacts"]) + len(state["remaining_contacts"])
    return jsonify({
        "status": state["status"],
        "total_found": state["total_found"],
        "limit": state["limit"],
        "sent": state["sent"],
        "errors": state["errors"],
        "current_target": state["current_target"],
        "remaining_count": total_left,
        "logs": state["logs"][:50]
    })

@app.route('/control/<action>')
def control(action):
    state = get_user_campaign()
    if action == "pause" and state["status"] == "running":
        state["status"] = "paused"
    elif action == "resume" and state["status"] == "paused":
        state["status"] = "running"
    elif action == "stop":
        state["status"] = "stopped"
    return jsonify({"status": state["status"]})

@app.route('/download_remaining')
def download_remaining():
    state = get_user_campaign()
    all_remaining = state["batch_contacts"] + state["remaining_contacts"]
    if not all_remaining:
        return "Aucun contact restant", 404

    output = io.StringIO()
    fieldnames = all_remaining[0].keys()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(all_remaining)

    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=contacts_restants_lot_suivant.csv"}
    )

# ================= ROUTE : SIGNALEMENT DE PROBLÈME DIRECT (SANS MOT DE PASSE) =================
@app.route('/report_issue', methods=['POST'])
def report_issue():
    data = request.json
    user_email = data.get('user_email', '').strip()
    problem_msg = data.get('message', '').strip()

    if not user_email or not problem_msg:
        return jsonify({"error": "Veuillez indiquer votre adresse e-mail et décrire votre problème."}), 400

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(ADMIN_SUPPORT_EMAIL, SUPPORT_SENDER_PASS)
            msg = EmailMessage()
            msg['Subject'] = f"[AutoMailer Pro - Support] Signalement de {user_email}"
            msg['From'] = ADMIN_SUPPORT_EMAIL
            msg['To'] = ADMIN_SUPPORT_EMAIL
            msg['Reply-To'] = user_email
            msg.set_content(
                f"Nouveau signalement recu depuis AutoMailer Pro :\n\n"
                f"Email de l'utilisateur : {user_email}\n"
                f"Date : {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
                f"Message :\n{problem_msg}"
            )
            server.send_message(msg)
        return jsonify({"success": True})
    except Exception as e:
        print(f"Erreur support : {e}")
        return jsonify({"error": "Le service d'envoi est temporairement indisponible. Vérifiez la configuration SMTP."}), 500

# ================= ROUTE : PDF 1 - GUIDE MOT DE PASSE GMAIL =================
@app.route('/download_guide_gmail_pdf')
def download_guide_gmail_pdf():
    pdf = FPDF()
    pdf.add_page()
    
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(13, 110, 253)
    pdf.cell(0, 12, clean_pdf_text("Guide : Mot de passe d'application Gmail"), ln=True, align="C")
    
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, clean_pdf_text("AutoMailer Pro - Developpe par Mohamed Ghabi & Yassin Trablssi"), ln=True, align="C")
    pdf.ln(8)

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text("Pourquoi un mot de passe d'application ?"), ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 7, clean_pdf_text(
        "Pour proteger votre compte Google, Gmail bloque les connexions utilisant votre mot de passe habituel. "
        "Vous devez generer un code unique de 16 lettres dedie a l'envoi d'e-mails."
    ))
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 10, clean_pdf_text("Etapes pour obtenir votre code en 1 minute :"), ln=True)
    pdf.ln(2)
    
    pdf.set_font("Helvetica", "", 11)
    steps_gmail = [
        "1. Rendez-vous sur votre compte Google : https://myaccount.google.com",
        "2. Dans le menu de gauche, cliquez sur l'onglet 'Securite'.",
        "3. Dans la section 'Connexion a Google', activez la 'Validation en deux etapes' (si ce n'est pas deja fait).",
        "4. Une fois activee, cliquez sur la barre de recherche tout en haut de la page Google et tapez : Mots de passe des applications",
        "5. Cliquez sur le resultat 'Mots de passe des applications'.",
        "6. Saisissez un nom pour l'application (exemple : AutoMailer Pro) puis cliquez sur 'Creer'.",
        "7. Une fenetre jaune/bleue affiche un code de 16 caracteres (ex : abcd efgh ijkl mnop).",
        "8. Copiez ce code et collez-le dans le champ 'Mot de passe d'application' de AutoMailer Pro."
    ]
    for step in steps_gmail:
        pdf.multi_cell(0, 8, clean_pdf_text(step))
        pdf.ln(1)

    pdf_bytes = bytes(pdf.output())
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-disposition": "attachment; filename=Guide_Code_Gmail.pdf"}
    )

# ================= ROUTE : PDF 2 - GUIDE FORMAT CSV =================
@app.route('/download_guide_csv_pdf')
def download_guide_csv_pdf():
    pdf = FPDF()
    pdf.add_page()
    
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(25, 135, 84)
    pdf.cell(0, 12, clean_pdf_text("Guide : Format du fichier Contacts (CSV & PDF)"), ln=True, align="C")
    
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, clean_pdf_text("AutoMailer Pro - Developpe par Mohamed Ghabi & Yassin Trablssi"), ln=True, align="C")
    pdf.ln(8)

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text("1. Structure obligatoire du fichier CSV"), ln=True)
    
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 7, clean_pdf_text(
        "Votre fichier CSV (separateur virgule) doit obligatoirement comporter ces 3 noms de colonnes exacts sur la premiere ligne :"
    ))
    pdf.ln(4)

    # Tableau exemple CSV
    pdf.set_fill_color(225, 240, 232)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(75, 9, "email", border=1, fill=True)
    pdf.cell(55, 9, "Contact name", border=1, fill=True)
    pdf.cell(60, 9, "Company Name", border=1, fill=True, ln=True)

    pdf.set_font("Helvetica", "", 10)
    exemples = [
        ("recrutement@google.com", "Sarah Jenkins", "Google"),
        ("hr@capgemini.com", "Marc Dupont", "Capgemini"),
        ("jobs@startup.io", "Hiring Team", "Startup IO"),
        ("contact@entreprise.tn", "Amine Ben Ali", "Entreprise TN")
    ]
    for em, nm, cp in exemples:
        pdf.cell(75, 8, clean_pdf_text(em), border=1)
        pdf.cell(55, 8, clean_pdf_text(nm), border=1)
        pdf.cell(60, 8, clean_pdf_text(cp), border=1, ln=True)

    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 10, clean_pdf_text("2. Utilisation des balises dynamiques"), ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 7, clean_pdf_text(
        "- {name} sera automatiquement remplace par la valeur de la colonne 'Contact name'.\n"
        "- {company} sera automatiquement remplace par la valeur de la colonne 'Company Name'."
    ))

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 10, clean_pdf_text("3. Alternative : Importation directe d'un fichier PDF"), ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 7, clean_pdf_text(
        "Si vous n'avez pas de fichier CSV, vous pouvez importer directement un document PDF contenant une liste d'e-mails. "
        "L'application extraira automatiquement toutes les adresses valides et devinera le nom de l'entreprise a partir du domaine."
    ))

    pdf_bytes = bytes(pdf.output())
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-disposition": "attachment; filename=Guide_Format_CSV.pdf"}
    )

# ================= ROUTE : RAPPORT PDF FINAL (VALIDES & INVALIDES) =================
@app.route('/download_report_pdf')
def download_report_pdf():
    state = get_user_campaign()
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(13, 110, 253)
    pdf.cell(0, 12, clean_pdf_text("Rapport de Campagne - AutoMailer Pro"), ln=True, align="C")
    
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, clean_pdf_text(f"Genere le {datetime.now().strftime('%d/%m/%Y a %H:%M')} | Statut : {state['status'].upper()}"), ln=True, align="C")
    pdf.cell(0, 6, clean_pdf_text("AutoMailer Pro - Developpe par Mohamed Ghabi & Yassin Trablssi"), ln=True, align="C")
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, clean_pdf_text(f"Total envoyes (Valides) : {len(state['valid_emails'])}   |   Total echoues (Invalides) : {len(state['invalid_emails'])}"), ln=True)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(25, 135, 84)
    pdf.cell(0, 9, clean_pdf_text(f"1. E-mails Valides / Envoyes ({len(state['valid_emails'])})"), ln=True)
    
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(0, 0, 0)
    if state['valid_emails']:
        for idx, item in enumerate(state['valid_emails'], 1):
            pdf.cell(0, 6, clean_pdf_text(f"{idx}. {item['email']}  -  ({item['company']})"), ln=True)
    else:
        pdf.cell(0, 6, clean_pdf_text("Aucun e-mail envoye."), ln=True)
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(220, 53, 69)
    pdf.cell(0, 9, clean_pdf_text(f"2. E-mails Invalides / Echoues ({len(state['invalid_emails'])})"), ln=True)
    
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(0, 0, 0)
    if state['invalid_emails']:
        for idx, item in enumerate(state['invalid_emails'], 1):
            pdf.multi_cell(0, 6, clean_pdf_text(f"{idx}. {item['email']} ({item['company']}) -> Erreur : {item['reason']}"))
    else:
        pdf.cell(0, 6, clean_pdf_text("Aucune erreur detectee."), ln=True)

    pdf_bytes = bytes(pdf.output())
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-disposition": "attachment; filename=Rapport_Emails_Valides_Invalides.pdf"}
    )

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 7860))
    app.run(host='0.0.0.0', port=port)