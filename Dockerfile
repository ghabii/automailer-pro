# Utilise une version officielle de Python
FROM python:3.10-slim

# Définit le dossier de travail sur le serveur
WORKDIR /app

# Copie les fichiers et installe les bibliothèques
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie tout le reste de ton code (app.py, templates/, etc.)
COPY . .

# Crée le dossier des uploads et donne les droits de lecture/écriture
RUN mkdir -p uploads && chmod 777 uploads

# Ouvre le port 7860 (requis par Hugging Face)
EXPOSE 7860

# Commande pour lancer l'application
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000"]
