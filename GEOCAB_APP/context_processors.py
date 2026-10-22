from django.conf import settings

from .models import Notifications
from .views import NOTIFICATIONS_SEEN_KEY, NOTIFICATIONS_TOAST_KEY


def notifications(request):
    """Број на нови известувања за 🔔 во менито (само за најавен корисник)."""
    person = request.session.get('geocab') if hasattr(request, 'session') else None
    if not person or person.get('role') != 'user':
        return {}
    seen = request.session.get(NOTIFICATIONS_SEEN_KEY, 0)
    if NOTIFICATIONS_TOAST_KEY not in request.session:
        # pop-up само за известувањата што ќе дојдат од сега, не за старите
        last = Notifications.objects.filter(user_id=person['id']).order_by('-notification_id').first()
        request.session[NOTIFICATIONS_TOAST_KEY] = last.notification_id if last else 0
    return {'new_notifications': Notifications.objects.filter(user_id=person['id'], notification_id__gt=seen).count()}


def contact(request):
    return {'contact': settings.GEOCAB_CONTACT}


def open_reports(request):
    """Број на отворени пријави за менито на администраторот."""
    person = request.session.get('geocab') if hasattr(request, 'session') else None
    if not person or person.get('role') != 'admin':
        return {}
    from .models import RideReports
    return {'open_reports_count': RideReports.objects.filter(status__status_text='open').count()}
