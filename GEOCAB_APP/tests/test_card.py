"""Плаќање со картичка: избор при барање, демо платежна порта, казни, пребивање возач - GEOCAB, исплати."""
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import SimpleTestCase

from GEOCAB_APP import card_gateway, db
from GEOCAB_APP.forms import CardPaymentForm
from GEOCAB_APP.models import CardTransactions, DriverPayouts, Notifications

from .base import RIDE, GeocabTestCase, field_errors, messages_of

CARD = dict(card_name='ANA PETROVA', card_number='4242 4242 4242 4242', card_expiry='12/30', card_cvv='123')
DECLINED = dict(CARD, card_number='4000 0000 0000 0002')
RIDE_PRICE, COMMISSION, DRIVER_SHARE = Decimal('251.60'), Decimal('50.32'), Decimal('201.28')


class CardGatewayTest(SimpleTestCase):
    def test_luhn_and_brand(self):
        self.assertTrue(card_gateway.luhn_ok('4242424242424242'))
        self.assertFalse(card_gateway.luhn_ok('4242424242424241'))
        self.assertEqual([card_gateway.card_brand(n) for n in ('4242424242424242', '5555555555554444', '378282246310005')],
                         ['VISA', 'MASTERCARD', 'AMEX'])

    def test_charge_approved_and_declined(self):
        ok = card_gateway.charge('4242424242424242', Decimal('100.00'))
        self.assertEqual((ok.approved, ok.brand, ok.last4), (True, 'VISA', '4242'))
        declined = card_gateway.charge('4000000000000002', Decimal('100.00'))
        self.assertEqual((declined.approved, declined.message), (False, 'Картичката е одбиена од банката.'))

    def test_form_validation(self):
        self.assertTrue(CardPaymentForm(CARD).is_valid())
        errors = CardPaymentForm(dict(CARD, card_number='1234 5678 9012 3456', card_expiry='01/20', card_cvv='12')).errors
        self.assertEqual(errors['card_number'], ['Бројот на картичката не е исправен.'])
        self.assertEqual(errors['card_expiry'], ['Картичката е истечена.'])
        self.assertEqual(errors['card_cvv'], ['CVV е 3 или 4 цифри од задната страна.'])
        amex = CardPaymentForm(dict(CARD, card_number='3782 822463 10005', card_cvv='123'))
        self.assertEqual(amex.errors['card_cvv'], ['CVV не одговара на типот на картичката.'])
        self.assertEqual(CardPaymentForm(dict(CARD, card_expiry='13/30')).errors['card_expiry'],
                         ['Внесете месец и година, на пр. 12/28.'])


class CardPaymentTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.make_user('ana')
        self.user = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.client = self.login('driver', 'marko')

    def card_ride(self):
        self.request_ride(self.user, payment_method='CARD')
        return self.drive(self.client, self.latest_ride())

    def test_choice_is_saved_and_shown_to_driver(self):
        self.request_ride(self.user, payment_method='CARD')
        ride = self.latest_ride()
        self.assertEqual(ride.payment_method.payment_tip, 'CARD')
        self.assertIn('💳 Картичка', self.client.get('/driver/').content.decode())   # во понудата
        self.drive(self.client, ride, ('accept',))
        self.assertIn('💳 Картичка', self.client.get('/driver/').content.decode())   # во активното возење

    def test_choice_on_ride_page(self):
        html = self.user.post('/ride/new/', {'action': 'estimate', **RIDE}).content.decode()
        self.assertIn('Како ќе платите?', html)
        self.assertRegex(html, r'value="CASH"\s+checked')

    def test_default_is_cash(self):
        self.request_ride(self.user)
        self.assertEqual(self.latest_ride().payment_method.payment_tip, 'CASH')

    def test_pay_by_card(self):
        ride = self.card_ride()
        self.assertIn('Корисникот плаќа со картичка', self.client.get('/driver/').content.decode())
        self.assertIn('Плати со картичка', self.user.get('/ride/current/').content.decode())
        response = self.user.post('/pay/card/', CARD, follow=True)
        self.assertIn(f'Платени {RIDE_PRICE} ден. со Visa •••• 4242.', messages_of(response))
        self.assertIn('Платено со картичка Visa •••• 4242', response.content.decode())
        payment = self.payment(ride)
        self.assertEqual((payment.status.status_text, payment.payment_method.payment_tip), ('completed', 'CARD'))
        tx = CardTransactions.objects.get(payment=payment)
        self.assertEqual((tx.card_brand, tx.card_last4, tx.approved), ('VISA', '4242', True))
        self.assertIsNone(self.client.get('/driver/').context['collect'])
        self.assertTrue(Notifications.objects.filter(ride=ride, message__message_text='Plakjanjeto e uspesno').exists())
        self.assertIn('•••• 4242', self.user.get('/rides/').content.decode())

    def test_declined_card_then_cash(self):
        ride = self.card_ride()
        response = self.user.post('/pay/card/', DECLINED, follow=True)
        self.assertTrue(any('Картичката е одбиена од банката.' in m for m in messages_of(response)))
        self.assertEqual(self.payment(ride).status.status_text, 'failed')
        self.assertFalse(CardTransactions.objects.get(payment=self.payment(ride)).approved)
        self.assertTrue(Notifications.objects.filter(ride=ride, message__message_text='Plakjanjeto ne uspea').exists())
        collect = self.client.get('/driver/').context['collect']
        self.assertTrue(collect['by_card'] and collect['card_failed'])
        # корисникот плаќа во готовина кај возачот
        self.client.post('/driver/action/', {'action': 'cash_received', 'ride_id': str(ride.pk)})
        payment = self.payment(ride)
        self.assertEqual((payment.status.status_text, payment.payment_method.payment_tip), ('completed', 'CASH'))

    def test_card_number_is_never_stored_or_shown_back(self):
        ride = self.card_ride()
        response = self.user.post('/pay/card/', dict(CARD, card_expiry='01/20'))   # истечена -> формата се враќа
        self.assertIn('Картичката е истечена.', field_errors(response))
        html = response.content.decode()
        self.assertNotIn('value="4242', html)          # (4242 се гледа само во демо упатството и placeholder)
        self.assertNotIn('4242424242424242', html)
        self.user.post('/pay/card/', CARD)
        with connection.cursor() as cursor:   # сите редови од табелите за плаќање како текст
            cursor.execute("SELECT (SELECT string_agg(ct::text, '|') FROM Card_transactions ct) || "
                           "(SELECT string_agg(p::text, '|') FROM Payments p)")
            dump = cursor.fetchone()[0]
        self.assertIn('4242', dump)
        self.assertNotIn('424242424242', dump)
        self.assertNotIn('424242424242', str(dict(self.user.session)))
        self.assertEqual(self.payment(ride).status.status_text, 'completed')

    def test_cancellation_fee_paid_by_card_right_away(self):
        self.request_ride(self.user, payment_method='CARD')
        fee_ride = self.drive(self.client, self.latest_ride(), ('accept',))
        self.user.post('/ride/cancel/')
        self.assertIn('Плати со картичка', self.user.get('/ride/current/').content.decode())
        self.user.post('/pay/card/', CARD)
        self.assertEqual(self.payment(fee_ride).status.status_text, 'completed')
        self.assertFalse(self.user.get('/ride/current/').context['unpaid_fees'])
        self.full_ride(self.user, self.client, pay=False)
        self.assertFalse(self.client.get('/driver/').context['collect']['fees'])

    def test_card_pays_ride_and_old_fee_together(self):
        self.request_ride(self.user)
        fee_ride = self.drive(self.client, self.latest_ride(), ('accept',))
        self.user.post('/ride/cancel/')
        ride = self.card_ride()
        self.assertEqual(self.user.get('/pay/card/').context['total'], RIDE_PRICE + Decimal('50.00'))
        self.user.post('/pay/card/', CARD)
        self.assertEqual({self.payment(fee_ride).status.status_text, self.payment(ride).status.status_text}, {'completed'})

    def test_nothing_to_pay(self):
        response = self.user.get('/pay/card/', follow=True)
        self.assertIn('Немате ништо за плаќање.', messages_of(response))

    def test_only_users_can_pay(self):
        self.assertRedirects(self.client.get('/pay/card/'), '/', fetch_redirect_response=False)

    def test_paid_twice_is_rejected(self):
        ride = self.card_ride()
        self.client.post('/driver/action/', {'action': 'cash_received', 'ride_id': str(ride.pk)})
        with self.assertRaisesMessage(Exception, 'Нема плаќање што чека'):
            db.process_payment(ride.pk, 'CARD', True, 'VISA', '4242')

    def test_card_payment_needs_card_details(self):
        ride = self.card_ride()
        with self.assertRaisesMessage(Exception, 'потребни се тип и последни 4 цифри'):
            db.process_payment(ride.pk, 'CARD')


