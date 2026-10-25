"""URL-а само за тестовите на страниците за грешки: view-а што намерно паѓаат."""
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.urls import path

from GEOCAB.urls import urlpatterns as geocab_urlpatterns


def boom(request):
    raise RuntimeError('намерна грешка за тест')


def bad(request):
    raise SuspiciousOperation('намерно неисправно барање за тест')


def forbidden(request):
    raise PermissionDenied


urlpatterns = [
    path('test-errors/500/', boom),
    path('test-errors/400/', bad),
    path('test-errors/403/', forbidden),
] + geocab_urlpatterns
