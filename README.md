# Bot Vinted → Telegram (gratuit, sans ordinateur allumé)

Le bot surveille vos recherches Vinted et vous envoie chaque nouvelle annonce
sur Telegram (photo, prix frais inclus, marque, taille, état, lien).
Il n'utilise **pas** votre compte Vinted et n'achète rien.

## 1. Créer le bot Telegram (2 min)

1. Dans Telegram, ouvrez **@BotFather** → `/newbot` → choisissez un nom.
   Il vous donne un **token** (ex. `123456:ABC...`).
2. Ouvrez la conversation avec votre nouveau bot et envoyez-lui `/start`.
3. Récupérez votre **chat id** : ouvrez dans le navigateur
   `https://api.telegram.org/bot<VOTRE_TOKEN>/getUpdates`
   et cherchez `"chat":{"id":` suivi d'un nombre. C'est votre chat id.

## 2. Mettre le bot sur GitHub (gratuit)

1. Créez un compte sur github.com, puis un **nouveau dépôt PUBLIC**
   (public = minutes d'exécution illimitées et gratuites ; vos identifiants
   restent cachés dans les « secrets », seul `searches.txt` est visible).
2. Envoyez-y les fichiers : `bot.py`, `searches.txt`, `README.md`, et le dossier
   `.github/workflows/vinted-alerts.yml` (déjà prêt dans le zip).
3. Dans le dépôt : **Settings → Secrets and variables → Actions → New repository secret**
   - `TELEGRAM_TOKEN` = votre token
   - `TELEGRAM_CHAT_ID` = votre chat id
4. Onglet **Actions** → activez les workflows → « Vinted alerts » → **Run workflow**
   pour un premier test. Au premier passage, le bot mémorise les annonces
   existantes sans vous notifier ; ensuite vous ne recevez que les nouvelles.

Modifiez `searches.txt` à tout moment (directement sur github.com depuis
votre téléphone) pour changer vos recherches.

## Limites à connaître

- GitHub lance la tâche environ toutes les 5 minutes, parfois avec 5 à 15 min
  de retard. Pour des alertes plus rapides, utilisez l'option 3.
- **Vinted bloque parfois les adresses IP de datacenter** (dont celles de GitHub).
  Si le workflow échoue avec « HTTP 403 », c'est ça : passez à l'option 3.
- Respectez des fréquences raisonnables : Vinted interdit en principe
  l'automatisation dans ses conditions d'utilisation.

## 3. Alternative : serveur toujours allumé

Le même script tourne en continu avec `python bot.py --loop` (vérifie toutes
les 60 s) sur un Raspberry Pi, un petit VPS gratuit (ex. Oracle Cloud Free Tier)
ou un vieux téléphone Android avec Termux. Il faut définir les variables
`TELEGRAM_TOKEN` et `TELEGRAM_CHAT_ID` et lancer `pip install requests`.
Une IP résidentielle (Raspberry Pi chez vous) est la moins souvent bloquée.
