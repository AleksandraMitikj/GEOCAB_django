"""
Најава за корисници, возачи и администратори од табелите users / drivers / admins.

Не се користи Django auth_user (тој е само за /admin/). Во session се чува:
    request.session['geocab'] = {'role': 'user'|'driver'|'admin', 'id': ..., 'name': ...}
"""
import hashlib
import secrets
from functools import wraps

from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.core import signing
from django.shortcuts import redirect

from .models import Admins, Drivers, Users

ROLES = {
    'user': (Users, 'Корисник'),
    'driver': (Drivers, 'Возач'),
    'admin': (Admins, 'Администратор'),
}
SESSION_KEY = 'geocab'


def is_active(person, role):
    return person.status.status_tip == role and person.status.status_text == 'active'


def login_person(request, role, person):
    request.session.cycle_key()  # нов session id по најава
    request.session[SESSION_KEY] = {'role': role, 'id': person.pk, 'name': person.name}


def logout_person(request):
    request.session.flush()


def current_person(request):
    """Враќа (role, објект) за најавениот или (None, None).
    Секогаш чита од базата, за блокиран профил веднаш да изгуби пристап."""
    data = request.session.get(SESSION_KEY)
    if not data or data.get('role') not in ROLES:
        return None, None
    model = ROLES[data['role']][0]
    person = model.objects.select_related('status').filter(pk=data['id']).first()
    if person is None or not is_active(person, data['role']):
        logout_person(request)
        return None, None
    return data['role'], person


def role_required(*roles):
    """@role_required('user') - страницата е само за најавен корисник.
    Во view-то најавениот е достапен како request.person."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            role, person = current_person(request)
            if person is None:
                messages.warning(request, 'Најавете се за да продолжите.')
                return redirect(f'/login/?next={request.path}')
            if role not in roles:
                messages.error(request, 'Немате пристап до таа страница.')
                return redirect('home')
            request.role, request.person = role, person
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


# ---- Потврда на е-пошта при регистрација ------------------------------------------

def confirmation_token_hash(token):
    """Во базата се чува само SHA-256 од токенот, не самиот токен од линкот."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_confirmation_token():
    """(токен за линкот во мејлот, hash за базата)"""
    token = secrets.token_urlsafe(32)
    return token, confirmation_token_hash(token)


# ---- Лозинки -------------------------------------------------------------------

RESET_SALT = 'geocab-password-reset'
RESET_MAX_AGE = 60 * 60  # линкот за нова лозинка важи 1 час


def set_password(person, password):
    person.password_hash = make_password(password)
    person.save(update_fields=['password_hash'])


def make_reset_token(role, person):
    """Потпишан линк. Делот од тековниот hash го прави неважечки штом лозинката ќе се смени."""
    return signing.dumps({'r': role, 'id': person.pk, 'h': person.password_hash[-16:]}, salt=RESET_SALT)


def person_from_reset_token(token):
    """(role, person) ако линкот е важечки, инаку (None, None)."""
    try:
        data = signing.loads(token, salt=RESET_SALT, max_age=RESET_MAX_AGE)
    except signing.BadSignature:
        return None, None
    if data.get('r') not in ROLES:
        return None, None
    person = ROLES[data['r']][0].objects.select_related('status').filter(pk=data.get('id')).first()
    if person is None or person.password_hash[-16:] != data.get('h') or not is_active(person, data['r']):
        return None, None
    return data['r'], person
