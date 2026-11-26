"""Agrega los triggers que protegen la edición de vigencias y cobros.

Una base nueva ya los recibe desde esquema.sql (migración 0001); por eso aquí
se usa CREATE TRIGGER IF NOT EXISTS: solo actúa en bases creadas antes de este
cambio, sin tener que borrarlas.
"""
from django.db import migrations

TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS cobros_dentro_de_vigencia_editada
    BEFORE UPDATE OF fecha_inicio, fecha_fin ON vigencias
    WHEN EXISTS (
        SELECT 1 FROM cobros
        WHERE vigencia_id = NEW.id
          AND fecha_vencimiento NOT BETWEEN NEW.fecha_inicio AND NEW.fecha_fin
    )
    BEGIN
        SELECT RAISE(ABORT, 'Hay cobros fuera de las nuevas fechas de la vigencia; corrígelos o elimínalos primero.');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS cobro_editado_dentro_de_vigencia
    BEFORE UPDATE OF fecha_vencimiento ON cobros
    WHEN NEW.fecha_vencimiento NOT BETWEEN
         (SELECT fecha_inicio FROM vigencias WHERE id = NEW.vigencia_id)
     AND (SELECT fecha_fin    FROM vigencias WHERE id = NEW.vigencia_id)
    BEGIN
        SELECT RAISE(ABORT, 'La fecha del cobro debe estar dentro de la vigencia.');
    END;
    """,
]


class Migration(migrations.Migration):
    dependencies = [("interfaz", "0001_inicial")]
    operations = [
        migrations.RunSQL(
            TRIGGERS,
            reverse_sql=[
                "DROP TRIGGER IF EXISTS cobros_dentro_de_vigencia_editada;",
                "DROP TRIGGER IF EXISTS cobro_editado_dentro_de_vigencia;",
            ],
        ),
    ]
