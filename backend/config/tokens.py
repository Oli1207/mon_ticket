import hashlib


def hash_token(raw_token):
    """Seul ce hash est stocke : une fuite de la base ne donne aucun lien utilisable."""
    return hashlib.sha256(raw_token.encode()).hexdigest()
