"""Плаќање во готовина, провизија 80/20, казни, уплати на провизија, месечен долг и блокада, пријави."""
from datetime import date
from decimal import Decimal

from GEOCAB_APP import db
from GEOCAB_APP.models import ActiveDrivers, CommissionPayouts, Notifications, PlatformSettings, RideReports

from .base import GeocabTestCase, messages_of


class CashPaymentTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.make_user('ana')
        self.user = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.client = self.login('driver', 'marko')

    def test_driver_collects_cash(self):
        ride = self.full_ride(self.user, self.client, pay=False)
        response = self.client.get('/driver/')
        self.assertIn('Наплатете во готовина', response.content.decode())
        self.assertEqual(response.context['collect']['total'], Decimal('251.60'))
        response = self.client.post('/driver/action/', {'action': 'cash_received', 'ride_id': str(ride.pk)}, follow=True)
        self.assertIn('Наплатено 251.60 ден. во готовина.', messages_of(response))
        payment = self.payment(ride)
        self.assertEqual((payment.status.status_text, payment.payment_method.payment_tip), ('completed', 'CASH'))
        self.assertIsNone(self.client.get('/driver/').context['collect'])
        self.assertTrue(Notifications.objects.filter(ride=ride, message__message_text='Plakjanjeto e uspesno').exists())

    def test_cash_for_wrong_ride_rejected(self):
        self.full_ride(self.user, self.client, pay=False)
        response = self.client.post('/driver/action/', {'action': 'cash_received', 'ride_id': '999999'}, follow=True)
        self.assertIn('Нема плаќање што чека за ова возење.', messages_of(response))

    def test_cancellation_fee_collected_with_next_ride(self):
        self.request_ride(self.user)
        fee_ride = self.drive(self.client, self.latest_ride(), ('accept',))
        self.user.post('/ride/cancel/')
        self.assertIsNone(self.client.get('/driver/').context['collect'])   # казната не се наплаќа веднаш
        self.assertIn('неплатена казна', self.user.get('/ride/current/').content.decode())
        ride = self.full_ride(self.user, self.client, pay=False)
        collect = self.client.get('/driver/').context['collect']
        self.assertEqual((collect['ride_amount'], collect['fees_amount'], collect['total']), (Decimal('251.60'), Decimal('50.00'), Decimal('301.60')))
        self.client.post('/driver/action/', {'action': 'cash_received', 'ride_id': str(ride.pk)})
        self.assertEqual(self.payment(fee_ride).status.status_text, 'completed')
        self.assertEqual(self.payment(ride).status.status_text, 'completed')
        self.assertFalse(self.user.get('/ride/current/').context['unpaid_fees'])


class CommissionTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.make_user('ana')
        self.user = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.client = self.login('driver', 'marko')

    def test_80_20_split_is_computed_by_database(self):
        payment = self.payment(self.full_ride(self.user, self.client))
        self.assertEqual((payment.commission_percentage, payment.platform_amount, payment.driver_amount),
                         (Decimal('20.00'), Decimal('50.32'), Decimal('201.28')))

    def test_new_commission_only_for_new_payments(self):
        first = self.payment(self.full_ride(self.user, self.client))
        PlatformSettings.objects.filter(pk=1).update(commission_percentage=15)
        second = self.payment(self.full_ride(self.user, self.client))
        first.refresh_from_db()
        self.assertEqual((first.commission_percentage, second.commission_percentage), (Decimal('20.00'), Decimal('15.00')))
        self.assertEqual(second.driver_amount, Decimal('213.86'))

    def test_cancellation_fee_belongs_to_cancelled_driver(self):
        self.request_ride(self.user)
        fee_ride = self.drive(self.client, self.latest_ride(), ('accept',))
        self.user.post('/ride/cancel/')
        other = self.make_driver('drug')
        self.client.post('/driver/action/', {'action': 'end_shift'})
        self.start_shift(other)
        ride = self.full_ride(self.user, self.login('driver', 'drug'))
        summary = db.fetch_view('vw_driver_earnings_summary', 'driver_id = %s', [self.driver.pk])[0]
        self.assertEqual((summary['cancellation_earnings'], summary['driver_earnings']), (Decimal('40.00'), Decimal('40.00')))
        self.assertEqual(self.payment(fee_ride).status.status_text, 'completed')   # ја наплатил другиот возач
        self.assertEqual(ride.driver_id, other.pk)

    def test_refund_removes_earnings(self):
        ride = self.full_ride(self.user, self.client)
        db.refund_payment(self.payment(ride).pk, self.admin.pk)
        summary = db.fetch_view('vw_driver_earnings_summary', 'driver_id = %s', [self.driver.pk])[0]
        self.assertEqual((summary['driver_earnings'], summary['platform_commission']), (0, 0))
        self.assertIn('вратено', self.user.get('/rides/').content.decode())


class PayoutAndDebtTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.make_user('ana')
        self.user = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.client = self.login('driver', 'marko')
        self.admin_client = self.login('admin', 'admin')

    def ride_paid_on(self, when):
        ride = self.full_ride(self.user, self.client)
        self.set_payment_time(ride, when)
        return self.payment(ride).platform_amount

    def debt(self, today):
        return db.call_table_function('fn_driver_debt_status', self.driver.pk, today)[0]

    def test_payout_cannot_exceed_debt(self):
        self.ride_paid_on('2026-10-05')
        url = f'/manage/drivers/{self.driver.pk}/action/'
        self.assertTrue(any('е поголем од долгот' in m for m in messages_of(self.admin_client.post(url, {'action': 'payout', 'amount': '1000'}, follow=True))))
        self.assertIn('Внесете валиден износ.', messages_of(self.admin_client.post(url, {'action': 'payout', 'amount': '-5'}, follow=True)))
        self.admin_client.post(url, {'action': 'payout', 'amount': '30', 'note': 'готовина'})
        payout = CommissionPayouts.objects.get(driver=self.driver)
        self.assertEqual((payout.amount, payout.admin_id, payout.note), (Decimal('30.00'), self.admin.pk, 'готовина'))
        balance = db.fetch_view('vw_driver_commission_balance', 'driver_id = %s', [self.driver.pk])[0]['balance']
        self.assertEqual(balance, Decimal('20.32'))
        self.assertIn('Должите: 20.32 ден.', self.client.get('/driver/profile/').content.decode())

    def test_inactive_admin_cannot_record_payout(self):
        self.ride_paid_on('2026-10-05')
        inactive = self.make_admin('stari', active=False)
        with self.assertRaisesMessage(Exception, 'не постои или не е активен'):
            db.record_commission_payout(self.driver.pk, inactive.pk, 10)

    def test_monthly_deadlines(self):
        commission = self.ride_paid_on('2026-10-10')
        expected = {
            date(2026, 10, 25): (0, 0, 0, False),             # тековен месец - уште не се плаќа
            date(2026, 11, 5): (commission, 0, 0, False),      # во рок (1-14)
            date(2026, 11, 14): (commission, 0, 0, False),
            date(2026, 11, 15): (0, commission, 0, False),     # опомена од 15-ти
            date(2026, 11, 30): (0, commission, 0, False),
            date(2026, 12, 1): (0, 0, commission, True),       # блокада од 1-ви во М+2
            date(2027, 2, 1): (0, 0, commission, True),
        }
        for day, (due, warning, overdue, blocked) in expected.items():
            d = self.debt(day)
            self.assertEqual((d['due_amount'], d['warning_amount'], d['overdue_amount'], d['is_blocked']), (due, warning, overdue, blocked), day)

    def test_partial_payment_keeps_block_full_payment_lifts_it(self):
        commission = self.ride_paid_on('2026-10-10')
        db.record_commission_payout(self.driver.pk, self.admin.pk, 10)
        self.assertEqual(self.debt(date(2026, 12, 1))['overdue_amount'], commission - 10)
        db.record_commission_payout(self.driver.pk, self.admin.pk, commission - 10)
        self.assertFalse(self.debt(date(2026, 12, 1))['is_blocked'])

    def test_payouts_cover_oldest_month_first(self):
        october = self.ride_paid_on('2026-10-10')
        november = self.ride_paid_on('2026-11-10')
        db.record_commission_payout(self.driver.pk, self.admin.pk, october)
        d = self.debt(date(2026, 12, 20))
        self.assertEqual((d['overdue_amount'], d['warning_amount'], d['warning_month']), (0, november, date(2026, 11, 1)))

    def test_overdue_driver_is_blocked_today(self):
        self.ride_paid_on('2026-01-10')   # многу стар долг -> денес блокиран
        self.client.post('/driver/action/', {'action': 'end_shift'})
        response = self.client.post('/driver/action/', {'action': 'start_shift', 'vehicle_ownership': self.driver.vehicle.pk,
                                                        'loc_lat': '41.99', 'loc_lng': '21.43', 'loc_grad': 'a', 'loc_naselba': 'b', 'loc_ulica': 'c'}, follow=True)
        self.assertTrue(any('Блокирани сте поради неплатена провизија' in m for m in messages_of(response)))
        html = self.client.get('/driver/').content.decode()
        self.assertIn('Блокирани сте поради неплатен долг', html)
        self.assertNotIn('value="start_shift"', html)

    def test_overdue_driver_on_shift_gets_no_offers(self):
        self.ride_paid_on('2026-01-10')
        self.request_ride(self.user)
        self.assertEqual(self.latest_ride().status.status_text, 'no_driver')
        self.assertTrue(ActiveDrivers.objects.filter(driver=self.driver, working_end__isnull=True).exists())

    def test_admin_sees_blocked_and_payment_unblocks(self):
        commission = self.ride_paid_on('2026-01-10')
        rows = {r['driver'].pk: r['debt'] for r in self.admin_client.get('/manage/drivers/?debt=1').context['rows']}
        self.assertEqual(rows[self.driver.pk], 'blocked')
        self.assertEqual(self.admin_client.get('/manage/').context['blocked_for_debt'], 1)
        self.admin_client.post(f'/manage/drivers/{self.driver.pk}/action/', {'action': 'payout', 'amount': str(commission)})
        self.assertIsNone(db.driver_debt_status(self.driver.pk)['level'])

    def test_login_messages_for_warning_and_block(self):
        self.ride_paid_on('2026-01-10')
        from django.test import Client
        from .base import PASSWORD
        response = Client().post('/login/', {'role': 'driver', 'username': 'marko', 'password': PASSWORD}, follow=True)
        self.assertTrue(any('Блокирани сте' in m for m in messages_of(response)))


class ReportTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.ana = self.make_user('ana')
        self.user = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.client = self.login('driver', 'marko')
        self.request_ride(self.user)
        self.ride = self.latest_ride()

    def test_only_during_active_ride(self):
        self.assertNotIn('Пријави проблем', self.user.get('/ride/current/').content.decode())
        response = self.user.post('/ride/report/', {'category': 'SAFETY', 'message': 'x'}, follow=True)
        self.assertIn('Проблем може да се пријави само додека возењето е во тек.', messages_of(response))
        self.drive(self.client, self.ride)
        response = self.user.post('/ride/report/', {'category': 'SAFETY', 'message': 'x'}, follow=True)
        self.assertIn('Проблем може да се пријави само додека возењето е во тек.', messages_of(response))

    def test_user_and_driver_report_with_identity(self):
        self.drive(self.client, self.ride, ('accept',))
        html = self.user.get('/ride/current/').content.decode()
        self.assertIn('Пријави проблем', html)
        self.assertIn('tel:192', html)
        self.assertIn('Изберете тип на проблем.', messages_of(self.user.post('/ride/report/', {'category': 'X', 'message': 'a'}, follow=True)))
        self.assertTrue(any('Опишете го проблемот' in m for m in messages_of(self.user.post('/ride/report/', {'category': 'OTHER', 'message': '  '}, follow=True))))
        self.user.post('/ride/report/', {'category': 'SAFETY', 'message': 'Вози пребрзо'})
        self.client.post('/ride/report/', {'category': 'BEHAVIOR', 'message': 'Патникот вика'})
        reports = list(RideReports.objects.order_by('report_id').values_list('reported_by_type', 'reported_by_id', 'category', 'status__status_text'))
        self.assertEqual(reports, [('USER', self.ana.pk, 'SAFETY', 'open'), ('DRIVER', self.driver.pk, 'BEHAVIOR', 'open')])
        self.assertIn('се разгледува', self.user.get('/ride/current/').content.decode())

    def test_admin_resolves_and_reporter_sees_answer(self):
        self.drive(self.client, self.ride, ('accept',))
        self.client.post('/ride/report/', {'category': 'VEHICLE', 'message': 'Гума'})
        self.user.post('/ride/report/', {'category': 'SAFETY', 'message': 'Опасно'})
        admin = self.login('admin', 'admin')
        response = admin.get('/manage/reports/')
        self.assertEqual([r['category'] for r in response.context['reports']], ['SAFETY', 'VEHICLE'])   # безбедност прва
        self.assertEqual(response.context['open_reports_count'], 2)
        self.assertIn('безбедност!', admin.get('/manage/').content.decode())
        report = RideReports.objects.get(category='SAFETY')
        admin.post(f'/manage/reports/{report.pk}/resolve/', {'note': 'Разговаравме со возачот'})
        self.assertEqual(RideReports.objects.get(pk=report.pk).status.status_text, 'resolved')
        self.assertIn('Разговаравме со возачот', self.user.get('/ride/current/').content.decode())
        response = admin.post(f'/manage/reports/{report.pk}/resolve/', {}, follow=True)
        self.assertTrue(any('е веќе решена' in m for m in messages_of(response)))
        self.assertEqual(len(admin.get('/manage/reports/').context['reports']), 1)
        self.assertEqual(len(admin.get('/manage/reports/?status=all').context['reports']), 2)

    def test_cannot_report_someone_elses_ride(self):
        self.drive(self.client, self.ride, ('accept',))
        with self.assertRaisesMessage(Exception, 'само за ваше возење'):
            db.report_problem(self.ride.pk, 'USER', self.make_user('petar').pk, 'OTHER', 'x')
