"""Админ панел (/manage/), Django /admin/, правила во базата, seed, рути, и проверка на сите линкови."""
import re
from collections import deque
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User as DjangoUser
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import Client, TestCase
from django.urls import Resolver404, resolve

from GEOCAB_APP import db, routing
from GEOCAB_APP.models import (Admins, Cancellations, Drivers, DriversVehicleOwnership, Payments, PlatformSettings,
                               PricingRules, Rides, Status, Users, VehicleOwnership, VehicleTypes)

from .base import PASSWORD, GeocabTestCase, field_errors, messages_of, status

NEW_DRIVER = dict(name='Трајко Трајковски', username='trajko', phone='+38970555111', email='trajko@test.mk',
                  license_number='VD-9001', password='Trajko2026!x')


class ManageDriversTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.client = self.login('admin', 'admin')

    def test_dashboard(self):
        response = self.client.get('/manage/')
        for key in ('today', 'month', 'money', 'on_shift', 'outstanding', 'debtors', 'recent_rides', 'open_reports'):
            self.assertIn(key, response.context)

    def test_new_driver_with_new_vehicle(self):
        response = self.client.post('/manage/drivers/new/', {**NEW_DRIVER, 'vehicle_mode': 'new', 'v-plate': 'SK-9001-TT',
                                                              'v-traffic_permit': 'TD-9001', 'v-vehicle_type': self.standard.pk,
                                                              'v-color': 'сива', 'v-year': '2019'}, follow=True)
        driver = Drivers.objects.get(username='trajko')
        self.assertTrue(any('е регистриран' in m for m in messages_of(response)))
        self.assertEqual((driver.status.status_text, driver.rating, driver.created_by_admin_id), ('active', Decimal('5.00'), self.admin.pk))
        self.assertTrue(DriversVehicleOwnership.objects.filter(driver=driver, vehicle_ownership__plate='SK-9001-TT').exists())
        self.login('driver', 'trajko', 'Trajko2026!x')

    def test_new_driver_with_existing_or_no_vehicle(self):
        free = self.make_vehicle()
        self.client.post('/manage/drivers/new/', {**NEW_DRIVER, 'vehicle_mode': 'existing', 'existing_vehicle': free.pk})
        self.assertTrue(DriversVehicleOwnership.objects.filter(vehicle_ownership=free, driver__username='trajko').exists())
        self.client.post('/manage/drivers/new/', {**NEW_DRIVER, 'username': 'bez', 'phone': '070555112', 'email': 'bez@test.mk',
                                                  'license_number': 'VD-9002', 'vehicle_mode': 'none'})
        self.assertFalse(DriversVehicleOwnership.objects.filter(driver__username='bez').exists())

    def test_new_driver_validation_creates_nothing(self):
        self.make_driver('marko')
        response = self.client.post('/manage/drivers/new/', {**NEW_DRIVER, 'username': 'marko', 'phone': 'abc', 'password': '123',
                                                              'vehicle_mode': 'new', 'v-plate': ''})
        errors = field_errors(response)
        self.assertIn('Телефонот смее да содржи само бројки и +.', errors)
        self.assertTrue(any('already exists' in e for e in errors))
        self.assertFalse(Drivers.objects.filter(name=NEW_DRIVER['name']).exists())
        response = self.client.post('/manage/drivers/new/', {**NEW_DRIVER, 'vehicle_mode': 'existing'})
        self.assertIn('Изберете возило.', response.content.decode())          # видливо и кога нема слободни возила
        self.assertNotIn('This field is required.', field_errors(response))  # без грешки од формата за ново возило

    def test_only_unassigned_vehicles_offered(self):
        driver = self.make_driver('marko')
        free = self.make_vehicle()
        choices = list(self.client.get('/manage/drivers/new/').context['vehicle_choice'].fields['existing_vehicle'].queryset)
        self.assertIn(free, choices)
        self.assertNotIn(driver.vehicle, choices)

    def test_drivers_list_search_and_filters(self):
        driver = self.make_driver('marko')
        self.make_driver('elena')
        rows = self.client.get(f'/manage/drivers/?q={driver.vehicle.plate}').context['rows']
        self.assertEqual([r['driver'].username for r in rows], ['marko'])
        self.assertEqual(self.client.get('/manage/drivers/?debt=1').context['rows'], [])

    def test_edit_driver(self):
        driver = self.make_driver('marko')
        url = f'/manage/drivers/{driver.pk}/action/'
        self.client.post(url, {'action': 'edit', 'name': 'Марко Нов', 'username': 'marko', 'phone': driver.phone,
                               'email': driver.email, 'license_number': 'L-NEW'})
        driver.refresh_from_db()
        self.assertEqual((driver.name, driver.license_number), ('Марко Нов', 'L-NEW'))
        self.make_driver('elena')
        response = self.client.post(url, {'action': 'edit', 'name': 'X', 'username': 'elena', 'phone': '1', 'email': 'a@b.mk',
                                          'license_number': 'L-NEW'}, follow=True)
        self.assertTrue(any('Не е зачувано' in m for m in messages_of(response)))

    def test_block_and_unblock_driver(self):
        driver = self.make_driver('marko')
        self.start_shift(driver)
        driver_client = self.login('driver', 'marko')
        self.client.post(f'/manage/drivers/{driver.pk}/action/', {'action': 'block'})
        driver.refresh_from_db()
        self.assertEqual(driver.status.status_text, 'inactive')
        self.assertTrue(driver_client.get('/driver/').url.startswith('/login/'))   # исфрлен
        self.client.post(f'/manage/drivers/{driver.pk}/action/', {'action': 'unblock'})
        self.login('driver', 'marko')

    def test_cannot_block_driver_during_ride(self):
        driver = self.make_driver('marko')
        self.start_shift(driver)
        self.make_user('ana')
        self.request_ride(self.login('user', 'ana'))
        self.drive(self.login('driver', 'marko'), self.latest_ride(), ('accept',))
        response = self.client.post(f'/manage/drivers/{driver.pk}/action/', {'action': 'block'}, follow=True)
        self.assertTrue(any('има активно возење' in m for m in messages_of(response)))
        self.assertEqual(Drivers.objects.get(pk=driver.pk).status.status_text, 'active')

    def test_vehicles_add_edit_remove(self):
        driver = self.make_driver('marko')
        url = f'/manage/drivers/{driver.pk}/action/'
        self.client.post(url, {'action': 'add_vehicle', 'vehicle_mode': 'new', 'v-plate': 'SK-2222-AB', 'v-traffic_permit': 'TD-2222',
                               'v-vehicle_type': self.premium.pk, 'v-color': 'црна', 'v-year': '2022'})
        self.assertEqual(DriversVehicleOwnership.objects.filter(driver=driver).count(), 2)
        response = self.client.post(url, {'action': 'add_vehicle', 'vehicle_mode': 'new', 'v-plate': 'SK-2222-AB', 'v-traffic_permit': 'X',
                                          'v-vehicle_type': self.premium.pk, 'v-color': 'c', 'v-year': '2022'}, follow=True)
        self.assertTrue(any('Возилото не е зачувано' in m for m in messages_of(response)))
        vehicle = VehicleOwnership.objects.get(plate='SK-2222-AB')
        self.assertTrue(any('1900' in e for e in field_errors(self.client.post(f'/manage/vehicles/{vehicle.pk}/?driver={driver.pk}',
                        {'plate': 'SK-2222-AB', 'traffic_permit': 'TD-2222', 'vehicle_type': self.premium.pk, 'color': 'c', 'year': '1800'}))))
        response = self.client.post(f'/manage/vehicles/{vehicle.pk}/?driver={driver.pk}', {'plate': 'SK-3333-AB', 'traffic_permit': 'TD-2222',
                                    'vehicle_type': self.premium.pk, 'color': 'бела', 'year': '2023'})
        self.assertEqual(response.url, f'/manage/drivers/{driver.pk}/')
        self.start_shift(driver)
        response = self.client.post(url, {'action': 'remove_vehicle', 'vehicle_id': driver.vehicle.pk}, follow=True)
        self.assertTrue(any('моментално вози' in m for m in messages_of(response)))
        self.client.post(url, {'action': 'remove_vehicle', 'vehicle_id': vehicle.pk})
        self.assertFalse(DriversVehicleOwnership.objects.filter(driver=driver, vehicle_ownership=vehicle).exists())
        self.assertTrue(VehicleOwnership.objects.filter(pk=vehicle.pk).exists())


class ManageRidesTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.client = self.login('admin', 'admin')
        self.make_user('ana')
        self.user = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.driver_client = self.login('driver', 'marko')

    def test_list_filters(self):
        done = self.full_ride(self.user, self.driver_client)
        self.request_ride(self.user, vehicle_class='Premium')   # no_driver
        def ids(query):
            return [r['ride_id'] for r in self.client.get(f'/manage/rides/{query}').context['page']]
        self.assertEqual(ids('?status=completed'), [done.pk])
        self.assertEqual(len(ids('?status=no_driver')), 1)
        self.assertEqual(ids(f'?q={done.pk}'), [done.pk])
        self.assertEqual(ids(f'?q={self.driver.vehicle.plate}'), [done.pk])
        self.assertEqual(ids('?to=2000-01-01'), [])
        self.assertEqual(len(ids('?from=losdatum')), 2)

    def test_emergency_cancel_even_in_progress(self):
        self.request_ride(self.user)
        ride = self.drive(self.driver_client, self.latest_ride(), ('accept', 'arrived', 'start'))
        self.assertIn('Итно откажување', self.client.get(f'/manage/rides/{ride.pk}/').content.decode())
        response = self.client.post(f'/manage/rides/{ride.pk}/action/', {'action': 'cancel', 'reason': ''}, follow=True)
        self.assertIn('Внесете причина за откажувањето.', messages_of(response))
        self.client.post(f'/manage/rides/{ride.pk}/action/', {'action': 'cancel', 'reason': 'Незгода'})
        cancellation = Cancellations.objects.get(ride=ride)
        self.assertEqual((self.ride_status(ride), cancellation.cancelled_by_type, cancellation.cancellation_fee), ('cancelled', 'ADMIN', 0))
        from GEOCAB_APP.models import ActiveDrivers
        self.assertTrue(ActiveDrivers.objects.get(driver=self.driver).is_available)

    def test_completed_ride_cannot_be_cancelled(self):
        ride = self.full_ride(self.user, self.driver_client)
        self.assertNotIn('Итно откажување', self.client.get(f'/manage/rides/{ride.pk}/').content.decode())
        response = self.client.post(f'/manage/rides/{ride.pk}/action/', {'action': 'cancel', 'reason': 'x'}, follow=True)
        self.assertTrue(any('Недозволен премин' in m for m in messages_of(response)))

    def test_refund(self):
        ride = self.full_ride(self.user, self.driver_client)
        payment = Payments.objects.get(ride=ride)
        response = self.client.post(f'/manage/rides/{ride.pk}/action/', {'action': 'refund', 'payment_id': payment.pk}, follow=True)
        self.assertIn('Вратени се 251.60 ден. на корисникот.', messages_of(response))
        response = self.client.post(f'/manage/rides/{ride.pk}/action/', {'action': 'refund', 'payment_id': payment.pk}, follow=True)
        self.assertTrue(any('Може да се вратат само платени плаќања' in m for m in messages_of(response)))
        self.assertEqual(self.client.post(f'/manage/rides/{ride.pk}/action/', {'action': 'refund', 'payment_id': 999999}).status_code, 404)

    def test_unknown_ride(self):
        response = self.client.get('/manage/rides/999999/', follow=True)
        self.assertIn('Возењето #999999 не постои.', messages_of(response))

    def test_ride_detail_timeline(self):
        ride = self.full_ride(self.user, self.driver_client)
        labels = [label for label, _ in self.client.get(f'/manage/rides/{ride.pk}/').context['timeline']]
        self.assertEqual(labels, ['Побарано', 'Возачот пристигна', 'Возењето почна', 'Возењето заврши'])


class ManageSettingsAndUsersTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.client = self.login('admin', 'admin')

    def test_commission_setting(self):
        self.assertEqual(self.client.post('/manage/settings/', {'action': 'commission', 'commission_percentage': '120'}).status_code, 200)
        self.assertEqual(PlatformSettings.objects.get(pk=1).commission_percentage, 20)
        response = self.client.post('/manage/settings/', {'action': 'commission', 'commission_percentage': '25'}, follow=True)
        self.assertIn('Провизијата е 25%. Важи за новите плаќања.', messages_of(response))

    def test_prices_and_new_class(self):
        self.client.post('/manage/settings/', {'action': 'price', 'vehicle_type_id': self.standard.pk, 'base_price': '120', 'price_per_km': '45'})
        rule = PricingRules.objects.get(vehicle_type=self.standard)
        self.assertEqual((rule.base_price, rule.price_per_km), (Decimal('120.00'), Decimal('45.00')))
        response = self.client.post('/manage/settings/', {'action': 'price', 'vehicle_type_id': self.standard.pk, 'base_price': '-1', 'price_per_km': '1'}, follow=True)
        self.assertIn('Цените мора да се броеви поголеми или еднакви на 0.', messages_of(response))
        self.client.post('/manage/settings/', {'action': 'new_class', 'description': 'Van', 'type_name': 'Minivan', 'model': 'VW Transporter',
                                               'base_price': '200', 'price_per_km': '70'})
        self.assertTrue(PricingRules.objects.filter(vehicle_type__description='Van', base_price=200).exists())
        response = self.client.post('/manage/settings/', {'action': 'new_class', 'description': 'van', 'type_name': 'X', 'model': 'Y',
                                                          'base_price': '1', 'price_per_km': '1'})
        self.assertIn('Таа класа веќе постои.', field_errors(response))
        self.make_user('ana')
        classes = [e['vehicle_class'] for e in self.login('user', 'ana').post('/ride/new/', {'action': 'estimate', **__import__('GEOCAB_APP.tests.base', fromlist=['RIDE']).RIDE}).context['estimates']]
        self.assertIn('Van', classes)

    def test_users_list_block_and_password(self):
        ana = self.make_user('ana')
        ana_client = self.login('user', 'ana')
        row = self.client.get('/manage/users/?q=ana').context['page'][0]
        self.assertEqual((row['user'].username, row['user'].completed, row['spent']), ('ana', 0, 0))
        response = self.client.post(f'/manage/users/{ana.pk}/action/', {'action': 'block', 'next': 'https://evil.com'})
        self.assertEqual(response.url, '/manage/users/')
        self.assertEqual(Users.objects.get(pk=ana.pk).status.status_text, 'blocked')
        self.assertTrue(ana_client.get('/ride/new/').url.startswith('/login/'))
        self.client.post(f'/manage/users/{ana.pk}/action/', {'action': 'unblock'})
        self.login('user', 'ana')


class DjangoAdminTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.superuser = DjangoUser.objects.create_superuser('dev', 'dev@test.mk', 'x')
        self.client = Client()
        self.client.force_login(self.superuser)

    def test_all_changelists_open(self):
        for model in ('users', 'admins', 'drivers', 'vehiclesmodel', 'vehicletypes', 'pricingrules', 'vehicleownership',
                      'driversvehicleownership', 'locations', 'status', 'messages', 'paymentmethods', 'rides', 'activedrivers',
                      'payments', 'cancellations', 'riderejections', 'ratings', 'notifications', 'platformsettings',
                      'commissionpayouts', 'ridereports'):
            self.assertEqual(self.client.get(f'/admin/GEOCAB_APP/{model}/').status_code, 200, model)

    def test_read_only_tables(self):
        for model in ('status', 'messages', 'paymentmethods', 'rides', 'payments', 'cancellations', 'commissionpayouts', 'ridereports'):
            self.assertEqual(self.client.get(f'/admin/GEOCAB_APP/{model}/add/').status_code, 403, model)
        self.assertEqual(self.client.get('/admin/GEOCAB_APP/platformsettings/add/').status_code, 403)
        self.assertEqual(self.client.get('/admin/GEOCAB_APP/platformsettings/1/delete/').status_code, 403)

    def test_add_admin_hashes_password_and_limits_status(self):
        response = self.client.get('/admin/GEOCAB_APP/admins/add/')
        statuses = list(response.context['adminform'].form.fields['status'].queryset.values_list('status_tip', flat=True))
        self.assertEqual(set(statuses), {'admin'})
        self.client.post('/admin/GEOCAB_APP/admins/add/', {'name': 'Марија', 'username': 'marija', 'phone': '070123123',
                                                           'email': 'marija@test.mk', 'password': 'Marija2026!x',
                                                           'status': status('admin', 'active').pk})
        self.login('admin', 'marija', 'Marija2026!x')
        self.assertNotIn('Marija2026', Admins.objects.get(username='marija').password_hash)

    def test_add_driver_uses_database_function(self):
        vehicle = self.make_vehicle()
        self.client.post('/admin/GEOCAB_APP/drivers/add/', {'name': 'Возач', 'username': 'novdj', 'phone': '070321321', 'email': 'n@test.mk',
                                                            'password': 'Vozac2026!x', 'license_number': 'L-DJ', 'created_by_admin': self.admin.pk,
                                                            'vehicle_ownership': vehicle.pk})
        driver = Drivers.objects.get(username='novdj')
        self.assertEqual((driver.rating, driver.status.status_text), (Decimal('5.00'), 'active'))
        self.assertTrue(DriversVehicleOwnership.objects.filter(driver=driver, vehicle_ownership=vehicle).exists())


