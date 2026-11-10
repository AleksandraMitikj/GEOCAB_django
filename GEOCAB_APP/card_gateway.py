"""
Платежна порта за картички - ДЕМО (не се наплаќаат вистински пари).

Се однесува како вистинска порта: ја проверува картичката и враќа „одобрено“ или „одбиено“.
Кога GEOCAB ќе има договор со банка (на пр. CaSys/cPay), се менува само функцијата charge();
остатокот од апликацијата ја користи само неа и резултатот CardResult.

Безбедност: целиот број и CVV се користат само во оваа функција и никаде не се запишуваат
(ни во базата, ни во сесијата, ни во логовите). Во базата одат само типот и последните 4 цифри.

Тест картички (како кај познатите порти):
    4242 4242 4242 4242  -> одобрено (Visa)
    5555 5555 5555 4444  -> одобрено (Mastercard)
    4000 0000 0000 0002  -> одбиено од банката
    4000 0000 0000 9995  -> нема доволно средства
Секоја друга исправна картичка (Luhn) е одобрена.
"""
from dataclasses import dataclass

DEMO = True   # на страницата се прикажува „Демо плаќање - не се наплаќаат вистински пари“

DECLINED = {
    '4000000000000002': 'Картичката е одбиена од банката.',
    '4000000000009995': 'Нема доволно средства на картичката.',
}

BRAND_LABELS = {'VISA': 'Visa', 'MASTERCARD': 'Mastercard', 'MAESTRO': 'Maestro', 'AMEX': 'American Express',
                'OTHER': 'Картичка'}


@dataclass(frozen=True)
class CardResult:
    approved: bool
    brand: str      # VISA / MASTERCARD / MAESTRO / AMEX / OTHER
    last4: str
    message: str


def digits_only(number):
    """'4242 4242-4242 4242' -> '4242424242424242'"""
    return ''.join(ch for ch in str(number) if ch.isdigit())


def luhn_ok(number):
    """Контролна цифра (Luhn) - ги фаќа погрешно внесените броеви."""
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return len(number) >= 12 and total % 10 == 0


def card_brand(number):
    if number.startswith('4'):
        return 'VISA'
    if number[:2] in ('34', '37'):
        return 'AMEX'
    if 51 <= int(number[:2] or 0) <= 55 or 2221 <= int(number[:4] or 0) <= 2720:
        return 'MASTERCARD'
    if number[:2] in ('50', '56', '57', '58') or number.startswith('6'):
        return 'MAESTRO'
    return 'OTHER'


def charge(number, amount):
    """Наплата од картичката. number = само цифри (CardPaymentForm го чисти и проверува)."""
    brand, last4 = card_brand(number), number[-4:]
    reason = DECLINED.get(number)
    if reason:
        return CardResult(False, brand, last4, reason)
    return CardResult(True, brand, last4, f'Платени {amount} ден. со {BRAND_LABELS[brand]} •••• {last4}.')
