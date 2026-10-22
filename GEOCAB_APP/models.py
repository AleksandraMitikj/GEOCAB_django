# Модели генерирани од постоечката база geocab_app (manage.py inspectdb).
# Табелите ги креира geocab_schema.sql, затоа сите модели се managed = False:
# Django само чита/запишува, никогаш не ги менува табелите со миграции.
# Текот на возењето (барање, прифаќање, плаќање...) оди преку SQL функциите
# и процедурите - види GEOCAB_APP/db.py.
from django.db import models
from django.db.models import F
from django.db.models.functions import Now, Round


class ActiveDrivers(models.Model):
    driver = models.OneToOneField('Drivers', models.DB_CASCADE, primary_key=True)
    current_location = models.ForeignKey('Locations', models.DO_NOTHING)
    is_available = models.BooleanField()
    working_start = models.DateTimeField()
    working_end = models.DateTimeField(blank=True, null=True)
    vehicle_ownership = models.ForeignKey('VehicleOwnership', models.DO_NOTHING)
    position_updated_at = models.DateTimeField(blank=True, null=True)  # последно GPS јавување

    class Meta:
        managed = False
        db_table = 'active_drivers'

    def __str__(self):
        return f'{self.driver} (available={self.is_available})'


class Admins(models.Model):
    admins_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    username = models.CharField(unique=True, max_length=255)
    phone = models.CharField(unique=True, max_length=255)
    email = models.CharField(unique=True, max_length=255)
    password_hash = models.TextField()
    created_at = models.DateTimeField(db_default=Now())
    status = models.ForeignKey('Status', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'admins'

    def __str__(self):
        return self.username


class Cancellations(models.Model):
    cancellation_id = models.AutoField(primary_key=True)
    cancelled_by_type = models.CharField(max_length=255)
    cancelled_by_id = models.IntegerField()
    reason = models.CharField(max_length=255)
    cancellation_fee = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(db_default=Now())
    ride = models.ForeignKey('Rides', models.DB_CASCADE)
    is_no_show = models.BooleanField(db_default=False)

    class Meta:
        managed = False
        db_table = 'cancellations'

    def __str__(self):
        return f'Ride {self.ride_id}: {self.reason}'


class CommissionPayouts(models.Model):
    """Уплата на провизија од возач кон GEOCAB (ја евидентира администратор)."""
    payout_id = models.AutoField(primary_key=True)
    driver = models.ForeignKey('Drivers', models.DO_NOTHING)
    admin = models.ForeignKey(Admins, models.DO_NOTHING)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_at = models.DateTimeField(db_default=Now())
    note = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'commission_payouts'

    def __str__(self):
        return f'{self.driver}: {self.amount} ден.'


class Drivers(models.Model):
    driver_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    username = models.CharField(unique=True, max_length=255)
    phone = models.CharField(unique=True, max_length=255)
    email = models.CharField(unique=True, max_length=255)
    password_hash = models.TextField()
    license_number = models.CharField(unique=True, max_length=255)
    registration_date = models.DateField()
    rating = models.DecimalField(max_digits=3, decimal_places=2)
    status = models.ForeignKey('Status', models.DO_NOTHING)
    created_at = models.DateTimeField(db_default=Now())
    created_by_admin = models.ForeignKey(Admins, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'drivers'

    def __str__(self):
        return self.username


class DriversVehicleOwnership(models.Model):
    driver = models.ForeignKey(Drivers, models.DB_CASCADE)
    vehicle_ownership = models.ForeignKey('VehicleOwnership', models.DB_CASCADE)

    class Meta:
        managed = False
        db_table = 'drivers_vehicle_ownership'
        unique_together = (('driver', 'vehicle_ownership'),)

    def __str__(self):
        return f'{self.driver} - {self.vehicle_ownership}'


class Locations(models.Model):
    location_id = models.AutoField(primary_key=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    grad = models.CharField(max_length=255)
    naselba = models.CharField(max_length=255)
    ulica = models.CharField(max_length=255)
    broj = models.CharField(max_length=255)
    created_at = models.DateTimeField(db_default=Now())
    # GEOGRAPHY(POINT) - го пополнува тригерот trg_locations_geom од latitude/longitude
    geom = models.TextField(blank=True, null=True, editable=False)

    class Meta:
        managed = False
        db_table = 'locations'

    def __str__(self):
        return f'{self.ulica} {self.broj}, {self.naselba}, {self.grad}'


class Messages(models.Model):
    message_id = models.AutoField(primary_key=True)
    message_text = models.CharField(unique=True, max_length=255)

    class Meta:
        managed = False
        db_table = 'messages'

    def __str__(self):
        return self.message_text


class Notifications(models.Model):
    notification_id = models.AutoField(primary_key=True)
    message = models.ForeignKey(Messages, models.DB_CASCADE)
    sent_time = models.DateTimeField()
    user = models.ForeignKey('Users', models.DB_CASCADE)
    ride = models.ForeignKey('Rides', models.DB_CASCADE)
    status = models.ForeignKey('Status', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'notifications'

    def __str__(self):
        return f'{self.user} - {self.message}'


class PaymentMethods(models.Model):
    payment_method_id = models.AutoField(primary_key=True)
    payment_tip = models.CharField(unique=True, max_length=255)

    class Meta:
        managed = False
        db_table = 'payment_methods'

    def __str__(self):
        return self.payment_tip


class Payments(models.Model):
    payment_id = models.AutoField(primary_key=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.ForeignKey(PaymentMethods, models.DO_NOTHING)
    payment_time = models.DateTimeField()
    ride = models.ForeignKey('Rides', models.DB_CASCADE)
    status = models.ForeignKey('Status', models.DO_NOTHING)
    # провизијата ја поставува тригерот trg_payment_commission при креирање на плаќањето
    commission_percentage = models.DecimalField(max_digits=5, decimal_places=2, db_default=0)
    platform_amount = models.GeneratedField(
        expression=Round(F('amount') * F('commission_percentage') / 100, 2),
        output_field=models.DecimalField(max_digits=10, decimal_places=2), db_persist=True)
    driver_amount = models.GeneratedField(
        expression=F('amount') - Round(F('amount') * F('commission_percentage') / 100, 2),
        output_field=models.DecimalField(max_digits=10, decimal_places=2), db_persist=True)

    class Meta:
        managed = False
        db_table = 'payments'

    def __str__(self):
        return f'Payment {self.payment_id} ({self.amount})'


class PlatformSettings(models.Model):
    """Секогаш еден ред (setting_id = 1). Провизијата важи за новите плаќања."""
    setting_id = models.IntegerField(primary_key=True, db_default=1)
    commission_percentage = models.DecimalField(max_digits=5, decimal_places=2, db_default=20)
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'platform_settings'

    def __str__(self):
        return f'Провизија {self.commission_percentage}%'


class PricingRules(models.Model):
    tariff_id = models.AutoField(primary_key=True)
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    price_per_km = models.DecimalField(max_digits=10, decimal_places=2)
    vehicle_type = models.ForeignKey('VehicleTypes', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'pricing_rules'

    def __str__(self):
        return f'{self.vehicle_type}: {self.base_price} + {self.price_per_km}/km'


class Ratings(models.Model):
    rating_id = models.AutoField(primary_key=True)
    score = models.IntegerField()
    comment = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(db_default=Now())
    ride = models.OneToOneField('Rides', models.DB_CASCADE)
    user = models.ForeignKey('Users', models.DB_CASCADE)
    driver = models.ForeignKey(Drivers, models.DB_CASCADE)

    class Meta:
        managed = False
        db_table = 'ratings'

    def __str__(self):
        return f'Ride {self.ride_id}: {self.score}'


class RideRejections(models.Model):
    ride = models.ForeignKey('Rides', models.DB_CASCADE)
    driver = models.ForeignKey(Drivers, models.DB_CASCADE)
    rejected_at = models.DateTimeField()
    reason = models.CharField(max_length=255, db_default='REJECTED')

    class Meta:
        managed = False
        db_table = 'ride_rejections'
        unique_together = (('ride', 'driver'),)

    def __str__(self):
        return f'Ride {self.ride_id} rejected by {self.driver}'


class Rides(models.Model):
    ride_id = models.AutoField(primary_key=True)
    user = models.ForeignKey('Users', models.DO_NOTHING)
    driver = models.ForeignKey(Drivers, models.DO_NOTHING, blank=True, null=True)
    vehicle_ownership = models.ForeignKey('VehicleOwnership', models.DO_NOTHING, blank=True, null=True)
    pickup_location = models.ForeignKey(Locations, models.DO_NOTHING, related_name='rides_pickup_location_set')
    dropoff_location = models.ForeignKey(Locations, models.DO_NOTHING, related_name='rides_dropoff_location_set')
    status = models.ForeignKey('Status', models.DO_NOTHING)
    requested_vehicle_class = models.CharField(max_length=255, blank=True, null=True)
    request_time = models.DateTimeField()
    offered_at = models.DateTimeField(blank=True, null=True)
    pickup_time = models.DateTimeField(blank=True, null=True)
    start_time = models.DateTimeField(blank=True, null=True)
    end_time = models.DateTimeField(blank=True, null=True)
    distance_km = models.DecimalField(max_digits=6, decimal_places=2)
    final_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, db_default=0)

    class Meta:
        managed = False
        db_table = 'rides'

    def __str__(self):
        return f'Ride {self.ride_id} ({self.status})'


class Status(models.Model):
    status_id = models.AutoField(primary_key=True)
    status_tip = models.CharField(max_length=255)
    status_text = models.CharField(max_length=255)

    class Meta:
        managed = False
        db_table = 'status'
        unique_together = (('status_tip', 'status_text'),)

    def __str__(self):
        return f'{self.status_tip}: {self.status_text}'


class Users(models.Model):
    user_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    username = models.CharField(unique=True, max_length=255)
    phone = models.CharField(unique=True, max_length=255)
    email = models.CharField(unique=True, max_length=255)
    password_hash = models.TextField()
    created_at = models.DateTimeField(db_default=Now())
    status = models.ForeignKey(Status, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'users'

    def __str__(self):
        return self.username


class VehicleOwnership(models.Model):
    vehicle_ownership_id = models.AutoField(primary_key=True)
    plate = models.CharField(unique=True, max_length=255)
    traffic_permit = models.CharField(unique=True, max_length=255)
    color = models.CharField(max_length=255)
    year = models.IntegerField()
    vehicle_type = models.ForeignKey('VehicleTypes', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'vehicle_ownership'

    def __str__(self):
        return self.plate


class VehicleTypes(models.Model):
    vehicle_type_id = models.AutoField(primary_key=True)
    type_name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)
    vehicles_model = models.ForeignKey('VehiclesModel', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'vehicle_types'

    def __str__(self):
        return self.type_name


class VehiclesModel(models.Model):
    vehicles_model_id = models.AutoField(primary_key=True)
    model = models.CharField(max_length=255)

    class Meta:
        managed = False
        db_table = 'vehicles_model'

    def __str__(self):
        return self.model



class RideReports(models.Model):
    """Пријава на проблем во тек на возење (sp_report_problem / sp_resolve_report)."""
    report_id = models.AutoField(primary_key=True)
    ride = models.ForeignKey(Rides, models.DB_CASCADE)
    reported_by_type = models.CharField(max_length=255)
    reported_by_id = models.IntegerField()
    category = models.CharField(max_length=255)
    message = models.CharField(max_length=1000)
    created_at = models.DateTimeField(db_default=Now())
    status = models.ForeignKey(Status, models.DO_NOTHING)
    resolved_by_admin = models.ForeignKey(Admins, models.DO_NOTHING, blank=True, null=True)
    resolved_at = models.DateTimeField(blank=True, null=True)
    admin_note = models.CharField(max_length=1000, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'ride_reports'

    def __str__(self):
        return f'Пријава {self.report_id} (возење {self.ride_id})'
