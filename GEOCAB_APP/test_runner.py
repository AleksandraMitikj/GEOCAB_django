"""
Тест стартер: python manage.py test

Django креира празна тест база (test_geocab_app) и ги прави своите табели (auth, sessions...).
Табелите на GEOCAB се managed = False, затоа тука се извршуваат geocab_schema.sql и
geocab_lookup_data.sql - тестовите ги проверуваат вистинските тригери, процедури и правила.
Тест базата се брише на крајот; geocab_app никогаш не се допира.
"""
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.test.runner import DiscoverRunner

SQL_FILES = ('geocab_schema.sql', 'geocab_lookup_data.sql')


class GeocabTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        config = super().setup_databases(**kwargs)
        sql_dir = Path(settings.GEOCAB_SQL_DIR)
        for alias in connections:
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.rides')")
                if cursor.fetchone()[0]:  # --keepdb: шемата веќе постои
                    continue
                for name in SQL_FILES:
                    cursor.execute((sql_dir / name).read_text(encoding='utf-8'))
        return config
