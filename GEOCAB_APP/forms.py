from django import forms
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.validators import MinValueValidator, RegexValidator

from .auth import ROLES, is_active
from .models import Drivers, DriversVehicleOwnership, Status, Users, VehicleOwnership, VehicleTypes, VehiclesModel

# Исти правила како CHECK ограничувањата во базата (chk_*_phone, chk_*_email)
phone_validator = RegexValidator(r'^[0-9+]+$', 'Телефонот смее да содржи само бројки и +.')
email_validator = RegexValidator(r'^[^@]+@[^@]+\.[^@]+$', 'Невалидна е-пошта.')


class RegisterForm(forms.ModelForm):
    phone = forms.CharField(label='Телефон', max_length=255, validators=[phone_validator])
    email = forms.CharField(label='Е-пошта', max_length=255, validators=[email_validator])
    password1 = forms.CharField(label='Лозинка', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Потврди лозинка', widget=forms.PasswordInput)

    class Meta:
        model = Users
        fields = ('name', 'username', 'phone', 'email')
        labels = {'name': 'Име и презиме', 'username': 'Корисничко име'}

    def clean_password1(self):
        password = self.cleaned_data['password1']
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('password1') and cleaned.get('password1') != cleaned.get('password2'):
            self.add_error('password2', 'Лозинките не се совпаѓаат.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.password_hash = make_password(self.cleaned_data['password1'])
        user.status = Status.objects.get(status_tip='user', status_text='active')
        if commit:
            user.save()
        return user


class LoginForm(forms.Form):
    role = forms.ChoiceField(label='Најави се како', initial='user',
                             choices=[(key, label) for key, (_, label) in ROLES.items()],
                             widget=forms.RadioSelect)
    username = forms.CharField(label='Корисничко име')
    password = forms.CharField(label='Лозинка', widget=forms.PasswordInput)

    def clean(self):
        cleaned = super().clean()
        role, username, password = cleaned.get('role'), cleaned.get('username'), cleaned.get('password')
        if not (role and username and password):
            return cleaned
        model = ROLES[role][0]
        person = model.objects.select_related('status').filter(username=username).first()
        if person is None or not check_password(password, person.password_hash):
            raise forms.ValidationError('Погрешно корисничко име или лозинка.')
        if not is_active(person, role):
            raise forms.ValidationError(f'Профилот не е активен ({person.status.status_text}).')
        cleaned['person'] = person
        return cleaned


class RideForm(forms.Form):
    """Две точки од мапата (координати + адреса) и избрана класа на возило."""
    pickup_lat = forms.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90, widget=forms.HiddenInput)
    pickup_lng = forms.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180, widget=forms.HiddenInput)
    pickup_grad = forms.CharField(label='Град', max_length=255)
    pickup_naselba = forms.CharField(label='Населба', max_length=255)
    pickup_ulica = forms.CharField(label='Улица', max_length=255)
    pickup_broj = forms.CharField(label='Број', max_length=255, required=False)
    dropoff_lat = forms.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90, widget=forms.HiddenInput)
    dropoff_lng = forms.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180, widget=forms.HiddenInput)
    dropoff_grad = forms.CharField(label='Град', max_length=255)
    dropoff_naselba = forms.CharField(label='Населба', max_length=255)
    dropoff_ulica = forms.CharField(label='Улица', max_length=255)
    dropoff_broj = forms.CharField(label='Број', max_length=255, required=False)
    vehicle_class = forms.CharField(max_length=255, required=False)

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get('pickup_lat') is not None and cleaned.get('dropoff_lat') is not None
                and (cleaned['pickup_lat'], cleaned['pickup_lng']) == (cleaned['dropoff_lat'], cleaned['dropoff_lng'])):
            raise forms.ValidationError('Почетната и крајната локација не смеат да бидат исти.')
        return cleaned

    def road_distance_km(self):
        """Растојание по пат помеѓу двете точки (None ако OSRM не одговори)."""
        from .routing import road_distance_km
        c = self.cleaned_data
        return road_distance_km(c['pickup_lat'], c['pickup_lng'], c['dropoff_lat'], c['dropoff_lng'])

    def location_args(self, prefix):
        """Аргументи за fn_create_location(lat, lng, grad, naselba, ulica, broj)."""
        c = self.cleaned_data
        return (c[f'{prefix}_lat'], c[f'{prefix}_lng'], c[f'{prefix}_grad'], c[f'{prefix}_naselba'],
                c[f'{prefix}_ulica'], c[f'{prefix}_broj'] or 'бб')


