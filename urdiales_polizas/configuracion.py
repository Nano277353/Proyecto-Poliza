"""Lee config.toml (paradigma declarativo): lo que cambia sin tocar el código.

Uso:
    from urdiales_polizas.configuracion import cargar_configuracion
    configuracion = cargar_configuracion()
    configuracion["anticipacion_dias"]   # 30
    configuracion["dias_inhabiles"]      # (date(2026, 12, 25), ...)
    configuracion["aseguradoras"]        # ("Chop Seguros", "Banorte", ...)

La variable de entorno URDIALES_CONFIG permite usar otro archivo (p. ej. en pruebas).
"""
from datetime import date
import os
from pathlib import Path
import tomllib

BASE_DIR = Path(__file__).resolve().parent.parent
ASEGURADORAS_POR_OMISION = ("Chop Seguros", "Banorte", "GNP", "Acsa Seguros", "Qualitas", "Afirme")


def _anticipacion(valor):
    if isinstance(valor, bool) or not isinstance(valor, int) or not 0 <= valor <= 365:
        raise ValueError('anticipacion_dias debe ser un entero entre 0 y 365.')
    return valor


def _dias_inhabiles(valores):
    """Convierte la lista de config.toml en una tupla ordenada de fechas sin repetir."""
    if not isinstance(valores, list):
        raise ValueError('dias_inhabiles debe ser una lista de fechas AAAA-MM-DD.')
    fechas = set()
    for valor in valores:
        if isinstance(valor, date):
            fechas.add(valor)
            continue
        try:
            fechas.add(date.fromisoformat(str(valor).strip()))
        except ValueError:
            raise ValueError(f'Día inhábil no válido en config.toml: {valor!r}. Usa AAAA-MM-DD.') from None
    return tuple(sorted(fechas))


def _aseguradoras(valores):
    if not isinstance(valores, list) or not all(isinstance(v, str) and v.strip() for v in valores):
        raise ValueError('aseguradoras debe ser una lista de nombres en config.toml.')
    return tuple(dict.fromkeys(" ".join(v.split()) for v in valores))


def cargar_configuracion():
    ruta = Path(os.environ.get('URDIALES_CONFIG', BASE_DIR / 'config.toml'))
    with ruta.open('rb') as archivo:
        configuracion = tomllib.load(archivo)
    return {
        'anticipacion_dias': _anticipacion(configuracion.get('anticipacion_dias', 30)),
        'dias_inhabiles': _dias_inhabiles(configuracion.get('dias_inhabiles', [])),
        'aseguradoras': _aseguradoras(configuracion.get('aseguradoras', list(ASEGURADORAS_POR_OMISION))),
    }
