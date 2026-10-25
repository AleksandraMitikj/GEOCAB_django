"""Страници за грешки: 400, 403, 403 CSRF, 404, 405, 500 (наш дизајн, на македонски)."""
from django.template.loader import render_to_string
from django.test import Client, override_settings

from .base import PASSWORD, GeocabTestCase


@override_settings(DEBUG=False, ROOT_URLCONF='GEOCAB_APP.tests.error_urls')
class ErrorPagesTest(GeocabTestCase):
    def client_without_exceptions(self):
        client = Client()
        client.raise_request_exception = False   # како вистински сервер: грешката -> страница 500
        return client

    def test_404_unknown_address(self):
        response = Client().get('/nema-takva-stranica/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Оваа улица ја нема на нашата мапа', status_code=404)
        self.assertContains(response, 'Грешка 404', status_code=404)
        self.assertContains(response, 'Регистрација', status_code=404)   # мени за гостин од base.html
        self.assertNotContains(response, '{#', status_code=404)          # коментар од шаблонот не смее да се гледа

    def test_404_buttons_per_role(self):
        self.make_user('ana')
        html = self.login('user', 'ana').get('/nema/').content.decode()
        self.assertIn('Побарај возење', html)
        driver = self.make_driver('marko')
        self.assertIn('Моја смена', self.login('driver', driver.username).get('/nema/').content.decode())
        self.assertIn('Админ панел', self.login('admin', 'admin').get('/nema/').content.decode())

    def test_404_missing_object_in_manage(self):
        response = self.login('admin', 'admin').get('/manage/drivers/999999/')
        self.assertContains(response, 'Оваа улица ја нема на нашата мапа', status_code=404)

    def test_500_page(self):
        response = self.client_without_exceptions().get('/test-errors/500/')
        self.assertContains(response, 'Моторот застана', status_code=500)
        self.assertContains(response, 'Обиди се повторно', status_code=500)

    def test_500_works_without_database_and_session(self):
        # Django ја прикажува 500 без request; страницата не смее да чита од базата
        with self.assertNumQueries(0):
            html = render_to_string('500.html')
        self.assertIn('Моторот застана', html)
        self.assertNotIn('Одјава', html)
        self.assertNotIn('{#', html)

    def test_400_page(self):
        response = self.client_without_exceptions().get('/test-errors/400/')
        self.assertContains(response, 'Неисправно барање', status_code=400)

    def test_400_unknown_host(self):
        response = Client().get('/', HTTP_HOST='nepoznat-domen.example')
        self.assertContains(response, 'Неисправно барање', status_code=400)

    def test_403_page(self):
        response = Client().get('/test-errors/403/')
        self.assertContains(response, 'Немате пристап до оваа страница', status_code=403)

    def test_csrf_stale_form(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post('/login/', {'role': 'admin', 'username': 'admin', 'password': PASSWORD},
                               HTTP_REFERER='http://testserver/login/')
        self.assertContains(response, 'Страницата застарела', status_code=403)
        self.assertContains(response, 'href="http://testserver/login/"', status_code=403)

    def test_csrf_back_link_only_to_geocab(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post('/login/', {}, HTTP_REFERER='https://zlonameren.example/')
        self.assertContains(response, 'Страницата застарела', status_code=403)
        self.assertNotContains(response, 'zlonameren', status_code=403)

    def test_405_get_on_post_only_address(self):
        response = Client().get('/logout/')
        self.assertContains(response, 'Оваа акција се прави преку копче', status_code=405)
        self.assertIn('POST', response['Allow'])
