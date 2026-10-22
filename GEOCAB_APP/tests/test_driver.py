"""Возач: смена, понуди (5 минути), чекори на возењето, откажување, no-show, GPS, мапа и навигација."""
import re
from decimal import Decimal

from GEOCAB_APP import db
from GEOCAB_APP.models import ActiveDrivers, Cancellations, Locations, RideRejections

from .base import SHIFT_LOCATION, GeocabTestCase, messages_of


class ShiftTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver('marko')
        self.client = self.login('driver', 'marko')

    def test_start_shift_form_and_validation(self):
        html = self.client.get('/driver/').content.decode()
        self.assertIn('Почни смена', html)
        self.assertIn(self.driver.vehicle.plate, html)
        self.assertIn('Користи моја локација', html)
        no_point = {k: v for k, v in SHIFT_LOCATION.items() if k not in ('loc_lat', 'loc_lng')}
        response = self.client.post('/driver/action/', {'action': 'start_shift', 'vehicle_ownership': self.driver.vehicle.pk, **no_point}, follow=True)
        self.assertIn('Изберете ја вашата локација на мапата и пополнете ја адресата.', messages_of(response))

    def test_start_and_end_shift(self):
        response = self.client.post('/driver/action/', {'action': 'start_shift', 'vehicle_ownership': self.driver.vehicle.pk,
                                                        **SHIFT_LOCATION}, follow=True)
        self.assertIn('Смената започна. Чекате понуди.', messages_of(response))
        self.assertIn('Слободни сте. Чекате понуди', response.content.decode())
        self.assertIn('gps-status', response.content.decode())
        self.assertEqual(self.client.get('/driver/status/').json(), {'collect': None, 'shift': True, 'offer': None, 'ride': None})
        response = self.client.post('/driver/action/', {'action': 'end_shift'}, follow=True)
        self.assertIn('Смената е завршена.', messages_of(response))
        self.assertFalse(ActiveDrivers.objects.filter(driver=self.driver, working_end__isnull=True).exists())
        response = self.client.post('/driver/action/', {'action': 'end_shift'}, follow=True)
        self.assertTrue(any('нема отворена смена' in m for m in messages_of(response)))

    def test_driver_without_vehicle(self):
        self.make_driver('bez', vehicle=False)
        html = self.login('driver', 'bez').get('/driver/').content.decode()
        self.assertIn('Немате доделено возило', html)

    def test_cannot_use_someone_elses_vehicle(self):
        other = self.make_driver('drug')
        response = self.client.post('/driver/action/', {'action': 'start_shift', 'vehicle_ownership': other.vehicle.pk, **SHIFT_LOCATION}, follow=True)
        self.assertFalse(ActiveDrivers.objects.filter(driver=self.driver).exists())
        self.assertTrue(any('Изберете ја вашата локација' in m for m in messages_of(response)))   # возилото не е во изборот

    def test_vehicle_already_driven_by_other_driver(self):
        from GEOCAB_APP.models import DriversVehicleOwnership
        other = self.make_driver('drug', vehicle=False)
        DriversVehicleOwnership.objects.create(driver=other, vehicle_ownership=self.driver.vehicle)
        self.start_shift(self.driver)
        other.vehicle = self.driver.vehicle
        response = self.login('driver', 'drug').post('/driver/action/', {'action': 'start_shift', 'vehicle_ownership': self.driver.vehicle.pk,
                                                                         **SHIFT_LOCATION}, follow=True)
        self.assertTrue(any('веќе го вози друг возач' in m for m in messages_of(response)))

    def test_update_location(self):
        self.start_shift(self.driver)
        response = self.client.post('/driver/action/', {'action': 'update_location', **SHIFT_LOCATION, 'loc_lat': '42.001000'}, follow=True)
        self.assertIn('Локацијата е ажурирана.', messages_of(response))
        self.assertEqual(ActiveDrivers.objects.get(driver=self.driver).current_location.latitude, Decimal('42.001000'))

    def test_unknown_action(self):
        self.assertIn('Непозната акција.', messages_of(self.client.post('/driver/action/', {'action': 'xyz'}, follow=True)))


class OfferTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.make_user('ana')
        self.user = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.client = self.login('driver', 'marko')
        self.request_ride(self.user)
        self.ride = self.latest_ride()

    def test_offer_card_with_price_distance_and_map(self):
        response = self.client.get('/driver/')
        html = response.content.decode()
        self.assertIn('Нова понуда', html)
        self.assertEqual(response.context['offer']['estimated_price'], Decimal('251.60'))
        self.assertGreater(response.context['seconds_left'], 290)   # 5 минути
        self.assertIn('id="route-map"', html)
        self.assertIn('data-target="pickup"', html)
        self.assertNotIn('id="nav-google"', html)   # навигацијата е дури по прифаќање
        self.assertEqual(self.client.get('/driver/status/').json()['offer'], self.ride.pk)

    def test_offer_still_valid_after_4_minutes_59(self):
        self.age_offer(self.ride, 299)
        self.user.get('/ride/current/status/')       # и корисничката страница проверува истекување
        self.client.get('/driver/status/')
        self.assertEqual(self.ride_status(self.ride), 'requested')
        self.assertEqual(self.client.get('/driver/status/').json()['offer'], self.ride.pk)

    def test_offer_expires_after_5_minutes(self):
        self.age_offer(self.ride, 301)
        self.user.get('/ride/current/status/')
        self.assertEqual(self.ride_status(self.ride), 'no_driver')
        self.assertEqual(list(RideRejections.objects.filter(ride=self.ride).values_list('reason', flat=True)), ['TIMEOUT'])

    def test_expired_offer_goes_to_next_driver(self):
        second = self.make_driver('vtor')
        self.start_shift(second, lat=41.999, lng=21.44)
        self.age_offer(self.ride, 301)
        self.client.get('/driver/status/')
        self.ride.refresh_from_db()
        self.assertEqual((self.ride.driver_id, self.ride_status(self.ride)), (second.pk, 'requested'))

    def test_reject_goes_to_next_driver_and_not_back(self):
        second = self.make_driver('vtor')
        self.start_shift(second, lat=41.999, lng=21.44)
        response = self.client.post('/driver/action/', {'action': 'reject', 'ride_id': self.ride.pk}, follow=True)
        self.assertIn('Понудата е одбиена.', messages_of(response))
        self.ride.refresh_from_db()
        self.assertEqual(self.ride.driver_id, second.pk)
        self.login('driver', 'vtor').post('/driver/action/', {'action': 'reject', 'ride_id': self.ride.pk})
        self.assertEqual(self.ride_status(self.ride), 'no_driver')   # првиот не ја добива пак

    def test_other_driver_cannot_accept(self):
        self.make_driver('drug')
        response = self.login('driver', 'drug').post('/driver/action/', {'action': 'accept', 'ride_id': self.ride.pk}, follow=True)
        self.assertTrue(any('не е доделено на возачот' in m for m in messages_of(response)))

    def test_busy_driver_gets_no_second_offer(self):
        self.client.post('/driver/action/', {'action': 'accept', 'ride_id': self.ride.pk})
        self.make_user('petar')
        self.request_ride(self.login('user', 'petar'))
        self.assertEqual(self.latest_ride().status.status_text, 'no_driver')


class RideStepsTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.make_user('ana')
        self.user = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.client = self.login('driver', 'marko')
        self.request_ride(self.user)
        self.ride = self.latest_ride()

    def test_full_ride_step_by_step(self):
        steps = [('accept', 'accepted', 'Пристигнав на локацијата'), ('arrived', 'driver_arrived', 'Почни возење'),
                 ('start', 'in_progress', 'Заврши возење')]
        for action, expected, next_button in steps:
            self.client.post('/driver/action/', {'action': action, 'ride_id': self.ride.pk})
            self.assertEqual(self.ride_status(self.ride), expected)
            response = self.client.get('/driver/')
            self.assertEqual(response.context['next_action'][1], next_button)
        response = self.client.post('/driver/action/', {'action': 'complete', 'ride_id': self.ride.pk}, follow=True)
        self.assertIn('Возењето е завршено. Цена: 251.60 ден.', messages_of(response))
        self.ride.refresh_from_db()
        self.assertEqual((self.ride_status(self.ride), self.ride.final_price), ('completed', Decimal('251.60')))
        ad = ActiveDrivers.objects.get(driver=self.driver)
        self.assertTrue(ad.is_available)
        self.assertEqual(ad.current_location_id, self.ride.dropoff_location_id)   # возачот е на дестинацијата
        self.assertEqual(self.payment(self.ride).status.status_text, 'pending')

    def test_wrong_order_is_rejected(self):
        for action in ('arrived', 'start', 'complete'):
            response = self.client.post('/driver/action/', {'action': action, 'ride_id': self.ride.pk}, follow=True)
            self.assertTrue(any('се очекува' in m for m in messages_of(response)), action)
        self.assertEqual(self.ride_status(self.ride), 'requested')

    def test_cannot_end_shift_during_ride(self):
        self.drive(self.client, self.ride, ('accept',))
        self.assertNotIn('value="end_shift"', self.client.get('/driver/').content.decode())
        response = self.client.post('/driver/action/', {'action': 'end_shift'}, follow=True)
        self.assertTrue(any('има активно возење' in m for m in messages_of(response)))

    def test_driver_cancel_sends_ride_to_another_driver(self):
        second = self.make_driver('vtor')
        self.start_shift(second, lat=41.999, lng=21.44)
        self.drive(self.client, self.ride, ('accept',))
        response = self.client.post('/driver/action/', {'action': 'cancel', 'ride_id': self.ride.pk}, follow=True)
        self.assertIn('Се откажавте. Возењето оди кај друг возач.', messages_of(response))
        self.ride.refresh_from_db()
        self.assertEqual((self.ride_status(self.ride), self.ride.driver_id), ('requested', second.pk))
        self.assertEqual(RideRejections.objects.get(ride=self.ride, driver=self.driver).reason, 'DRIVER_CANCELLED')
        self.assertFalse(Cancellations.objects.filter(ride=self.ride).exists())

    def test_no_show_only_after_arrival(self):
        self.drive(self.client, self.ride, ('accept',))
        self.assertNotIn('Корисникот не дојде', self.client.get('/driver/').content.decode())
        response = self.client.post('/driver/action/', {'action': 'no_show', 'ride_id': self.ride.pk}, follow=True)
        self.assertTrue(any('No-show може да пријави само возачот' in m for m in messages_of(response)))
        self.drive(self.client, self.ride, ('arrived',))
        self.assertIn('Корисникот не дојде', self.client.get('/driver/').content.decode())
        self.client.post('/driver/action/', {'action': 'no_show', 'ride_id': self.ride.pk})
        cancellation = Cancellations.objects.get(ride=self.ride)
        self.assertEqual((self.ride_status(self.ride), cancellation.cancellation_fee, cancellation.is_no_show), ('cancelled', Decimal('100.00'), True))

    def test_cannot_cancel_in_progress(self):
        self.drive(self.client, self.ride, ('accept', 'arrived', 'start'))
        self.assertNotIn('value="cancel"', self.client.get('/driver/').content.decode())
        response = self.client.post('/driver/action/', {'action': 'cancel', 'ride_id': self.ride.pk}, follow=True)
        self.assertTrue(any('може да го прекине само администратор' in m for m in messages_of(response)))

    def test_route_map_and_navigation_links(self):
        self.drive(self.client, self.ride, ('accept',))
        html = self.client.get('/driver/').content.decode()
        for element in ('id="route-map"', 'data-target="pickup"', 'id="nav-google"', 'id="nav-waze-pickup"', 'id="nav-waze-dropoff"', 'Упатства чекор по чекор'):
            self.assertIn(element, html)
        self.drive(self.client, self.ride, ('arrived', 'start'))
        html = self.client.get('/driver/').content.decode()
        self.assertIn('data-target="dropoff"', html)
        self.assertNotIn('id="nav-waze-pickup"', html)

    def test_report_button_only_during_ride(self):
        self.assertNotIn('Пријави проблем', self.client.get('/driver/').content.decode())
        self.drive(self.client, self.ride, ('accept',))
        html = self.client.get('/driver/').content.decode()
        self.assertIn('Пријави проблем', html)
        for number in ('192', '194', '193', '112'):
            self.assertIn(f'tel:{number}', html)


class GpsTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver('marko')
        self.client = self.login('driver', 'marko')

    def test_gps_requires_open_shift(self):
        response = self.client.post('/driver/position/', {'lat': '41.99', 'lng': '21.43'})
        self.assertEqual((response.status_code, response.json()['ok']), (409, False))

    def test_invalid_positions(self):
        self.start_shift(self.driver)
        self.assertEqual(self.client.post('/driver/position/', {'lat': 'abc', 'lng': '21.43'}).status_code, 400)
        self.assertEqual(self.client.post('/driver/position/', {'lng': '21.43'}).status_code, 400)
        self.assertEqual(self.client.post('/driver/position/', {'lat': '95', 'lng': '21.43'}).json()['error'], 'Невалидна GPS позиција')

    def test_small_moves_do_not_create_locations(self):
        self.start_shift(self.driver, lat=41.99, lng=21.42)
        before = self.locations_count()
        self.assertTrue(self.client.post('/driver/position/', {'lat': '41.990200', 'lng': '21.420100'}).json()['ok'])
        self.assertEqual(self.locations_count(), before)
        self.assertIsNotNone(ActiveDrivers.objects.get(driver=self.driver).position_updated_at)

    def test_moves_over_50m_update_location(self):
        self.start_shift(self.driver, lat=41.99, lng=21.42)
        self.client.post('/driver/position/', {'lat': '41.993000', 'lng': '21.425000'})
        location = ActiveDrivers.objects.get(driver=self.driver).current_location
        self.assertEqual((location.latitude, location.ulica), (Decimal('41.993000'), 'GPS позиција'))
        self.assertIsNotNone(Locations.objects.get(pk=location.pk).geom)

    def test_nearest_driver_uses_gps_position(self):
        self.make_user('ana')
        far_start = self.make_driver('drug')
        self.start_shift(self.driver, lat=42.03, lng=21.47)       # смената почнала далеку
        self.start_shift(far_start, lat=42.00, lng=21.44)
        self.client.post('/driver/position/', {'lat': '41.996100', 'lng': '21.431100'})   # GPS: веднаш до корисникот
        self.request_ride(self.login('user', 'ana'))
        self.assertEqual(self.latest_ride().driver_id, self.driver.pk)

    def test_users_cannot_send_gps(self):
        self.make_user('ana')
        self.assertEqual(self.login('user', 'ana').post('/driver/position/', {'lat': '41.99', 'lng': '21.43'}).status_code, 302)


class DriverProfileTest(GeocabTestCase):
    def test_profile_shows_earnings_vehicles_and_debt(self):
        driver = self.make_driver('marko')
        self.make_user('ana')
        self.start_shift(driver)
        client = self.login('driver', 'marko')
        self.full_ride(self.login('user', 'ana'), client)
        response = client.get('/driver/profile/')
        summary = response.context['summary']
        self.assertEqual((summary['total_collected'], summary['driver_earnings'], summary['platform_commission']),
                         (Decimal('251.60'), Decimal('201.28'), Decimal('50.32')))
        html = response.content.decode()
        self.assertIn('Вкупно заработено (ваши 80%)', html)
        self.assertIn(driver.vehicle.plate, html)
        self.assertIn('Должите: 50.32 ден.', html)
        self.assertEqual(len(response.context['daily']), 1)
        self.assertEqual(len(response.context['payments']), 1)

    def test_login_redirect_and_menu(self):
        self.make_driver('marko')
        client = self.login('driver', 'marko')
        html = client.get('/driver/').content.decode()
        self.assertIn('Моја смена', html)
        self.assertIn('Мој профил', html)
        self.assertTrue(re.search(r'href="/driver/profile/"', html))