class DatabaseRulesTest(GeocabTestCase):
    """Правила што ги чува самата база (CHECK, тригери, процедури)."""

    def assertDbError(self, text, func, *args, **kwargs):
        with self.assertRaisesMessage(DatabaseError, text), transaction.atomic():
            func(*args, **kwargs)

    def test_status_must_match_table(self):
        user = self.make_user('ana')
        user.status = status('ride', 'completed')
        self.assertDbError('не е од тип "user"', user.save)

    def test_check_constraints(self):
        self.assertDbError('chk_users_phone', Users.objects.create, name='x', username='x', phone='abc', email='x@y.mk',
                           password_hash='x', status=status('user', 'active'))
        self.assertDbError('chk_vehicle_year', VehicleOwnership.objects.create, plate='P', traffic_permit='T', color='c', year=1800,
                           vehicle_type=self.standard)
        self.assertDbError('chk_price', PricingRules.objects.create, base_price=-1, price_per_km=1, vehicle_type=self.standard)
        self.assertDbError('chk_commission', PlatformSettings.objects.filter(pk=1).update, commission_percentage=101)

    def test_illegal_status_transition(self):
        driver = self.make_driver('marko')
        self.start_shift(driver)
        ride_id = db.request_ride(self.make_user('ana').pk, self.location(), self.location(42.0045, 21.391), 'Standard')
        self.assertDbError('се очекува driver_arrived', db.call_procedure, 'sp_start_ride', ride_id, driver.pk)
        self.assertDbError('Недозволен премин', Rides.objects.filter(pk=ride_id).update, status=status('ride', 'completed'))

    def test_inactive_admin_cannot_create_driver(self):
        inactive = self.make_admin('stari', active=False)
        self.assertDbError('не постои или не е активен', db.admin_create_driver, inactive.pk, 'X', 'x', '070', 'x@y.mk', 'h', 'L')

    def test_only_one_platform_settings_row(self):
        self.assertDbError('chk_single_row', PlatformSettings.objects.create, setting_id=2, commission_percentage=10)

    def test_user_cannot_cancel_someone_elses_ride(self):
        driver = self.make_driver('marko')
        self.start_shift(driver)
        ride_id = db.request_ride(self.make_user('ana').pk, self.location(), self.location(42.0045, 21.391), 'Standard')
        self.assertDbError('не може да го откаже туѓото возење', db.cancel_ride, ride_id, 'USER', self.make_user('petar').pk, 'x')


class RoutingTest(TestCase):
    def test_osrm_failure_returns_none(self):
        routing._road_km.cache_clear()
        with mock.patch('GEOCAB_APP.routing.urlopen', side_effect=OSError('offline')), \
                self.assertLogs('GEOCAB_APP.routing', 'WARNING') as logs:
            self.assertIsNone(routing.road_distance_km(41.9, 21.4, 42.0, 21.5))
        self.assertIn('се користи воздушно растојание', logs.output[0])

    def test_osrm_distance_parsed(self):
        routing._road_km.cache_clear()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        with mock.patch('GEOCAB_APP.routing.urlopen', return_value=response), \
                mock.patch('GEOCAB_APP.routing.json.load', return_value={'code': 'Ok', 'routes': [{'distance': 3790.4}]}):
            self.assertEqual(routing.road_distance_km(41.8, 21.4, 42.1, 21.5), Decimal('3.79'))


