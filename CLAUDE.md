# Mon Ticket — Plateforme de billetterie spectacle pour l'Afrique

## Exigence de fond

Cette application est faite pour **scaler** : des centaines de milliers d'utilisateurs, des pics de charge
le jour des mises en vente (rush tickets). Chaque fonctionnalite doit etre pensee pour ca des la premiere
ligne. Faire ne suffit pas : il faut **tres bien faire**, au niveau des normes professionnelles les plus
strictes, en securite, en UI/UX et en architecture.

Avant de coder : se demander "que se passe-t-il avec 100 000 commandes simultanees sur un evenement sold-out ?"
et se contredire soi-meme sur chaque decision.

## Stack

### Backend
- Django 6 + Django REST Framework + SimpleJWT
- PostgreSQL (psycopg2) + Redis + Celery
- python-dotenv

### Frontend
- React 19 + Vite 8
- CSS Modules + CSS custom properties (PAS de Tailwind)
- React Router DOM v7
- Axios + Zustand 5
- Framer Motion 11 (animations)
- maplibre-gl 5.6 (cartes, avec OpenFreeMap tiles gratuit sans cle)
- react-hot-toast (notifications)
- qrcode + qr-scanner (QR codes billets)
- date-fns (formatage dates)

### Structure
Monorepo : `backend/` et `frontend/` avec chacun son venv/node_modules.

## Performance et montee en charge

- **Jamais de travail non borne dans une requete** : toute liste est paginee cote serveur, avec un plafond
  (`page_size` max). La page d'accueil charge les featured, puis le reste au scroll.
- **Index sur tout ce qui est trie ou filtre** (`ORDER BY`, `WHERE` frequents). Un tri sans index = scan complet.
  Verifier les migrations generees. Tables Order/Ticket : index sur `status`, `event_id`, `email`, `expires_at`.
- **Pas de N+1** : `select_related` / `prefetch_related` partout. Jamais de requete dans une boucle.
- **Agregats en SQL** (`Sum`, `Count`, `Exists`), jamais en Python sur toutes les lignes.
  Les stats du dashboard organisateur sont des requetes SQL aggregees, pas des boucles Python.
- **Compteurs et statistiques couteux** : mis en cache Redis avec un TTL court (5 min pour PricingSettings,
  1 min pour les stats dashboard).
- **Travail lourd hors requete** : taches Celery par lots, idempotentes (relancables sans doublon).
  Exemples : expiration des commandes pending, envoi des emails de confirmation, generation des billets PDF.
- **Requetes annulables** cote client (`AbortController`), pas de reponse tardive qui ecrase une plus recente.
- **Rush tickets** : la creation de commande doit etre rapide. Les verifications anti-accaparement
  utilisent des requetes indexees (`email`, `client_ip`, `status='pending'`).
- `select_for_update` sur les TicketType lors de la reservation pour eviter les survendings.

## Securite (normes strictes)

- **Validation cote serveur de tout** ce qui vient du client : liste fermee de valeurs, longueurs max, types.
  La validation front n'est qu'un confort. Jamais de texte libre non borne.
- **Pas de fuite d'information** : reponses generiques sur l'existence d'un compte, pas de PII dans les URLs
  ni les logs, IDs publics non devinables (`token_urlsafe`).
- **Autorisation explicite sur chaque endpoint** (`permission_classes`). Le masquage d'un bouton dans l'UI
  ne protege rien. Les capabilities sont verifiees cote serveur, pas seulement filtrees dans le frontend.
- **Actions sensibles journalisees** dans `PricingChange` (commission/pricing) et `AuditLog` (organisations).
  Qui, quoi, quand, ancienne/nouvelle valeur.
- **Rate limiting** (DRF throttle scopes) sur : login, inscription, creation de commande, webhook Paystack,
  scan de billets, endpoints admin sensibles.
- **Secrets uniquement dans `.env`** : `DJANGO_SECRET_KEY`, `QR_SECRET_KEY`, `PAYSTACK_SECRET_KEY`,
  `PAYSTACK_WEBHOOK_SECRET`. Jamais dans le depot, les logs, ni les reponses.
