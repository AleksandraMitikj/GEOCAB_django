"""
Тест податоци за развој:  python manage.py seed

Може да се пушти повеќе пати - постоечките редови не се дуплираат.
Не ги менува податоците што веќе ги имате внесено.

Тест профили (лозинка за сите: TEST_PASSWORD подолу):
    Администратор:  admin_test
    Возачи:         vozac_marko  (Škoda Octavia, Standard)
                    vozac_elena  (Mercedes E-Class, Premium)
    Корисници:      korisnik_ana, korisnik_petar
"""
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.db import transaction

from GEOCAB_APP import db
from GEOCAB_APP.models import (
    Admins, Drivers, PricingRules, Status, Users, VehicleOwnership, VehicleTypes, VehiclesModel,
)

TEST_PASSWORD = 'Geocab2026!'

CLASSES = [
    # класа (description), тип, почетна цена, цена по km
    ('Standard', 'Sedan', 100, 40),
    ('Premium', 'Sedan', 150, 60),
]
CARS = [
    # таблица, сообраќајна, боја, година, класа, марка и модел
    ('SK-1234-AB', 'TD-100001', 'бела', 2021, 'Standard', 'Škoda Octavia'),
    ('SK-5678-CD', 'TD-100002', 'црна', 2023, 'Premium', 'Mercedes E-Class'),
]
DRIVERS = [
    # име, username, телефон, е-пошта, возачка, таблица
    ('Марко Петровски', 'vozac_marko', '+38970100001', 'marko@geocab.test', 'VD-0001', 'SK-1234-AB'),
    ('Елена Стојанова', 'vozac_elena', '+38970100002', 'elena@geocab.test', 'VD-0002', 'SK-5678-CD'),
]
USERS = [
    ('Ана Николова', 'korisnik_ana', '+38971200001', 'ana@geocab.test'),
    ('Петар Јовановски', 'korisnik_petar', '+38971200002', 'petar@geocab.test'),
]


class Command(BaseCommand):
    help = 'Внесува тест податоци (возила, цени, админ, возачи, корисници).'

    @transaction.atomic
    def handle(self, *args, **options):
        password_hash = make_password(TEST_PASSWORD)
        status = lambda tip, text: Status.objects.get(status_tip=tip, status_text=text)

        types = {}
        for vehicle_class, type_name, base, per_km in CLASSES:
            vt, _ = VehicleTypes.objects.get_or_create(description=vehicle_class, defaults={'type_name': type_name})
            PricingRules.objects.get_or_create(vehicle_type=vt, defaults={'base_price': base, 'price_per_km': per_km})
            types[vehicle_class] = vt

        cars = {}
        for plate, permit, color, year, vehicle_class, model in CARS:
            vm, _ = VehiclesModel.objects.get_or_create(model=model)
            cars[plate], _ = VehicleOwnership.objects.get_or_create(
                plate=plate, defaults={'traffic_permit': permit, 'color': color, 'year': year,
                                       'vehicle_type': types[vehicle_class], 'vehicles_model': vm})

        admin, _ = Admins.objects.get_or_create(
            username='admin_test', defaults={'name': 'Тест Администратор', 'phone': '+38970000001',
                                             'email': 'admin@geocab.test', 'password_hash': password_hash,
                                             'status': status('admin', 'active')})

        for name, username, phone, email, license_number, plate in DRIVERS:
            if not Drivers.objects.filter(username=username).exists():
                db.admin_create_driver(admin.pk, name, username, phone, email, password_hash,
                                       license_number, cars[plate].pk)

        for name, username, phone, email in USERS:
            Users.objects.get_or_create(
                username=username, defaults={'name': name, 'phone': phone, 'email': email,
                                             'password_hash': password_hash, 'status': status('user', 'active')})

        self.stdout.write(self.style.SUCCESS(
            f'Готово: {VehicleTypes.objects.count()} класи возила, {VehicleOwnership.objects.count()} возила, '
            f'{Admins.objects.count()} админ, {Drivers.objects.count()} возачи, {Users.objects.count()} корисници.'))
        self.stdout.write('Тест профилите и лозинката: GEOCAB_APP/management/commands/seed.py')