class DriverLocationForm(forms.Form):
    """Моментална локација на возачот (почеток на смена / нова локација)."""
    loc_lat = forms.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90, widget=forms.HiddenInput)
    loc_lng = forms.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180, widget=forms.HiddenInput)
    loc_grad = forms.CharField(label='Град', max_length=255)
    loc_naselba = forms.CharField(label='Населба', max_length=255)
    loc_ulica = forms.CharField(label='Улица', max_length=255)
    loc_broj = forms.CharField(label='Број', max_length=255, required=False)
    vehicle_ownership = forms.ModelChoiceField(label='Возило', queryset=None, required=False, empty_label=None)

    def __init__(self, *args, vehicles=None, **kwargs):
        super().__init__(*args, **kwargs)
        if vehicles is None:
            del self.fields['vehicle_ownership']  # само нова локација, без избор на возило
        else:
            self.fields['vehicle_ownership'].queryset = vehicles
            self.fields['vehicle_ownership'].required = True

    def location_args(self):
        c = self.cleaned_data
        return (c['loc_lat'], c['loc_lng'], c['loc_grad'], c['loc_naselba'], c['loc_ulica'], c['loc_broj'] or 'бб')



# ---- Админ панел ----------------------------------------------------------------

class NewDriverForm(forms.ModelForm):
    """Податоци за нов возач. Возилото е во VehicleForm / постоечко возило."""
    phone = forms.CharField(label='Телефон', max_length=255, validators=[phone_validator])
    email = forms.CharField(label='Е-пошта', max_length=255, validators=[email_validator])
    password = forms.CharField(label='Почетна лозинка', widget=forms.PasswordInput,
                               help_text='Возачот ја користи за најава. Кажете му ја лично.')

    class Meta:
        model = Drivers
        fields = ('name', 'username', 'phone', 'email', 'license_number')
        labels = {'name': 'Име и презиме', 'username': 'Корисничко име', 'license_number': 'Број на возачка дозвола'}

    def clean_password(self):
        password = self.cleaned_data['password']
        validate_password(password)
        return password


