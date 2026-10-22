"""
Профил и лозинки за сите улоги (корисник, возач, администратор).
"""
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.urls import reverse

from .auth import ROLES, current_person, is_active, login_person, make_reset_token, person_from_reset_token, role_required, set_password
from .forms import ChangePasswordForm, ForgotPasswordForm, NewPasswordForm, profile_form

ALL_ROLES = tuple(ROLES)

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
