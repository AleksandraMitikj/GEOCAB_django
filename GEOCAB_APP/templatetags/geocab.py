import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

RIDE_STATUS = {
    'requested': 'Барање', 'accepted': 'Прифатено', 'driver_arrived': 'Возачот пристигна',
    'in_progress': 'Во тек', 'completed': 'Завршено', 'cancelled': 'Откажано', 'no_driver': 'Немаше возач',
}


@register.filter
def ride_status(value):
    """completed -> Завршено"""
    return RIDE_STATUS.get(value, value)


@register.filter
def message_label(value):
    """Порака од базата (латиница) -> кирилица"""
    from GEOCAB_APP.views import MESSAGE_LABELS
    return MESSAGE_LABELS.get(value, value)


@register.simple_tag
def static_v(path):
    """Како {% static %}, но со ?v=<време на промена>, за прелистувачот секогаш да ја земе
    новата верзија на JS/CSS фајлот наместо старата од кешот."""
    url = static(path)
    found = finders.find(path)
    return f'{url}?v={int(os.path.getmtime(found))}' if found else url
