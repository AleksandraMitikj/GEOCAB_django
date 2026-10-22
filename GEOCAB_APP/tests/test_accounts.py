"""Јавни страници, регистрација, најава/одјава, заштита по улога, лозинки и профил."""
import re

from django.contrib.auth.hashers import check_password
from django.core import mail
from django.test import Client

from GEOCAB_APP.models import Drivers, Users

from .base import PASSWORD, GeocabTestCase, field_errors, messages_of, status

REGISTER = dict(name='Ана Петрова', username='ana_new', phone='070111222', email='ana.new@test.mk',
                password1='Geocab2026!nova', password2='Geocab2026!nova')


class PublicPagesTest(GeocabTestCase):
    def test_public_pages_open(self):
        for url in ('/', '/about/', '/login/', '/register/', '/password/forgot/'):
            self.assertEqual(Client().get(url).status_code, 200, url)

    def test_home_shows_cash_only_and_real_prices(self):
        html = Client().get('/').content.decode()
        self.assertIn('Плаќање само во готовина', html)
        self.assertNotIn('картичка', html.lower().replace('без картички', ''))
        self.assertIn('data-base="100.0"', html)      # Standard почетна цена од базата
        self.assertIn('data-per-km="60.0"', html)     # Premium цена по km

    def test_home_buttons_per_role(self):
        self.assertIn('Започни - бесплатно', Client().get('/').content.decode())
        self.make_user('ana')
        self.assertIn('Побарај возење', self.login('user', 'ana').get('/').content.decode())
        self.make_driver('marko')
        self.assertIn('Моја смена', self.login('driver', 'marko').get('/').content.decode())
        self.assertIn('Админ панел', self.login('admin', 'admin').get('/').content.decode())

    def test_about_page_contact_and_driver_share(self):
        html = Client().get('/about/').content.decode()
        self.assertIn('Како да станете GEOCAB возач', html)
        self.assertIn('80%', html)
        self.assertIn('mailto:', html)

    def test_driver_share_follows_commission(self):
        from GEOCAB_APP.models import PlatformSettings
        PlatformSettings.objects.filter(pk=1).update(commission_percentage=25)
        self.assertIn('75%', Client().get('/').content.decode())


class RegisterTest(GeocabTestCase):
    def test_register_creates_active_user_and_logs_in(self):
        client = Client()
        response = client.post('/register/', REGISTER, follow=True)
        user = Users.objects.get(username='ana_new')
        self.assertEqual(user.status.status_text, 'active')
        self.assertTrue(check_password('Geocab2026!nova', user.password_hash))
        self.assertIn('Ана Петрова', response.content.decode())
        self.assertEqual(client.session['geocab']['role'], 'user')

    def test_register_validation(self):
        response = Client().post('/register/', {**REGISTER, 'phone': 'abc', 'email': 'losa', 'password2': 'drugo'})
        errors = field_errors(response)
        self.assertIn('Телефонот смее да содржи само бројки и +.', errors)
        self.assertIn('Невалидна е-пошта.', errors)
        self.assertIn('Лозинките не се совпаѓаат.', errors)
        self.assertFalse(Users.objects.filter(username='ana_new').exists())

    def test_register_weak_password(self):
        response = Client().post('/register/', {**REGISTER, 'password1': '123', 'password2': '123'})
        self.assertTrue(any('too short' in e for e in field_errors(response)))

    def test_register_duplicates_rejected(self):
        self.make_user('ana')
        existing = Users.objects.get(username='ana')
        response = Client().post('/register/', {**REGISTER, 'username': 'ana', 'phone': existing.phone, 'email': existing.email})
        self.assertEqual(len([e for e in field_errors(response) if 'already exists' in e]), 3)

    def test_logged_in_user_is_redirected_from_register_and_login(self):
        self.make_user('ana')
        client = self.login('user', 'ana')
        self.assertEqual(client.get('/register/').url, '/')
        self.assertEqual(client.get('/login/').url, '/')


class LoginTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('ana')
        self.driver = self.make_driver('marko')

    def test_login_redirects_per_role(self):
        self.assertEqual(Client().post('/login/', {'role': 'user', 'username': 'ana', 'password': PASSWORD}).url, '/')
        self.assertEqual(Client().post('/login/', {'role': 'driver', 'username': 'marko', 'password': PASSWORD}).url, '/driver/')
        self.assertEqual(Client().post('/login/', {'role': 'admin', 'username': 'admin', 'password': PASSWORD}).url, '/manage/')

    def test_wrong_password_or_wrong_role(self):
        for role, username, password in [('user', 'ana', 'pogresna'), ('driver', 'ana', PASSWORD), ('admin', 'marko', PASSWORD),
                                         ('user', 'nepostoi', PASSWORD)]:
            response = Client().post('/login/', {'role': role, 'username': username, 'password': password})
            self.assertEqual(response.status_code, 200)
            self.assertIn('Погрешно корисничко име или лозинка.', response.content.decode())

    def test_inactive_profiles_cannot_login(self):
        Users.objects.filter(pk=self.user.pk).update(status=status('user', 'blocked'))
        Drivers.objects.filter(pk=self.driver.pk).update(status=status('driver', 'inactive'))
        for role, username in [('user', 'ana'), ('driver', 'marko')]:
            response = Client().post('/login/', {'role': role, 'username': username, 'password': PASSWORD})
            self.assertIn('Профилот не е активен', response.content.decode())

    def test_safe_next_redirect(self):
        response = Client().post('/login/?next=/rides/', {'role': 'user', 'username': 'ana', 'password': PASSWORD})
        self.assertEqual(response.url, '/rides/')
        response = Client().post('/login/?next=https://evil.com/', {'role': 'user', 'username': 'ana', 'password': PASSWORD})
        self.assertEqual(response.url, '/')

    def test_logout_requires_post(self):
        client = self.login('user', 'ana')
        self.assertEqual(client.get('/logout/').status_code, 405)
        response = client.post('/logout/', follow=True)
        self.assertIn('Одјавени сте.', messages_of(response))
        self.assertNotIn('geocab', client.session)

    def test_blocked_during_session_is_logged_out(self):
        client = self.login('user', 'ana')
        Users.objects.filter(pk=self.user.pk).update(status=status('user', 'blocked'))
        self.assertTrue(client.get('/ride/new/').url.startswith('/login/'))

    def test_session_switches_when_logging_in_as_other_role(self):
        client = self.login('user', 'ana')
        client.post('/logout/')
        client.post('/login/', {'role': 'driver', 'username': 'marko', 'password': PASSWORD})
        self.assertEqual(client.session['geocab']['role'], 'driver')


class RoleProtectionTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.make_user('ana')
        self.make_driver('marko')
        self.clients = {'none': Client(), 'user': self.login('user', 'ana'),
                        'driver': self.login('driver', 'marko'), 'admin': self.login('admin', 'admin')}

    PAGES = {
        'user': ['/ride/new/', '/ride/current/', '/rides/', '/notifications/', '/notifications/poll/', '/ride/current/track/'],
        'driver': ['/driver/', '/driver/profile/', '/driver/status/'],
        'admin': ['/manage/', '/manage/drivers/', '/manage/drivers/new/', '/manage/rides/', '/manage/users/',
                  '/manage/settings/', '/manage/reports/'],
        'any': ['/account/', '/account/password/'],
    }

    def test_each_role_only_sees_its_pages(self):
        for owner, urls in self.PAGES.items():
            for url in urls:
                for who, client in self.clients.items():
                    response = client.get(url)
                    allowed = who != 'none' and (owner == 'any' or owner == who)
                    if allowed:
                        self.assertEqual(response.status_code, 200, f'{who} {url}')
                    else:
                        self.assertEqual(response.status_code, 302, f'{who} {url}')
                        expected = '/login/' if who == 'none' else '/'
                        self.assertTrue(response.url.startswith(expected), f'{who} {url} -> {response.url}')

    def test_post_actions_reject_get(self):
        admin = self.clients['admin']
        for url in ('/logout/',):
            self.assertEqual(admin.get(url).status_code, 405)
        self.assertEqual(self.clients['user'].get('/ride/cancel/').status_code, 405)
        self.assertEqual(self.clients['driver'].get('/driver/action/').status_code, 405)
        self.assertEqual(self.clients['driver'].get('/driver/position/').status_code, 405)

    def test_forms_have_csrf_protection(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post('/login/', {'role': 'user', 'username': 'ana', 'password': PASSWORD})
        self.assertEqual(response.status_code, 403)


class PasswordTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('ana')
        self.driver = self.make_driver('marko')

    def test_change_password(self):
        client = self.login('driver', 'marko')
        response = client.post('/account/password/', {'old_password': 'pogresna', 'new_password1': '1', 'new_password2': '2'})
        self.assertIn('Сегашната лозинка не е точна.', field_errors(response))
        response = client.post('/account/password/', {'old_password': PASSWORD, 'new_password1': 'Nova2026!lozinka',
                                                      'new_password2': 'Nova2026!lozinka'}, follow=True)
        self.assertIn('Лозинката е променета.', messages_of(response))
        self.assertEqual(client.get('/driver/').status_code, 200)   # останува најавен
        self.login('driver', 'marko', 'Nova2026!lozinka')
        self.assertEqual(Client().post('/login/', {'role': 'driver', 'username': 'marko', 'password': PASSWORD}).status_code, 200)

    def _reset_link(self):
        return re.search(r'http://testserver(/password/reset/\S+/)', mail.outbox[-1].body).group(1)

    def test_forgot_password_full_flow(self):
        response = Client().post('/password/forgot/', {'role': 'user', 'email': 'ANA@test.mk'}, follow=True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ana@test.mk'])
        self.assertTrue(any('Ако постои профил' in m for m in messages_of(response)))
        link = self._reset_link()
        client = Client()
        self.assertIn(self.user.name, client.get(link).content.decode())
        response = client.post(link, {'new_password1': 'Reset2026!nova', 'new_password2': 'Reset2026!nova'}, follow=True)
        self.assertIn('Лозинката е поставена. Најавете се со новата лозинка.', messages_of(response))
        self.login('user', 'ana', 'Reset2026!nova')
        # линкот важи само еднаш
        response = Client().get(link, follow=True)
        self.assertIn('Линкот не е важечки или е истечен. Побарајте нов.', messages_of(response))

    def test_forgot_password_does_not_reveal_accounts(self):
        for data in ({'role': 'user', 'email': 'nema@test.mk'}, {'role': 'driver', 'email': 'ana@test.mk'}):
            response = Client().post('/password/forgot/', data, follow=True)
            self.assertTrue(any('Ако постои профил' in m for m in messages_of(response)))
        self.assertEqual(len(mail.outbox), 0)

    def test_forgot_password_blocked_profile_gets_no_mail(self):
        Users.objects.filter(pk=self.user.pk).update(status=status('user', 'blocked'))
        Client().post('/password/forgot/', {'role': 'user', 'email': 'ana@test.mk'})
        self.assertEqual(len(mail.outbox), 0)

    def test_fake_or_expired_reset_link(self):
        response = Client().get('/password/reset/lazen-token/', follow=True)
        self.assertIn('Линкот не е важечки или е истечен. Побарајте нов.', messages_of(response))
        Client().post('/password/forgot/', {'role': 'user', 'email': 'ana@test.mk'})
        link = self._reset_link()
        from unittest import mock
        with mock.patch('GEOCAB_APP.auth.RESET_MAX_AGE', -1):
            response = Client().get(link, follow=True)
        self.assertIn('Линкот не е важечки или е истечен. Побарајте нов.', messages_of(response))

    def test_admin_sets_password_for_driver_and_user(self):
        admin = self.login('admin', 'admin')
        response = admin.post(f'/manage/drivers/{self.driver.pk}/action/', {'action': 'set_password', 'new_password': '123'}, follow=True)
        self.assertTrue(any('Лозинката не е прифатена' in m for m in messages_of(response)))
        admin.post(f'/manage/drivers/{self.driver.pk}/action/', {'action': 'set_password', 'new_password': 'Admin2026!vozac'})
        self.login('driver', 'marko', 'Admin2026!vozac')
        admin.post(f'/manage/users/{self.user.pk}/action/', {'action': 'set_password', 'new_password': 'Admin2026!kor'})
        self.login('user', 'ana', 'Admin2026!kor')


class ProfileTest(GeocabTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('ana')
        self.other = self.make_user('petar')
        self.driver = self.make_driver('marko')

    def test_user_edits_own_profile(self):
        client = self.login('user', 'ana')
        self.assertEqual(list(client.get('/account/').context['form'].fields), ['name', 'phone', 'email'])
        response = client.post('/account/', {'name': 'Ана Нова', 'phone': '+38970999111', 'email': 'nova@test.mk'}, follow=True)
        self.user.refresh_from_db()
        self.assertEqual((self.user.name, self.user.phone, self.user.email), ('Ана Нова', '+38970999111', 'nova@test.mk'))
        self.assertIn('👤 Ана Нова', response.content.decode())   # новото име во менито
        self.assertTrue(check_password(PASSWORD, self.user.password_hash))

    def test_profile_validation_and_uniqueness(self):
        client = self.login('user', 'ana')
        errors = field_errors(client.post('/account/', {'name': 'X', 'phone': 'abc', 'email': 'x'}))
        self.assertIn('Телефонот смее да содржи само бројки и +.', errors)
        errors = field_errors(client.post('/account/', {'name': 'X', 'phone': self.other.phone, 'email': self.other.email}))
        self.assertEqual(len([e for e in errors if 'already exists' in e]), 2)

    def test_driver_can_only_change_contact(self):
        client = self.login('driver', 'marko')
        self.assertEqual(list(client.get('/account/').context['form'].fields), ['phone', 'email'])
        rating = Drivers.objects.get(pk=self.driver.pk).rating
        client.post('/account/', {'phone': '+38970999222', 'email': 'marko.nov@test.mk', 'name': 'Хакер', 'license_number': 'X'})
        driver = Drivers.objects.get(pk=self.driver.pk)
        self.assertEqual((driver.phone, driver.email), ('+38970999222', 'marko.nov@test.mk'))
        self.assertEqual(driver.name, self.driver.name)
        self.assertEqual(driver.license_number, self.driver.license_number)
        self.assertEqual(driver.rating, rating)

    def test_admin_edits_own_profile(self):
        client = self.login('admin', 'admin')
        response = client.post('/account/', {'name': 'Главен Админ', 'phone': '+38970000099', 'email': 'glaven@test.mk'}, follow=True)
        self.assertIn('Податоците се зачувани.', messages_of(response))
