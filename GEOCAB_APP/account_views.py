"""
Регистрација со потврда на е-пошта, профил и лозинки за сите улоги (корисник, возач, администратор).
"""
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.db import DatabaseError, transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from . import db
from .auth import (ROLES, confirmation_token_hash, current_person, is_active, login_person, make_reset_token,
                   new_confirmation_token, person_from_reset_token, role_required, set_password)
from .forms import ChangePasswordForm, ForgotPasswordForm, NewPasswordForm, RegisterForm, ResendConfirmationForm, profile_form
from .models import PendingRegistrations, Users

ALL_ROLES = tuple(ROLES)


# ---- Регистрација со потврда на е-пошта ----------------------------------------------
# Профилот во Users се креира дури кога корисникот ќе го отвори линкот од мејлот.
# Во развој мејлот се печати во терминалот на runserver (MAILERS во settings.py).

PENDING_EMAIL_KEY = 'geocab_pending_email'   # за страницата „Проверете ја е-поштата“


def _send_confirmation_email(request, name, email, token):
    link = request.build_absolute_uri(reverse('register_confirm', args=[token]))
    context = {'name': name, 'link': link, 'hours': db.CONFIRMATION_VALID_HOURS}
    send_mail('GEOCAB - потврдете ја е-поштата',
              render_to_string('GEOCAB_APP/emails/confirm_email.txt', context),
              None, [email],
              html_message=render_to_string('GEOCAB_APP/emails/confirm_email.html', context))


def register(request):
    if current_person(request)[1]:
        return redirect('home')
    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        c = form.cleaned_data
        token, token_hash = new_confirmation_token()
        try:
            with transaction.atomic():
                db.create_pending_registration(c['name'], c['username'], c['phone'], c['email'],
                                               make_password(c['password1']), token_hash)
        except DatabaseError as exc:
            form.add_error(None, db.error_message(exc))
        else:
            _send_confirmation_email(request, c['name'], c['email'], token)
            request.session[PENDING_EMAIL_KEY] = c['email']
            return redirect('register_sent')
    return render(request, 'GEOCAB_APP/register.html', {'form': form})


def register_sent(request):
    """„Ви испративме мејл“ - по регистрацијата и по повторно праќање."""
    email = request.session.get(PENDING_EMAIL_KEY)
    if not email:
        return redirect('register')
    return render(request, 'GEOCAB_APP/account/register_sent.html', {
        'email': email, 'hours': db.CONFIRMATION_VALID_HOURS,
        'resend_form': ResendConfirmationForm(initial={'email': email}),
    })


def register_confirm(request, token):
    """Линкот од мејлот: страница со копче „Потврди“ (POST), за мејл програмите што сами ги
    отвораат линковите да не го активираат профилот без корисникот."""
    pending = PendingRegistrations.objects.filter(token_hash=confirmation_token_hash(token)).first()
    if request.method == 'POST':
        try:
            with transaction.atomic():
                user_id = db.confirm_registration(confirmation_token_hash(token))
        except DatabaseError as exc:
            messages.error(request, db.error_message(exc))
            return redirect('login')
        user = Users.objects.get(pk=user_id)
        request.session.pop(PENDING_EMAIL_KEY, None)
        login_person(request, 'user', user)
        messages.success(request, f'Е-поштата е потврдена. Добредојдовте, {user.name}! Профилот е креиран.')
        return redirect('home')
    if pending is None:
        messages.error(request, 'Линкот не е важечки или профилот е веќе активиран. Обидете се да се најавите.')
        return redirect('login')
    if pending.expires_at < timezone.now():
        messages.error(request, 'Линкот истече. Побарајте нов линк или регистрирајте се повторно.')
        return redirect('register_resend')
    return render(request, 'GEOCAB_APP/account/register_confirm.html', {'pending': pending})


def register_resend(request):
    """Нов линк за потврда. Иста порака и кога нема регистрација што чека (не се открива кој се регистрирал)."""
    form = ResendConfirmationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        token, token_hash = new_confirmation_token()
        with transaction.atomic():
            pending_id = db.renew_pending_registration(email, token_hash)
        if pending_id:
            pending = PendingRegistrations.objects.get(pk=pending_id)
            _send_confirmation_email(request, pending.name, pending.email, token)
        request.session[PENDING_EMAIL_KEY] = email
        messages.info(request, 'Ако има регистрација што чека потврда за таа е-пошта, испративме нов линк. '
                               'Стариот линк повеќе не важи.')
        return redirect('register_sent')
    return render(request, 'GEOCAB_APP/account/register_resend.html', {'form': form})

# што може секоја улога сама да менува (името и возачката на возачот ги менува само админот)
PROFILE_FIELDS = {
    'user': ('name', 'phone', 'email'),
    'driver': ('phone', 'email'),
    'admin': ('name', 'phone', 'email'),
}


@role_required(*ALL_ROLES)
def profile(request):
    Form = profile_form(ROLES[request.role][0], PROFILE_FIELDS[request.role])
    form = Form(request.POST or None, instance=request.person)
    if request.method == 'POST' and form.is_valid():
        person = form.save(commit=False)
        person.save(update_fields=PROFILE_FIELDS[request.role])
        login_person(request, request.role, person)  # ново име во менито
        messages.success(request, 'Податоците се зачувани.')
        return redirect('profile')
    return render(request, 'GEOCAB_APP/account/profile.html', {
        'form': form, 'role_label': ROLES[request.role][1], 'person': request.person,
    })


@role_required(*ALL_ROLES)
def change_password(request):
    form = ChangePasswordForm(request.POST or None, person=request.person)
    if request.method == 'POST' and form.is_valid():
        set_password(request.person, form.cleaned_data['new_password1'])
        login_person(request, request.role, request.person)  # нова сесија со новата лозинка
        messages.success(request, 'Лозинката е променета.')
        return redirect('profile')
    return render(request, 'GEOCAB_APP/account/change_password.html', {'form': form})


def forgot_password(request):
    if current_person(request)[1]:
        return redirect('change_password')
    form = ForgotPasswordForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        role, email = form.cleaned_data['role'], form.cleaned_data['email']
        person = ROLES[role][0].objects.select_related('status').filter(email__iexact=email).first()
        if person and is_active(person, role):
            link = request.build_absolute_uri(reverse('reset_password', args=[make_reset_token(role, person)]))
            send_mail(
                'GEOCAB - нова лозинка',
                f'Здраво {person.name},\n\nЗа да поставите нова лозинка, отворете го линкот (важи 1 час):\n{link}\n\n'
                f'Ако не побаравте нова лозинка, игнорирајте ја оваа порака.\n\nGEOCAB',
                None, [person.email])
        # иста порака и кога е-поштата не постои, за да не се открива кој има профил
        messages.info(request, 'Ако постои профил со таа е-пошта, испративме линк за нова лозинка. Проверете ја поштата.')
        return redirect('login')
    return render(request, 'GEOCAB_APP/account/forgot_password.html', {'form': form})


def reset_password(request, token):
    role, person = person_from_reset_token(token)
    if person is None:
        messages.error(request, 'Линкот не е важечки или е истечен. Побарајте нов.')
        return redirect('forgot_password')
    form = NewPasswordForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        set_password(person, form.cleaned_data['new_password1'])
        messages.success(request, 'Лозинката е поставена. Најавете се со новата лозинка.')
        return redirect('login')
    return render(request, 'GEOCAB_APP/account/reset_password.html', {'form': form, 'person': person})
