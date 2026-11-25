"""Crea las tablas leyendo persistencia/esquema.sql (SQL directo, sin modelos de Django)."""
from pathlib import Path

from django.db import migrations


ESQUEMA = (Path(__file__).resolve().parents[2] / "persistencia" / "esquema.sql").read_text(encoding="utf-8")


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.RunSQL(
            ESQUEMA,
            reverse_sql="""
                DROP TABLE cobros;
                DROP TABLE vehiculos_asegurados;
                DROP TABLE vigencias;
                DROP TABLE polizas;
                DROP TABLE aseguradoras;
                DROP TABLE medios_contacto;
                DROP TABLE contactos;
                DROP TABLE asegurados;
            """,
        ),
    ]
