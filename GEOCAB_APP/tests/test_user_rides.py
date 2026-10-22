"""Корисник: проценка, барање возење, следење, откажување, оценка, историја и известувања."""
from decimal import Decimal

from GEOCAB_APP.models import Cancellations, Notifications, Ratings, Rides
from GEOCAB_APP.views import MESSAGE_LABELS

from .base import RIDE, ROAD_KM, GeocabTestCase, field_errors, messages_of


class EstimateTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.make_user('ana')
        self.client = self.login('user', 'ana')

    def test_estimate_by_road_for_all_classes(self):
        before = self.locations_count()
        response = self.client.post('/ride/new/', {'action': 'estimate', **RIDE})
        estimates = {e['vehicle_class']: e for e in response.context['estimates']}
        self.assertEqual(set(estimates), {'Standard', 'Premium'})
        self.assertEqual(estimates['Standard']['distance_km'], ROAD_KM)
        self.assertEqual(estimates['Standard']['estimated_price'], Decimal('251.60'))   # 100 + 3.79 * 40
        self.assertEqual(estimates['Premium']['estimated_price'], Decimal('377.40'))    # 150 + 3.79 * 60
        self.assertTrue(response.context['by_road'])
        self.assertIn('km по пат', response.content.decode())
        self.assertEqual(self.locations_count(), before)   # проценката не запишува локации

    def test_estimate_falls_back_to_straight_line(self):
        self.osrm.return_value = None
        response = self.client.post('/ride/new/', {'action': 'estimate', **RIDE})
        self.assertFalse(response.context['by_road'])
        self.assertEqual(response.context['estimates'][0]['distance_km'], Decimal('3.45'))
        self.assertIn('(воздушно)', response.content.decode())

    def test_discount_over_10_km(self):
        self.osrm.return_value = Decimal('12.00')
        response = self.client.post('/ride/new/', {'action': 'estimate', **RIDE})
        standard = [e for e in response.context['estimates'] if e['vehicle_class'] == 'Standard'][0]
        self.assertEqual(standard['discount_percentage'], 20)
        self.assertEqual(standard['estimated_price'], Decimal('464.00'))   # (100 + 12*40) * 0.8

    def test_estimate_validation(self):
        no_points = {k: v for k, v in RIDE.items() if 'lat' not in k and 'lng' not in k}
        self.assertIn('Изберете ги двете точки на мапата.', self.client.post('/ride/new/', {'action': 'estimate', **no_points}).content.decode())
        same = {**RIDE, 'dropoff_lat': RIDE['pickup_lat'], 'dropoff_lng': RIDE['pickup_lng']}
        self.assertIn('Почетната и крајната локација не смеат да бидат исти.', self.client.post('/ride/new/', {'action': 'estimate', **same}).content.decode())
        bad = {**RIDE, 'pickup_lat': '999'}
        self.assertIsNone(self.client.post('/ride/new/', {'action': 'estimate', **bad}).context['estimates'])

    def test_no_prices_message(self):
        from GEOCAB_APP.models import PricingRules
        PricingRules.objects.all().delete()
        response = self.client.post('/ride/new/', {'action': 'estimate', **RIDE})
        self.assertIn('Сè уште нема внесени цени за возилата.', messages_of(response))

    def test_empty_house_number_becomes_bb(self):
        self.start_shift(self.make_driver('marko'))
        self.request_ride(self.client, dropoff_broj='')
        self.assertEqual(self.latest_ride().dropoff_location.broj, 'бб')


class RequestRideTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('ana')
        self.client = self.login('user', 'ana')
        self.driver = self.make_driver('marko')

    def test_no_driver_on_shift(self):
        response = self.request_ride(self.client)
        self.assertIn('Моментално нема слободен возач во близина. Обидете се повторно за малку.', messages_of(response))
        self.assertEqual(self.latest_ride().status.status_text, 'no_driver')
        self.assertIsNotNone(response.context['estimates'])   # страницата пак ги покажува цените

    def test_request_goes_to_nearest_driver_with_road_distance(self):
        far = self.make_driver('daleku')
        self.start_shift(far, lat=42.02, lng=21.45)
        self.start_shift(self.driver)
        response = self.request_ride(self.client)
        self.assertEqual(response.url, '/ride/current/')
        ride = self.latest_ride()
        self.assertEqual((ride.status.status_text, ride.driver_id, ride.distance_km), ('requested', self.driver.pk, ROAD_KM))
        self.assertIsNotNone(ride.offered_at)

    def test_driver_outside_10_km_is_not_offered(self):
        self.start_shift(self.driver, lat=42.20, lng=21.80)
        self.request_ride(self.client)
        self.assertEqual(self.latest_ride().status.status_text, 'no_driver')

    def test_vehicle_class_must_match(self):
        self.start_shift(self.driver)   # Standard
        self.request_ride(self.client, vehicle_class='Premium')
        self.assertEqual(self.latest_ride().status.status_text, 'no_driver')
        self.request_ride(self.client, vehicle_class='')   # било која
        self.assertEqual(self.latest_ride().driver_id, self.driver.pk)

    def test_unknown_class_is_rejected_by_database(self):
        self.start_shift(self.driver)
        response = self.request_ride(self.client, vehicle_class='Limuzina')
        self.assertIn('Непозната класа на возило: Limuzina', messages_of(response))

    def test_cannot_request_second_active_ride(self):
        self.start_shift(self.driver)
        self.request_ride(self.client)
        self.assertEqual(self.client.get('/ride/new/').url, '/ride/current/')
        self.assertEqual(Rides.objects.filter(user=self.user).count(), 1)

    def test_current_ride_page_states(self):
        self.start_shift(self.driver)
        self.request_ride(self.client)
        ride = self.latest_ride()
        response = self.client.get('/ride/current/')
        self.assertIn(f'Чекаме возачот {self.driver.name} да ја прифати понудата', response.context['status_label'])
        self.assertNotIn(self.driver.vehicle.plate, response.content.decode())   # таблицата пред прифаќање е скриена
        driver = self.login('driver', 'marko')
        expected = {'accept': 'доаѓа кон вас', 'arrived': 'пристигна на локацијата', 'start': 'во тек'}
        for step, text in expected.items():
            self.drive(driver, ride, (step,))
            response = self.client.get('/ride/current/')
            self.assertIn(text, response.context['status_label'])
        self.assertIn(self.driver.vehicle.plate, response.content.decode())

    def test_status_json(self):
        self.assertEqual(self.client.get('/ride/current/status/').json(), {'status': None, 'payment': None})
        self.start_shift(self.driver)
        self.request_ride(self.client)
        self.assertEqual(self.client.get('/ride/current/status/').json(), {'status': 'requested', 'driver_id': self.driver.pk})

    def test_track_json_and_gps_flag(self):
        self.start_shift(self.driver)
        self.request_ride(self.client)
        ride = self.latest_ride()
        self.assertIsNone(self.client.get('/ride/current/track/').json()['driver'])   # пред прифаќање
        driver = self.login('driver', 'marko')
        self.drive(driver, ride, ('accept',))
        data = self.client.get('/ride/current/track/').json()
        self.assertEqual((data['status'], round(data['driver']['lat'], 4), data['driver']['gps']), ('accepted', 41.9965, False))
        driver.post('/driver/position/', {'lat': '41.999000', 'lng': '21.435000'})
        data = self.client.get('/ride/current/track/').json()
        self.assertEqual((round(data['driver']['lat'], 3), data['driver']['gps']), (41.999, True))
        self.assertIn('id="live-map"', self.client.get('/ride/current/').content.decode())


class CancelTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('ana')
        self.client = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.driver_client = self.login('driver', 'marko')

    def _cancel_after(self, steps):
        self.request_ride(self.client)
        ride = self.drive(self.driver_client, self.latest_ride(), steps)
        response = self.client.post('/ride/cancel/', follow=True)
        return ride, response

    def test_cancel_while_searching_is_free(self):
        ride, response = self._cancel_after(())
        self.assertIn('Возењето е откажано.', messages_of(response))
        self.assertEqual(Cancellations.objects.get(ride=ride).cancellation_fee, 0)

    def test_cancel_after_accept_costs_half_base(self):
        self.request_ride(self.client)
        self.drive(self.driver_client, self.latest_ride(), ('accept',))
        self.assertEqual(self.client.get('/ride/current/').context['cancellation_fee'], Decimal('50.00'))
        self.client.post('/ride/cancel/')
        ride = self.latest_ride()
        self.assertEqual((ride.status.status_text, Cancellations.objects.get(ride=ride).cancellation_fee), ('cancelled', Decimal('50.00')))
        self.assertEqual(self.payment(ride).status.status_text, 'pending')
        self.assertTrue(self.client.get('/ride/current/').context['unpaid_fees'])

    def test_cancel_after_driver_arrived_costs_full_base(self):
        ride, _ = self._cancel_after(('accept', 'arrived'))
        self.assertEqual(Cancellations.objects.get(ride=ride).cancellation_fee, Decimal('100.00'))

    def test_cannot_cancel_in_progress(self):
        self.request_ride(self.client)
        self.drive(self.driver_client, self.latest_ride(), ('accept', 'arrived', 'start'))
        self.assertNotIn('Откажи возење', self.client.get('/ride/current/').content.decode())
        response = self.client.post('/ride/cancel/', follow=True)
        self.assertTrue(any('може да го прекине само администратор' in m for m in messages_of(response)))
        self.assertEqual(self.ride_status(self.latest_ride()), 'in_progress')

    def test_cancel_without_active_ride(self):
        response = self.client.post('/ride/cancel/', follow=True)
        self.assertIn('Немате активно возење.', messages_of(response))

    def test_driver_freed_after_user_cancel(self):
        from GEOCAB_APP.models import ActiveDrivers
        self._cancel_after(('accept',))
        self.assertTrue(ActiveDrivers.objects.get(driver=self.driver).is_available)


class RatingAndHistoryTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('ana')
        self.client = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.driver_client = self.login('driver', 'marko')

    def test_completed_ride_page_payment_and_rating(self):
        ride = self.full_ride(self.client, self.driver_client, pay=False)
        response = self.client.get('/ride/current/')
        self.assertEqual(response.context['last_payment'].status.status_text, 'pending')
        self.assertIn('Платете во готовина на возачот', response.content.decode())
        self.assertTrue(response.context['can_rate'])
        self.driver_client.post('/driver/action/', {'action': 'cash_received', 'ride_id': str(ride.pk)})
        self.assertIn('Платено во готовина', self.client.get('/ride/current/').content.decode())

    def test_rating_rules(self):
        ride = self.full_ride(self.client, self.driver_client)
        self.assertIn('Изберете оценка од 1 до 5 ѕвезди.', messages_of(self.client.post(f'/ride/{ride.pk}/rate/', {'score': '6'}, follow=True)))
        self.make_user('petar')
        other_client = self.login('user', 'petar')
        self.assertIn('Возењето не постои.', messages_of(other_client.post(f'/ride/{ride.pk}/rate/', {'score': '1'}, follow=True)))
        response = self.client.post(f'/ride/{ride.pk}/rate/', {'score': '3', 'comment': 'Добро'}, follow=True)
        self.assertIn('Ви благодариме за оценката!', messages_of(response))
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.rating, Decimal('3.00'))   # тригерот го пресметува просекот
        self.assertIn('Ова возење веќе е оценето.', messages_of(self.client.post(f'/ride/{ride.pk}/rate/', {'score': '5'}, follow=True)))
        self.assertEqual(Ratings.objects.filter(ride=ride).count(), 1)

    def test_cannot_rate_unfinished_ride(self):
        self.request_ride(self.client)
        ride = self.drive(self.driver_client, self.latest_ride(), ('accept',))
        response = self.client.post(f'/ride/{ride.pk}/rate/', {'score': '5'}, follow=True)
        self.assertTrue(any('не е завршено' in m for m in messages_of(response)))

    def test_rating_redirects_back_safely(self):
        ride = self.full_ride(self.client, self.driver_client)
        self.assertEqual(self.client.post(f'/ride/{ride.pk}/rate/', {'score': '4', 'next': '/rides/'}).url, '/rides/')
        ride2 = self.full_ride(self.client, self.driver_client)
        self.assertEqual(self.client.post(f'/ride/{ride2.pk}/rate/', {'score': '4', 'next': 'https://evil.com/'}).url, '/ride/current/')

    def test_history_totals_and_statuses(self):
        self.full_ride(self.client, self.driver_client)                          # платено 251.60
        self.request_ride(self.client); self.drive(self.driver_client, self.latest_ride(), ('accept',)); self.client.post('/ride/cancel/')
        self.full_ride(self.client, self.driver_client, pay=False)               # чека плаќање
        response = self.client.get('/rides/')
        rows = list(response.context['page'])
        self.assertEqual([r['label'] for r in rows], ['Завршено', 'Откажано', 'Завршено'])
        self.assertEqual(response.context['total_rides'], 2)
        self.assertEqual(response.context['total_spent'], Decimal('251.60'))
        html = response.content.decode()
        for text in ('Откажано од вас', 'казна 50.00', 'чека плаќање', '✓ платено'):
            self.assertIn(text, html)

    def test_history_pagination(self):
        for _ in range(12):
            self.request_ride(self.client, vehicle_class='Premium')   # нема Premium возач -> no_driver
        self.assertEqual(self.client.get('/rides/').context['page'].paginator.num_pages, 2)
        self.assertEqual(len(self.client.get('/rides/?page=2').context['page']), 2)

    def test_history_empty(self):
        self.assertIn('Сè уште немате возења.', self.client.get('/rides/').content.decode())


class NotificationsTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('ana')
        self.client = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)

    def test_all_database_messages_have_cyrillic_labels(self):
        from GEOCAB_APP.models import Messages
        self.assertLessEqual(set(Messages.objects.values_list('message_text', flat=True)), set(MESSAGE_LABELS))

    def test_poll_shows_only_new_once(self):
        self.client.get('/')   # почеток: старите не скокаат
        self.assertEqual(self.client.get('/notifications/poll/').json()['notifications'], [])
        self.request_ride(self.client)
        data = self.client.get('/notifications/poll/').json()
        self.assertEqual([n['text'] for n in data['notifications']], ['Вашето барање е успешно испратено'])
        self.assertEqual(data['unread'], 1)
        self.assertEqual(self.client.get('/notifications/poll/').json()['notifications'], [])

    def test_notifications_page_marks_seen(self):
        self.request_ride(self.client)
        self.login('driver', 'marko').post('/driver/action/', {'action': 'accept', 'ride_id': self.latest_ride().pk})
        self.assertEqual(self.client.get('/').context['new_notifications'], 2)
        html = self.client.get('/notifications/').content.decode()
        self.assertIn('Возачот го прифати вашето барање', html)
        self.assertNotIn('Vozacot', html)
        self.assertEqual(self.client.get('/').context['new_notifications'], 0)

    def test_toasts_only_for_users(self):
        self.assertIn('id="toasts"', self.client.get('/').content.decode())
        self.assertNotIn('id="toasts"', self.login('driver', 'marko').get('/driver/').content.decode())

    def test_rejection_notifies_user_but_timeout_does_not(self):
        self.request_ride(self.client)
        ride = self.latest_ride()
        self.login('driver', 'marko').post('/driver/action/', {'action': 'reject', 'ride_id': ride.pk})
        texts = list(Notifications.objects.filter(ride=ride).values_list('message__message_text', flat=True))
        self.assertIn('Vozacot go odbi vaseto baranje', texts)
