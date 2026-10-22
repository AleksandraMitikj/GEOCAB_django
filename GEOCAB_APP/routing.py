"""
Растојание по вистинскиот пат (улиците) преку OSRM - бесплатен сервис за рути врз OpenStreetMap.

Ако OSRM не одговори, функцијата враќа None и базата го користи воздушното растојание
(fn_calculate_distance_km), па апликацијата работи и без интернет.
"""
import json
import logging
import os
import ssl
from decimal import Decimal
from functools import lru_cache
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)


def _ssl_context():
    """Python од python.org на macOS нема пристап до системските сертификати -
    се користи certifi ако е инсталиран, или фајлот со сертификати на системот."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for path in ('/etc/ssl/cert.pem', '/etc/ssl/certs/ca-certificates.crt'):
        if os.path.exists(path):
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


SSL_CONTEXT = _ssl_context()


@lru_cache(maxsize=512)
def _road_km(lat1, lng1, lat2, lng2):
    url = f'{settings.OSRM_URL}/route/v1/driving/{lng1},{lat1};{lng2},{lat2}?overview=false'
    request = Request(url, headers={'User-Agent': 'GEOCAB/1.0 (student project)'})
    try:
        with urlopen(request, timeout=4, context=SSL_CONTEXT) as response:
            data = json.load(response)
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning('OSRM не одговори (%s), се користи воздушно растојание', exc)
        return None
    if data.get('code') != 'Ok' or not data.get('routes'):
        return None
    return Decimal(data['routes'][0]['distance'] / 1000).quantize(Decimal('0.01'))


def road_distance_km(lat1, lng1, lat2, lng2):
    """km по пат помеѓу две точки (Decimal) или None ако не може да се пресмета."""
    # заокружено на ~10 m за кешот да ги препознае истите точки
    return _road_km(round(float(lat1), 4), round(float(lng1), 4), round(float(lat2), 4), round(float(lng2), 4))