class VehicleForm(forms.ModelForm):
    """Возило: класата (Standard/Premium) ја одредува цената, марката е посебно поле.
    Нова марка се додава автоматски во Vehicles_model."""
    model_name = forms.CharField(label='Марка и модел', max_length=255,
                                 widget=forms.TextInput(attrs={'list': 'vehicle-models', 'placeholder': 'на пр. Toyota Corolla'}))
    year = forms.IntegerField(label='Година', validators=[MinValueValidator(1900)])

    class Meta:
        model = VehicleOwnership
        fields = ('plate', 'traffic_permit', 'vehicle_type', 'model_name', 'color', 'year')
        labels = {'plate': 'Регистарска таблица', 'traffic_permit': 'Сообраќајна дозвола',
                  'vehicle_type': 'Класа', 'color': 'Боја'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vehicle_type'].queryset = VehicleTypes.objects.order_by('vehicle_type_id')
        self.fields['vehicle_type'].label_from_instance = lambda t: t.description
        if self.instance.pk:
            self.fields['model_name'].initial = self.instance.vehicles_model.model
        # предлози за полето „Марка и модел“ (<datalist>)
        self.model_suggestions = VehiclesModel.objects.order_by('model').values_list('model', flat=True)

    def save(self, commit=True):
        name = ' '.join(self.cleaned_data['model_name'].split())
        model = VehiclesModel.objects.filter(model__iexact=name).first() or VehiclesModel.objects.create(model=name)
        self.instance.vehicles_model = model
        return super().save(commit)


def unassigned_vehicles():
    """Возила што не се доделени на ниеден возач."""
    return VehicleOwnership.objects.exclude(
        vehicle_ownership_id__in=DriversVehicleOwnership.objects.values('vehicle_ownership_id')
    ).select_related('vehicle_type', 'vehicles_model')


def vehicle_label(v):
    return f'{v.plate} · {v.vehicle_type.description} · {v.vehicles_model.model} · {v.color}'


class VehicleChoiceForm(forms.Form):
    """Ново возило, постоечко (недоделено) возило или без возило."""
    MODES = [('new', 'Ново возило'), ('existing', 'Постоечко возило'), ('none', 'Без возило (подоцна)')]
    vehicle_mode = forms.ChoiceField(choices=MODES, initial='new', widget=forms.RadioSelect)
    existing_vehicle = forms.ModelChoiceField(label='Возило', queryset=None, required=False)

    def __init__(self, *args, allow_none=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['existing_vehicle'].queryset = unassigned_vehicles()
        self.fields['existing_vehicle'].label_from_instance = vehicle_label
        if not allow_none:
            self.fields['vehicle_mode'].choices = self.MODES[:2]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('vehicle_mode') == 'existing' and not cleaned.get('existing_vehicle'):
            self.add_error('existing_vehicle', 'Изберете возило.')
        return cleaned


class PayoutForm(forms.Form):
    amount = forms.DecimalField(label='Износ (ден.)', max_digits=10, decimal_places=2, min_value=0.01)
    note = forms.CharField(label='Забелешка', max_length=255, required=False,
                           widget=forms.TextInput(attrs={'placeholder': 'на пр. уплатено во готовина'}))



class CommissionForm(forms.Form):
    commission_percentage = forms.DecimalField(label='Провизија за GEOCAB (%)', max_digits=5, decimal_places=2,
                                               min_value=0, max_value=100)


class PriceForm(forms.Form):
    base_price = forms.DecimalField(label='Почетна цена', max_digits=10, decimal_places=2, min_value=0)
    price_per_km = forms.DecimalField(label='Цена по km', max_digits=10, decimal_places=2, min_value=0)


class NewVehicleClassForm(PriceForm):
    """Нова класа на возило (Vehicle_types.description) со своја тарифа."""
    description = forms.CharField(label='Класа (ја гледа корисникот)', max_length=255,
                                  widget=forms.TextInput(attrs={'placeholder': 'на пр. Van'}))
    type_name = forms.CharField(label='Тип на возило', max_length=255, widget=forms.TextInput(attrs={'placeholder': 'на пр. Minivan'}))

    field_order = ['description', 'type_name', 'base_price', 'price_per_km']

    def clean_description(self):
        # функциите ја бараат класата по description, па мора да е единствена
        description = self.cleaned_data['description'].strip()
        if VehicleTypes.objects.filter(description__iexact=description).exists():
            raise forms.ValidationError('Таа класа веќе постои.')
        return description



# ---- Лозинки --------------------------------------------------------------------------

class NewPasswordForm(forms.Form):
    new_password1 = forms.CharField(label='Нова лозинка', widget=forms.PasswordInput)
    new_password2 = forms.CharField(label='Потврди нова лозинка', widget=forms.PasswordInput)

    def clean_new_password1(self):
        password = self.cleaned_data['new_password1']
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('new_password1') and cleaned.get('new_password1') != cleaned.get('new_password2'):
            self.add_error('new_password2', 'Лозинките не се совпаѓаат.')
        return cleaned


class ChangePasswordForm(NewPasswordForm):
    old_password = forms.CharField(label='Сегашна лозинка', widget=forms.PasswordInput)
    field_order = ['old_password', 'new_password1', 'new_password2']

    def __init__(self, *args, person=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.person = person

    def clean_old_password(self):
        if not check_password(self.cleaned_data['old_password'], self.person.password_hash):
            raise forms.ValidationError('Сегашната лозинка не е точна.')
        return self.cleaned_data['old_password']


class ForgotPasswordForm(forms.Form):
    role = forms.ChoiceField(label='Профил', initial='user', widget=forms.RadioSelect,
                             choices=[(key, label) for key, (_, label) in ROLES.items()])
    email = forms.CharField(label='Е-пошта', max_length=255, validators=[email_validator])


class AdminSetPasswordForm(forms.Form):
    """Админот поставува нова почетна лозинка (на пр. возачот ја заборавил)."""
    new_password = forms.CharField(label='Нова лозинка', widget=forms.PasswordInput)

    def clean_new_password(self):
        password = self.cleaned_data['new_password']
        validate_password(password)
        return password



# ---- Профил ---------------------------------------------------------------------------

PROFILE_LABELS = {'name': 'Име и презиме', 'username': 'Корисничко име', 'phone': 'Телефон',
                  'email': 'Е-пошта', 'license_number': 'Број на возачка дозвола'}


class ContactForm(forms.ModelForm):
    """Основа за профил: телефон и е-пошта со истите правила како CHECK во базата.
    Единственоста (unique) ја проверува ModelForm, без тековниот профил."""
    phone = forms.CharField(label='Телефон', max_length=255, validators=[phone_validator])
    email = forms.CharField(label='Е-пошта', max_length=255, validators=[email_validator])


def profile_form(model, fields):
    return forms.modelform_factory(model, form=ContactForm, fields=fields, labels=PROFILE_LABELS)