- **Webhook Paystack** : verification HMAC SHA-512 obligatoire, pas de confiance dans le body sans signature.
  Endpoint sans authentification JWT mais protege par la signature.
- **QR billets signes** : HMAC SHA-256 avec `QR_SECRET_KEY`, verification cote serveur au scan.
  Impossible de forger un QR valide sans la cle.
- **Anti-accaparement** : `max_pending_per_email`, `max_pending_per_ip`, `reservation_minutes` (TTL).
  Protection cote serveur, pas contournable par le client.
- **Commandes avec `access_token`** : pour acceder a sa commande sans login (lien dans l'email).
  Token long, non devinable (`token_urlsafe(32)`).

## UI / UX (normes strictes)

- **Mobile d'abord, tous les telephones** : pas de valeurs en pixels figees pour des elements qui dependent
  de la taille de police ou des zones systeme. Tester petit ecran (375 px) ET grand.
- **Etats complets pour tout ecran** : chargement (skeleton), vide (illustration + message), erreur avec
  "Reessayer", succes. Jamais d'ecran blanc.
- **Accessibilite** : labels sur les inputs, `aria-*` sur les controles sans texte, focus visible,
  fermeture par Echap pour les modales, contrastes lisibles, zones tactiles confortables (min 44px).
- **Messages d'erreur en francais**, clairs, disant quoi faire. Tutoiement chaleureux dans l'app.

## Design System — Regles strictes

Ce projet se distingue par son design editorial. PAS de vibecoding.

- **Typographie** : Inter Tight (display, condensed bold, 48-80px), Inter (body, 15-16px)
- **Couleurs** : noir #000 (fond), blanc #fff (texte), or #D4A017 (accent unique), gris #666 (muted)
- **Border-radius** : 0px par defaut, 2px max sur les cartes, `border-radius: 999px` uniquement sur les CTA pills
- **Images** : ratio 16:9, overlay sombre gradient, traitees comme des affiches de cinema
- **Espacement** : 80px+ entre les sections, genereux espace negatif
- **Ombres** : subtiles, pas de drop-shadow agressifs
- **Design tokens** : tout passe par les CSS custom properties de `styles/tokens.css`. Pas de couleurs en dur.

### CSS — Pas de Tailwind
- Chaque composant a son fichier `.module.css`
- Design tokens dans `styles/tokens.css` (CSS custom properties sur `:root`)
- Animations CSS simples (hover, active) dans le CSS
- Animations complexes (page transitions, stagger, drag) via Framer Motion

## Conventions de code

- Django apps : `users`, `organizations`, `events`, `ticketing`
- API REST : prefixe `/api/v1/`
- Nommage modeles : PascalCase, champs : snake_case
- Frontend composants : PascalCase fichiers et exports
- Pas de commentaires sauf quand le POURQUOI n'est pas evident
- FCFA comme monnaie (pas de decimales pour l'affichage, DecimalField en base)
- Code propre, lisible, coherent avec le style existant
- Une responsabilite par module, reutiliser au lieu de dupliquer
- Toute migration est relue ; donnees existantes preservees

## Architecture et qualite

- **Tester pour de vrai** : verifier les permissions (anonyme 401, non autorise 403), les cas limites
  (commande avec 0 billets, promo code expire, billet deja scanne), verifier visuellement l'UI (mobile + desktop).
- **Migrations** : relire les migrations generees, verifier les index, les valeurs par defaut, les nullable.
  Changements compatibles avec le code deploye.
- **Pas de code mort** : pas de fonctionnalites a moitie implementees, pas de TODO sans issue trackee.

## Commission system

Architecture a 4 niveaux (global > partner code > event > ticket type).
Resolution : le plus specifique non-null gagne.
Voir `docs/DESIGN.md` pour les details complets.

## Paystack

- Split payment via SubAccounts (reversement automatique)
- Webhook HMAC SHA-512 verification
- Billets QR codes signes HMAC (prefixe SPT1)
- Expiration des commandes pending via Celery beat (toutes les minutes)