class SeedAndTemplatesTest(TestCase):
    def test_seed_is_repeatable(self):
        call_command('seed', stdout=open('/dev/null', 'w'))
        call_command('seed', stdout=open('/dev/null', 'w'))
        self.assertEqual(Drivers.objects.filter(username__in=['vozac_marko', 'vozac_elena']).count(), 2)
        self.assertEqual(VehicleTypes.objects.filter(description='Standard').count(), 1)

    def test_static_files_are_versioned(self):
        html = Client().get('/').content.decode()
        self.assertRegex(html, r'/static/GEOCAB_APP/geocab\.css\?v=\d+')


class AllLinksTest(GeocabTestCase):
    """Ги отвора сите страници за секоја улога и го следи секој линк: без 404/500, без празни копчиња,
    секоја форма праќа на постоечка адреса, а POST формите имаат CSRF."""

    def crawl(self, client, starts):
        seen, queue = set(), deque(starts)
        while queue and len(seen) < 150:
            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            response = client.get(url)
            if response.status_code in (301, 302):
                continue
            self.assertEqual(response.status_code, 200, url)
            if 'text/html' not in response.get('Content-Type', ''):
                continue
            html = response.content.decode()
            for match in re.finditer(r'<a\b[^>]*href="([^"]*)"', html):
                href = match.group(1).replace('&amp;', '&')
                self.assertNotEqual(href, '#', f'празен линк на {url}: {match.group(0)[:80]}')
                if not href or href.startswith(('/admin/', '/static/', 'mailto:', 'tel:', 'http', '#', 'javascript:')):
                    continue
                queue.append((url.split('?')[0] + href if href.startswith('?') else href).split('#')[0])
            for match in re.finditer(r'<form\b([^>]*)>', html):
                action = re.search(r'action="([^"]*)"', match.group(1))
                target = action.group(1) if action else url.split('?')[0]
                try:
                    resolve(target.split('?')[0])
                except Resolver404:
                    self.fail(f'формата на {url} праќа на непостоечка адреса {target}')
                if 'method="post"' in match.group(1).lower():
                    body = html[match.end(): html.find('</form>', match.end())]
                    self.assertIn('csrfmiddlewaretoken', body, f'POST форма без CSRF на {url}')
        return seen

    def test_every_page_and_link_for_every_role(self):
        self.make_user('ana')
        user = self.login('user', 'ana')
        driver = self.make_driver('marko')
        self.start_shift(driver)
        driver_client = self.login('driver', 'marko')
        admin = self.login('admin', 'admin')
        done = self.full_ride(user, driver_client)
        user.post(f'/ride/{done.pk}/rate/', {'score': '5'})
        self.full_ride(user, driver_client, pay=False)
        self.request_ride(user)
        active = self.latest_ride()
        self.crawl(Client(), ['/', '/about/', '/login/', '/register/', '/password/forgot/'])
        self.crawl(user, ['/', '/ride/current/', '/rides/', '/notifications/', '/account/', '/account/password/'])
        self.crawl(driver_client, ['/driver/', '/driver/profile/'])
        self.drive(driver_client, active, ('accept',))
        user.post('/ride/report/', {'category': 'OTHER', 'message': 'тест'})
        self.crawl(driver_client, ['/driver/'])
        self.crawl(user, ['/ride/current/'])
        pages = self.crawl(admin, ['/manage/', '/manage/drivers/', '/manage/drivers/new/', f'/manage/drivers/{driver.pk}/', '/manage/rides/',
                                   f'/manage/rides/{done.pk}/', f'/manage/rides/{active.pk}/', '/manage/users/', '/manage/settings/',
                                   '/manage/reports/', '/manage/reports/?status=all'])
        self.assertGreater(len(pages), 10)
