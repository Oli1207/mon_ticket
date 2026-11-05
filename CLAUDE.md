# Mon Ticket — Plateforme de billetterie spectacle pour l'Afrique

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

## Design System — Regles strictes

Ce projet se distingue par son design editorial. PAS de vibecoding.

- **Typographie** : Inter Tight (display, condensed bold, 48-80px), Inter (body, 15-16px)
- **Couleurs** : noir #000 (fond), blanc #fff (texte), or #D4A017 (accent unique), gris #666 (muted)
- **Border-radius** : 0px par defaut, 2px max sur les cartes, `border-radius: 999px` uniquement sur les CTA pills
- **Images** : ratio 16:9, overlay sombre gradient, traitees comme des affiches de cinema
- **Espacement** : 80px+ entre les sections, genereux espace negatif
- **Ombres** : subtiles, pas de drop-shadow agressifs

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

## Commission system

Architecture a 4 niveaux (global > partner code > event > ticket type).
Voir `docs/DESIGN.md` pour les details complets.

## Paystack

- Split payment via SubAccounts (reversement automatique)
- Webhook HMAC SHA-512 verification
- Billets QR codes signes HMAC (prefixe SPT1)
