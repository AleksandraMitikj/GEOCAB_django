from datetime import datetime

from django import forms
from django.contrib import admin
from django.contrib.auth.hashers import make_password
from django.core.validators import MaxValueValidator, MinValueValidator

from . import db
from .forms import email_validator, phone_validator
from .models import (
    ActiveDrivers, Admins, Cancellations, CommissionPayouts, Drivers, DriversVehicleOwnership, Locations,
    Messages, Notifications, PaymentMethods, Payments, PlatformSettings, PricingRules, Ratings,
    RideRejections, RideReports, Rides, Status, Users, VehicleOwnership, VehicleTypes, VehiclesModel,
)

admin.site.site_header = 'GEOCAB администрација'
admin.site.site_title = 'GEOCAB'
admin.site.index_title = 'Табели'

class ReadOnlyAdmin(admin.ModelAdmin):
    """Само преглед. Овие табели ги пополнуваат SQL процедурите и тригерите,
    а рачна промена би го заобиколила текот на возењето."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---- Лица со лозинка -------------------------------------------------------

class PersonForm(forms.ModelForm):
    """Лозинката се внесува како обичен текст и се зачувува како hash во password_hash."""
    password = forms.CharField(label='Лозинка', widget=forms.PasswordInput, required=False,
                               help_text='При промена: оставете празно за да остане старата лозинка.')
    phone = forms.CharField(label='Телефон', max_length=255, validators=[phone_validator])
    email = forms.CharField(label='Е-пошта', max_length=255, validators=[email_validator])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['password'].required = True
            self.fields['password'].help_text = ''

    def save(self, commit=True):
        if self.cleaned_data.get('password'):
            self.instance.password_hash = make_password(self.cleaned_data['password'])
        return super().save(commit)


class PersonAdmin(admin.ModelAdmin):
    status_tip = None  # кој тип статуси се дозволени (trg_check_status_tip)
    list_display = ('username', 'name', 'email', 'phone', 'status', 'created_at')
    search_fields = ('username', 'name', 'email', 'phone')
    readonly_fields = ('created_at',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'status':
            kwargs['queryset'] = Status.objects.filter(status_tip=self.status_tip)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_list_filter(self, request):
        return (('status', admin.RelatedOnlyFieldListFilter),)


class UserForm(PersonForm):
    class Meta:
        model = Users
        fields = ('name', 'username', 'phone', 'email', 'password', 'status')


class AdminForm(PersonForm):
    class Meta:
        model = Admins
        fields = ('name', 'username', 'phone', 'email', 'password', 'status')


@admin.register(Users)
class UsersAdmin(PersonAdmin):
    form = UserForm
    status_tip = 'user'


@admin.register(Admins)
class AdminsAdmin(PersonAdmin):
    form = AdminForm
    status_tip = 'admin'


# ---- Возачи ----------------------------------------------------------------

class DriverAddForm(PersonForm):
    """Нов возач се креира преку fn_admin_create_driver (статус, датум и оценка ги поставува функцијата)."""
    created_by_admin = forms.ModelChoiceField(
        label='Креиран од администратор',
        queryset=Admins.objects.filter(status__status_tip='admin', status__status_text='active'),
        help_text='Само активни администратори.')
    vehicle_ownership = forms.ModelChoiceField(
        label='Возило', queryset=VehicleOwnership.objects.all(), required=False,
        help_text='Опционално: возило што веднаш се доделува на возачот.')

    class Meta:
        model = Drivers
        fields = ('name', 'username', 'phone', 'email', 'password', 'license_number')


class DriverChangeForm(PersonForm):
    class Meta:
        model = Drivers
        fields = ('name', 'username', 'phone', 'email', 'password', 'license_number', 'status')


@admin.register(Drivers)
class DriversAdmin(PersonAdmin):
    status_tip = 'driver'
    list_display = ('username', 'name', 'phone', 'license_number', 'rating', 'status', 'registration_date')
    search_fields = ('username', 'name', 'email', 'phone', 'license_number')

    def get_form(self, request, obj=None, **kwargs):
        kwargs['form'] = DriverChangeForm if obj else DriverAddForm
        return super().get_form(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('rating', 'registration_date', 'created_by_admin', 'created_at')
        return ()

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        data = form.cleaned_data
        vehicle = data.get('vehicle_ownership')
        obj.pk = db.admin_create_driver(
            data['created_by_admin'].pk, data['name'], data['username'], data['phone'], data['email'],
            make_password(data['password']), data['license_number'], vehicle.pk if vehicle else None)
        obj.refresh_from_db()


# ---- Возила и цени (слободно за уредување) ----------------------------------

@admin.register(VehiclesModel)
class VehiclesModelAdmin(admin.ModelAdmin):
    list_display = ('vehicles_model_id', 'model')
    search_fields = ('model',)


@admin.register(VehicleTypes)
class VehicleTypesAdmin(admin.ModelAdmin):
    list_display = ('vehicle_type_id', 'type_name', 'description', 'vehicles_model')
    list_filter = ('type_name',)
    search_fields = ('type_name', 'description')


class PricingRuleForm(forms.ModelForm):
    base_price = forms.DecimalField(label='Почетна цена', max_digits=10, decimal_places=2,
                                    validators=[MinValueValidator(0)])
    price_per_km = forms.DecimalField(label='Цена по km', max_digits=10, decimal_places=2,
                                      validators=[MinValueValidator(0)])

    class Meta:
        model = PricingRules
        fields = '__all__'


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    """Еден ред со провизијата: може само да се менува, не да се додава или брише."""
    list_display = ('__str__', 'updated_at')
    fields = ('commission_percentage', 'updated_at')
    readonly_fields = ('updated_at',)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'commission_percentage':
            field.label = 'Провизија за GEOCAB (%)'
            field.min_value, field.max_value = 0, 100
            field.validators += [MinValueValidator(0), MaxValueValidator(100)]
            field.help_text = 'Возачот го добива остатокот. Важи само за новите плаќања.'
        return field

    def save_model(self, request, obj, form, change):
        obj.updated_at = datetime.now()
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PricingRules)
class PricingRulesAdmin(admin.ModelAdmin):
    form = PricingRuleForm
    list_display = ('tariff_id', 'vehicle_type', 'base_price', 'price_per_km')


class VehicleOwnershipForm(forms.ModelForm):
    year = forms.IntegerField(label='Година', validators=[MinValueValidator(1900)])

    class Meta:
        model = VehicleOwnership
        fields = '__all__'


@admin.register(VehicleOwnership)
class VehicleOwnershipAdmin(admin.ModelAdmin):
    form = VehicleOwnershipForm
    list_display = ('plate', 'vehicle_type', 'color', 'year', 'traffic_permit')
    list_filter = ('vehicle_type',)
    search_fields = ('plate', 'traffic_permit')


@admin.register(DriversVehicleOwnership)
class DriversVehicleOwnershipAdmin(admin.ModelAdmin):
    list_display = ('driver', 'vehicle_ownership')
    search_fields = ('driver__username', 'vehicle_ownership__plate')


@admin.register(Locations)
class LocationsAdmin(admin.ModelAdmin):
    list_display = ('location_id', 'grad', 'naselba', 'ulica', 'broj', 'latitude', 'longitude')
    list_filter = ('grad',)
    search_fields = ('grad', 'naselba', 'ulica')
    readonly_fields = ('created_at',)


# ---- Шифрарници (функциите ги бараат по текст - не смеат да се менуваат) -----

@admin.register(Status)
class StatusAdmin(ReadOnlyAdmin):
    list_display = ('status_id', 'status_tip', 'status_text')
    list_filter = ('status_tip',)


@admin.register(Messages)
class MessagesAdmin(ReadOnlyAdmin):
    list_display = ('message_id', 'message_text')


@admin.register(PaymentMethods)
class PaymentMethodsAdmin(ReadOnlyAdmin):
    list_display = ('payment_method_id', 'payment_tip')


# ---- Тек на возењето (само преглед) ------------------------------------------

@admin.register(Rides)
class RidesAdmin(ReadOnlyAdmin):
    list_display = ('ride_id', 'user', 'driver', 'status', 'pickup_location', 'dropoff_location',
                    'request_time', 'distance_km', 'final_price')
    list_filter = (('status', admin.RelatedOnlyFieldListFilter), 'requested_vehicle_class')
    search_fields = ('user__username', 'driver__username')
    list_select_related = ('user', 'driver', 'status', 'pickup_location', 'dropoff_location')
    ordering = ('-ride_id',)


@admin.register(ActiveDrivers)
class ActiveDriversAdmin(ReadOnlyAdmin):
    list_display = ('driver', 'is_available', 'current_location', 'vehicle_ownership', 'working_start', 'working_end')
    list_filter = ('is_available',)


@admin.register(Payments)
class PaymentsAdmin(ReadOnlyAdmin):
    list_display = ('payment_id', 'ride', 'amount', 'commission_percentage', 'platform_amount', 'driver_amount',
                    'payment_method', 'status', 'payment_time')
    list_filter = ('payment_method', ('status', admin.RelatedOnlyFieldListFilter))
    ordering = ('-payment_id',)


@admin.register(Cancellations)
class CancellationsAdmin(ReadOnlyAdmin):
    list_display = ('cancellation_id', 'ride', 'cancelled_by_type', 'reason', 'cancellation_fee', 'is_no_show', 'created_at')
    list_filter = ('cancelled_by_type', 'is_no_show')
    ordering = ('-cancellation_id',)


@admin.register(RideRejections)
class RideRejectionsAdmin(ReadOnlyAdmin):
    list_display = ('ride', 'driver', 'reason', 'rejected_at')
    list_filter = ('reason',)


@admin.register(Ratings)
class RatingsAdmin(ReadOnlyAdmin):
    list_display = ('rating_id', 'ride', 'user', 'driver', 'score', 'comment', 'created_at')
    list_filter = ('score',)


@admin.register(Notifications)
class NotificationsAdmin(ReadOnlyAdmin):
    list_display = ('notification_id', 'user', 'ride', 'message', 'status', 'sent_time')
    ordering = ('-notification_id',)


@admin.register(CommissionPayouts)
class CommissionPayoutsAdmin(ReadOnlyAdmin):
    """Уплатите се евидентираат од админ панелот во апликацијата (sp_record_commission_payout)."""
    list_display = ('payout_id', 'driver', 'amount', 'paid_at', 'admin', 'note')
    list_filter = ('driver',)
    ordering = ('-payout_id',)


@admin.register(RideReports)
class RideReportsAdmin(ReadOnlyAdmin):
    list_display = ('report_id', 'ride', 'reported_by_type', 'category', 'status', 'created_at')
    list_filter = ('category', 'reported_by_type')
    ordering = ('-report_id',)
