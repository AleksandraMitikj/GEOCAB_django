"""
Мејлови во развој: наместо да се праќаат, се печатат во терминалот на runserver.

Стандардниот Django console backend го печати мејлот како суров MIME (base64), па кирилицата и
линковите не се читаат. Овој го печати читливо: до кого, наслов и текстот на мејлот со линкот,
за да може линкот за потврда / нова лозинка да се копира и отвори во прелистувачот.
"""
from django.core.mail.backends.console import EmailBackend


class ReadableConsoleEmailBackend(EmailBackend):
    def write_message(self, message):
        line = '=' * 79
        self.stream.write(
            f'\n{line}\n📧 МЕЈЛ (во развој не се праќа - се печати тука)\n'
            f'До:     {", ".join(message.to)}\n'
            f'Од:     {message.from_email}\n'
            f'Наслов: {message.subject}\n'
            f'{"-" * 79}\n{message.body.rstrip()}\n{line}\n\n'
        )
