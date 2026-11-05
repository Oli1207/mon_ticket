from django.conf import settings


def get_client_ip(request):
    """IP du client, en ne faisant confiance a X-Forwarded-For que derriere NUM_PROXIES proxies connus."""
    remote_addr = request.META.get('REMOTE_ADDR', '')
    num_proxies = settings.NUM_PROXIES
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if num_proxies and xff:
        addrs = [a.strip() for a in xff.split(',') if a.strip()]
        if addrs:
            return addrs[-min(num_proxies, len(addrs))]
    return remote_addr or '0.0.0.0'