class NettingTest(GeocabTestCase):
    """Пребивање: провизија од готовина - заработка на возачот од картички - уплати + исплати."""

    def setUp(self):
        super().setUp()
        self.make_user('ana')
        self.user = self.login('user', 'ana')
        self.driver = self.make_driver('marko')
        self.start_shift(self.driver)
        self.client = self.login('driver', 'marko')
        self.admin_client = self.login('admin', 'admin')
        self.url = f'/manage/drivers/{self.driver.pk}/action/'

    def cash_ride(self, when=None):
        ride = self.full_ride(self.user, self.client)
        if when:
            self.set_payment_time(ride, when)
        return ride

    def card_ride(self, when=None):
        self.request_ride(self.user, payment_method='CARD')
        ride = self.drive(self.client, self.latest_ride())
        self.user.post('/pay/card/', CARD)
        if when:
            self.set_payment_time(ride, when)
        return ride

    def balance(self):
        return db.fetch_view('vw_driver_commission_balance', 'driver_id = %s', [self.driver.pk])[0]

    def test_card_earnings_offset_cash_commission(self):
        self.cash_ride()
        self.card_ride()
        b = self.balance()
        self.assertEqual((b['total_commission'], b['card_earnings'], b['balance']), (COMMISSION, DRIVER_SHARE, COMMISSION - DRIVER_SHARE))
        self.assertIn(f'GEOCAB ви должи: {DRIVER_SHARE - COMMISSION} ден.', self.client.get('/driver/profile/').content.decode())
        summary = db.fetch_view('vw_driver_earnings_summary', 'driver_id = %s', [self.driver.pk])[0]
        self.assertEqual((summary['cash_collected'], summary['card_collected']), (RIDE_PRICE, RIDE_PRICE))

    def test_card_earnings_count_as_payment_for_old_cash_debt(self):
        cash = self.cash_ride()
        card = self.card_ride()
        self.set_payment_time(cash, '2026-01-10')   # стара провизија од готовина...
        self.assertIsNone(db.driver_debt_status(self.driver.pk)['level'])   # ...ја покрива заработката од картичката
        # без таа заработка (вратени пари на корисникот) долгот е пак стар -> блокада
        db.refund_payment(self.payment(card).pk, self.admin.pk)
        self.assertTrue(db.driver_debt_status(self.driver.pk)['is_blocked'])

    def test_only_card_rides_never_block(self):
        self.card_ride('2026-01-10')
        d = db.call_table_function('fn_driver_debt_status', self.driver.pk, date(2026, 12, 1))[0]
        self.assertEqual((d['total_commission'], d['is_blocked']), (0, False))

    def test_admin_pays_out_to_driver(self):
        self.card_ride()
        page = self.admin_client.get(f'/manage/drivers/{self.driver.pk}/')
        self.assertEqual(page.context['owed_to_driver'], DRIVER_SHARE)
        self.assertIn('Исплати на возачот', page.content.decode())
        response = self.admin_client.post(self.url, {'action': 'driver_payout', 'amount': '1000'}, follow=True)
        self.assertTrue(any('поголем од она што GEOCAB му должи' in m for m in messages_of(response)))
        # возачот не може да „уплати провизија“ кога нема долг
        response = self.admin_client.post(self.url, {'action': 'payout', 'amount': '10'}, follow=True)
        self.assertTrue(any('е поголем од долгот' in m for m in messages_of(response)))
        response = self.admin_client.post(self.url, {'action': 'driver_payout', 'amount': str(DRIVER_SHARE), 'note': 'на сметка'}, follow=True)
        self.assertIn(f'Евидентирана исплата од {DRIVER_SHARE} ден. на {self.driver.name}.', messages_of(response))
        payout = DriverPayouts.objects.get(driver=self.driver)
        self.assertEqual((payout.amount, payout.admin_id, payout.note), (DRIVER_SHARE, self.admin.pk, 'на сметка'))
        self.assertEqual(self.balance()['balance'], 0)
        self.assertIn('Исплатено од GEOCAB на', self.client.get('/driver/profile/').content.decode())

    def test_inactive_admin_cannot_pay_out(self):
        self.card_ride()
        inactive = self.make_admin('stari', active=False)
        with self.assertRaisesMessage(Exception, 'не постои или не е активен'):
            db.record_driver_payout(self.driver.pk, inactive.pk, 10)

    def test_refund_card_payment(self):
        ride = self.card_ride()
        payment = self.payment(ride)
        response = self.admin_client.post(f'/manage/rides/{ride.pk}/action/', {'action': 'refund', 'payment_id': payment.pk}, follow=True)
        self.assertIn(f'Вратени се {RIDE_PRICE} ден. на корисникот на картичката.', messages_of(response))
        self.assertEqual(self.balance()['card_earnings'], 0)
        detail = self.admin_client.get(f'/manage/rides/{ride.pk}/').content.decode()
        self.assertIn('Visa •••• 4242', detail)

    def test_admin_dashboard_shows_card_money(self):
        self.card_ride()
        context = self.admin_client.get('/manage/').context
        self.assertEqual((context['money']['card'], context['owed_to_drivers']), (RIDE_PRICE, DRIVER_SHARE))
