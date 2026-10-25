"""
Страници за грешки што Django не ги прикажува сам со наш шаблон.

400, 403, 404 и 500 ги прикажува Django од шаблоните 400.html, 403.html, 404.html и 500.html
(само кога DEBUG = False). Тука се:
- csrf_failure: застарена форма (CSRF_FAILURE_VIEW во settings),
- MethodNotAllowedMiddleware: 405 од @require_POST, кој инаку е празна бела страница.
"""
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def csrf_failure(request, reason=''):
    """Формата била отворена предолго, имало најава/одјава во друг таб или нема колачиња."""
    back_url = request.META.get('HTTP_REFERER', '')
    # копчето „Отвори ја страницата повторно“ води само на страница од GEOCAB
    if not url_has_allowed_host_and_scheme(back_url, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        back_url = reverse('home')
    return render(request, '403_csrf.html', {'back_url': back_url}, status=403)


class MethodNotAllowedMiddleware:
    """GET на адреса што прифаќа само POST (на пр. рачно отворен /logout/) -> наша страница 405."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code == 405 and not response.content:
            page = render(request, '405.html', status=405)
            page['Allow'] = response.get('Allow', '')
            return page
        return response
