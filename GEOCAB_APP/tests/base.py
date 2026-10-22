"""
Заеднички податоци и помошни функции за тестовите.

Секој тест работи во своја трансакција што на крајот се враќа назад,
па тестовите не си пречат и тест базата секогаш почнува празна (само шифрарниците).
Сервисот за рути (OSRM) е заменет со фиксно растојание - тестовите не бараат интернет.
"""
import re
from decimal import Decimal
from itertools import count
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.db import connection
from django.test import Client, TestCase

from GEOCAB_APP import db
from GEOCAB_APP.models import (Admins, Drivers, Locations, Payments, PricingRules, Rides, Status, Users,
                               VehicleOwnership, VehicleTypes, VehiclesModel)

PASSWORD = 'Geocab2026!test'
ROAD_KM = Decimal('3.79')   # растојание „по пат“ што го враќа лажниот OSRM

# Центар -> Карпош (воздушно ~3.45 km)
RIDE = dict(pickup_lat='41.996000', pickup_lng='21.431000', pickup_grad='Скопје', pickup_naselba='Центар',
            pickup_ulica='Македонија', pickup_broj='1',
            dropoff_lat='42.004500', dropoff_lng='21.391000', dropoff_grad='Скопје', dropoff_naselba='Карпош',
            dropoff_ulica='Партизанска', dropoff_broj='5')
SHIFT_LOCATION = dict(loc_lat='41.996500', loc_lng='21.432000', loc_grad='Скопје', loc_naselba='Центар',
                      loc_ulica='Плоштад', loc_broj='1')

_ids = count(1)


def status(tip, text):
    return Status.objects.get(status_tip=tip, status_text=text)


def messages_of(response):
    """Текстови од Bootstrap пораките (alert) на страницата."""
    return [m.strip() for m in re.findall(r'class="alert alert-\w+[^"]*"[^>]*>\s*([^<]+?)\s*<', response.content.decode())]


def field_errors(response):
    return re.findall(r'invalid-feedback">([^<]+)<', response.content.decode())


class GeocabTestCase(TestCase):
    """Основа: класа на возило со цена, админ, и помошни функции за корисник/возач/возење."""

    def setUp(self):
        patcher = mock.patch('GEOCAB_APP.routing._road_km', return_value=ROAD_KM)
        self.osrm = patcher.start()
        self.addCleanup(patcher.stop)

        self.model = VehiclesModel.objects.create(model='Škoda Octavia')
        self.standard = VehicleTypes.objects.create(type_name='Sedan', description='Standard')
        self.premium = VehicleTypes.objects.create(type_name='Sedan', description='Premium')
        PricingRules.objects.create(base_price=100, price_per_km=40, vehicle_type=self.standard)
        PricingRules.objects.create(base_price=150, price_per_km=60, vehicle_type=self.premium)
        self.admin = self.make_admin('admin')

    # ---- податоци ---------------------------------------------------------------

    def make_admin(self, username, active=True):
        n = next(_ids)
        return Admins.objects.create(name=f'Админ {username}', username=username, phone=f'07100{n:04d}',
                                     email=f'{username}@test.mk', password_hash=make_password(PASSWORD),
                                     status=status('admin', 'active' if active else 'inactive'))

    def make_user(self, username='ana', name=None):
        n = next(_ids)
        return Users.objects.create(name=name or f'Корисник {username}', username=username, phone=f'07200{n:04d}',
                                    email=f'{username}@test.mk', password_hash=make_password(PASSWORD),
                                    status=status('user', 'active'))

    def make_vehicle(self, vehicle_type=None, plate=None):
        n = next(_ids)
        return VehicleOwnership.objects.create(plate=plate or f'SK-{n:04d}-TT', traffic_permit=f'TD-{n:05d}',
                                               color='бела', year=2020, vehicle_type=vehicle_type or self.standard,
                                               vehicles_model=self.model)

    def make_driver(self, username='marko', vehicle=True, vehicle_type=None):
        n = next(_ids)
        vo = self.make_vehicle(vehicle_type) if vehicle else None
        driver_id = db.admin_create_driver(self.admin.pk, f'Возач {username}', username, f'07300{n:04d}',
                                           f'{username}@test.mk', make_password(PASSWORD), f'L-{n:05d}',
                                           vo.pk if vo else None)
        driver = Drivers.objects.get(pk=driver_id)
        driver.vehicle = vo
        return driver

    def location(self, lat=41.9965, lng=21.432, ulica='Плоштад'):
        return db.create_location(lat, lng, 'Скопје', 'Центар', ulica, '1')

    def start_shift(self, driver, lat=41.9965, lng=21.432):
        db.driver_start_shift(driver.pk, self.location(lat, lng), driver.vehicle.pk)

    # ---- најава -------------------------------------------------------------------

    def login(self, role, username, password=PASSWORD):
        client = Client()
        response = client.post('/login/', {'role': role, 'username': username, 'password': password})
        assert response.status_code == 302, f'најавата не успеа за {role} {username}'
        return client

    # ---- возење ---------------------------------------------------------------------

    def request_ride(self, client, vehicle_class='Standard', **overrides):
        return client.post('/ride/new/', {'action': 'request', **RIDE, 'vehicle_class': vehicle_class, **overrides})

    def latest_ride(self):
        return Rides.objects.select_related('status').latest('ride_id')

    def drive(self, driver_client, ride, steps=('accept', 'arrived', 'start', 'complete')):
        for step in steps:
            driver_client.post('/driver/action/', {'action': step, 'ride_id': ride.pk})
        ride.refresh_from_db()
        return ride

    def full_ride(self, user_client, driver_client, pay=True):
        """Барање -> прифати -> пристигнав -> почни -> заврши (-> готовина). Враќа возењето."""
        self.request_ride(user_client)
        ride = self.drive(driver_client, self.latest_ride())
        if pay:
            driver_client.post('/driver/action/', {'action': 'cash_received', 'ride_id': str(ride.pk)})
        return ride

    def payment(self, ride):
        return Payments.objects.select_related('status').filter(ride=ride).latest('payment_id')

    def ride_status(self, ride):
        return Rides.objects.select_related('status').get(pk=ride.pk).status.status_text

    def sql(self, query, params=None):
        with connection.cursor() as cursor:
            cursor.execute(query, params)

    def age_offer(self, ride, seconds):
        """Понудата е дадена пред `seconds` секунди."""
        self.sql("UPDATE rides SET offered_at = now() - make_interval(secs => %s) WHERE ride_id = %s", [seconds, ride.pk])

    def set_payment_time(self, ride, when):
        self.sql('UPDATE payments SET payment_time = %s WHERE ride_id = %s', [when, ride.pk])

    def locations_count(self):
        return Locations.objects.count()
