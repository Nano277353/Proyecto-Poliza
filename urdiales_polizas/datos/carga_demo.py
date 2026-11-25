"""Carga del Excel del despacho exportado a CSV (paradigma imperativo).

El CSV conserva las columnas del Excel original (ver COLUMNAS). La carga avanza
en dos pasadas con estado explícito:

1. Limpieza: cada fila se convierte en datos válidos o en un error con su
   número de fila y motivo. Las filas con el mismo número de póliza de autos
   se agrupan como una flotilla (un vehículo por fila).
2. Guardado: cada póliza se guarda con persistencia/repositorios.py dentro de
   su propio punto de guardado, así que una póliza inválida no detiene a las demás.

Fechas: igual que en el Excel, hay una columna por mes (Ene … Dic). En cada mes
con cobro se escribe el día y sus marcas:
    ✓  cobro pagado        C  cobranza        R  renovación lista para cotizar
    *  fecha anual de renovación (no es un cobro mensual cualquiera)
Tipo de cobro: el Excel no lo trae en una columna. Si la fila tiene alguna C, la
cobranza es manual (hay que cobrarle al cliente); si no tiene ninguna, la póliza
está domiciliada (cargo automático a tarjeta o cuenta) y solo se verifica el cargo.
El mes con * y el "Año de renovación" dan el inicio de la vigencia anual.
También se acepta una sola columna "Fecha de renovación" (p. ej. "15/03 ✓").

Si el archivo no se puede leer o le faltan columnas, la base no se modifica.

Los colores del Excel (año en rojo, R/C en rojo o verde) se pierden al exportar
a CSV; la aplicación los recalcula con las fechas y las reglas de atención.
"""

import calendar
import csv
from datetime import date, timedelta
import io
from pathlib import Path
import re
import unicodedata

from django.db import DatabaseError, transaction
from django.utils import timezone


NOMBRES_MESES = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")
CAMPOS_MESES = tuple(f"mes_{numero}" for numero in range(1, 13))

# Columnas del Excel, en el orden en que se exportan.
COLUMNAS = (
    ("asegurado", "Asegurado"),
    ("compania", "Compañía de seguro"),
    ("anio", "Año de renovación"),
    ("numero_poliza", "Número de póliza"),
    *((f"mes_{numero}", nombre) for numero, nombre in enumerate(NOMBRES_MESES, start=1)),
    ("forma_pago", "Forma de pago"),
    ("auto", "Descripción del auto"),
    ("modelo", "Modelo"),
    ("danios", "Póliza de daños"),
    ("serie", "Serie"),
    ("telefono", "Teléfono"),
    ("correo", "Correo"),
    ("recomendo", "Recomendó"),
    ("recomendo_cel", "Cel de quien recomendó"),
    ("recomendo_correo", "Correo de quien recomendó"),
    ("estatus", "Estatus"),
)
ENCABEZADOS = tuple(titulo for _, titulo in COLUMNAS)
# El celular y correo de quien recomendó pueden venir dentro de "Recomendó" ("Nombre / 81...").
# Las fechas vienen en los 12 meses o, como alternativa, en "Fecha de renovación".
OPCIONALES = {"recomendo_cel", "recomendo_correo", "fecha", *CAMPOS_MESES}

# Nombres con los que puede venir cada columna (se comparan sin acentos ni mayúsculas).
SINONIMOS = {
    "asegurado": ("asegurado", "cliente", "nombre del asegurado"),
    "compania": ("compania de seguro", "compania de seguros", "compania", "aseguradora"),
    "anio": ("ano de renovacion", "ano renovacion", "ano"),
    "numero_poliza": ("numero de poliza", "no de poliza", "num de poliza", "numero poliza", "poliza"),
    "fecha": ("fecha de renovacion", "fecha renovacion", "renovacion"),
    "forma_pago": ("forma de pago", "pago"),
    "auto": ("descripcion del auto", "descripcion auto", "auto", "vehiculo"),
    "modelo": ("modelo",),
    "danios": ("poliza de danos", "danos"),
    "serie": ("serie", "serie del vehiculo", "no de serie", "numero de serie"),
    "telefono": ("telefono", "tel", "celular"),
    "correo": ("correo", "email", "correo electronico"),
    "recomendo": ("recomendo", "nombre de persona que recomendo", "nombre de quien recomendo",
                  "quien recomendo", "recomendado por", "referido"),
    "recomendo_cel": ("cel de quien recomendo", "cel recomendo", "telefono de quien recomendo"),
    "recomendo_correo": ("correo de quien recomendo", "correo recomendo"),
    "estatus": ("estatus", "status", "estado"),
}

FORMAS_PAGO = {"mensual": 1, "trimestral": 3, "semestral": 6, "anual": 12}
ESTATUS = {
    "en vigor": "en_vigor", "vigor": "en_vigor", "vigente": "en_vigor", "en_vigor": "en_vigor",
    "cancelada": "cancelada", "canceladas": "cancelada", "cancelado": "cancelada", "cancelados": "cancelada",
}
MESES = {
    "ene": 1, "enero": 1, "feb": 2, "febrero": 2, "mar": 3, "marzo": 3, "abr": 4, "abril": 4,
    "may": 5, "mayo": 5, "jun": 6, "junio": 6, "jul": 7, "julio": 7, "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "septiembre": 9, "set": 9, "setiembre": 9, "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11, "dic": 12, "diciembre": 12,
}
for _nombre, _numero in MESES.items():
    SINONIMOS[f"mes_{_numero}"] = SINONIMOS.get(f"mes_{_numero}", ()) + (_nombre,)
# La "tachita" de pagada; "ü" es la palomita de la fuente Wingdings al exportar.
PALOMITAS = ("✓", "✔", "√", "☑", "ü")

_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_NUMERICA = re.compile(r"(?<!\d)(\d{1,2})\s*[/.-]\s*(\d{1,2})(?:\s*[/.-]\s*(\d{2,4}))?(?!\d)")
_CON_MES = re.compile(r"(?<!\d)(\d{1,2})\s*(?:de\s+|[/.-]\s*)?([a-z]{3,10})\.?(?:\s*(?:de\s+|[/.-]\s*)(\d{4}))?")
_NOTACION_CIENTIFICA = re.compile(r"^\d+(\.\d+)?e[+-]?\d+$", re.IGNORECASE)
_CORREO = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_TELEFONO_EN_TEXTO = re.compile(r"\+?\d[\d\s().-]{5,}\d")
_ASEGURADORA_ENTRE_PARENTESIS = re.compile(r"^(?P<nombre>.*?)\s*\((?P<aseguradora>[^()]+)\)\s*$")


# --------------------------------------------------------------------------- limpieza

def normalizar(texto):
    """Minúsculas, sin acentos y sin signos: "Compañía  de Seguro:" → "compania de seguro"."""
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9_]+", " ", texto).split())


def _espacios(valor):
    return " ".join(str(valor or "").split())


def _sin_notacion_cientifica(valor, columna):
    if _NOTACION_CIENTIFICA.match(valor):
        raise ValueError(f"{columna} se exportó en notación científica ({valor}); "
                         "en Excel da formato de Texto a esa columna y vuelve a exportar.")


def _entero(valor, columna, minimo, maximo):
    texto = _espacios(valor)
    if texto.endswith(".0"):  # Excel exporta 2026 como 2026.0
        texto = texto[:-2]
    if not texto.isdigit() or not minimo <= int(texto) <= maximo:
        raise ValueError(f"{columna} debe ser un número entre {minimo} y {maximo}: {valor!r}.")
    return int(texto)


def _alfanumerico(valor, columna, separadores=""):
    texto = _espacios(valor).upper()
    _sin_notacion_cientifica(texto, columna)
    if not re.fullmatch(rf"[A-Z0-9][A-Z0-9{re.escape(separadores)}]*", texto.replace(" ", "")):
        raise ValueError(f"{columna} solo admite letras y números: {valor!r}.")
    return texto.replace(" ", "")


def _telefonos(valor, columna="El teléfono"):
    telefonos = []
    for parte in re.split(r"[;/,]", str(valor or "")):
        parte = parte.strip()
        if not parte:
            continue
        _sin_notacion_cientifica(parte, columna)
        digitos = re.sub(r"\D", "", parte[:-2] if parte.endswith(".0") else parte)
        if not 7 <= len(digitos) <= 15:
            raise ValueError(f"{columna} no es válido: {parte!r}.")
        telefonos.append(digitos)
    return list(dict.fromkeys(telefonos))


def _correos(valor, columna="El correo"):
    correos = []
    for parte in re.split(r"[;,\s]+", str(valor or "")):
        parte = parte.strip().lower()
        if not parte:
            continue
        if not _CORREO.fullmatch(parte):
            raise ValueError(f"{columna} no es válido: {parte!r}.")
        correos.append(parte)
    return list(dict.fromkeys(correos))


def _sumar_meses(fecha, meses):
    anio, mes = divmod(fecha.month - 1 + meses, 12)
    anio += fecha.year
    return date(anio, mes + 1, min(fecha.day, calendar.monthrange(anio, mes + 1)[1]))


def _separar_marcas(crudo):
    """Quita ✓ y * de la celda y deja el texto en minúsculas, sin acentos, con / . -"""
    marcas = {"pagada": any(p in crudo for p in PALOMITAS), "anual": "*" in crudo,
              "renovacion": False, "cobranza": False}
    texto = "".join(" " if c in PALOMITAS or c == "*" else c for c in crudo)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9/.\-]+", " ", texto).split()), marcas


def _leer_marcas(resto, marcas, donde):
    for marca in re.sub(r"[/.\-]", " ", resto).split():
        if marca == "r":
            marcas["renovacion"] = True
        elif marca == "c":
            marcas["cobranza"] = True
        elif marca in ("pagada", "pagado"):
            marcas["pagada"] = True
        elif marca != "de":
            raise ValueError(f"Marca no reconocida en {donde}: {marca!r}. Usa ✓, R, C o *.")


def _fecha_existente(anio, mes, dia, donde):
    try:
        return date(anio, mes, dia)
    except ValueError:
        raise ValueError(f"{donde}: el día {dia} no existe en {NOMBRES_MESES[mes - 1]} de {anio}.") from None


_CELDA_MES = re.compile(r"(\d{1,2})\s*[/.-]\s*(0?[1-9]|1[0-2])(?:\s*[/.-]\s*\d{2,4})?(?!\d)|(\d{1,2})(?:\.0)?(?!\d)")


def leer_celda_mes(valor, mes):
    """Lee la celda de un mes: "15", "15 ✓", "15 C", "15 ✓ R *" o "15/03 ✓". Vacía → None."""
    crudo = str(valor or "").strip()
    if not crudo:
        return None
    nombre = NOMBRES_MESES[mes - 1]
    texto, marcas = _separar_marcas(crudo)
    encontrada = _CELDA_MES.match(texto)
    if not encontrada:
        raise ValueError(f"{nombre}: escribe el día del mes con sus marcas, p. ej. «15 ✓»; se leyó {crudo!r}.")
    if encontrada[1]:
        dia, mes_celda = int(encontrada[1]), int(encontrada[2])
        if mes_celda != mes:
            raise ValueError(f"{nombre}: la fecha {crudo!r} es de otro mes.")
    else:
        dia = int(encontrada[3])
    if not 1 <= dia <= 31:
        raise ValueError(f"{nombre}: el día {dia} no es válido.")
    _leer_marcas(texto[encontrada.end():], marcas, f"la columna {nombre}")
    return dia, marcas


def fechas_desde_meses(celdas, anio):
    """Convierte las 12 columnas de meses en (inicio de vigencia, cobros).

    ``celdas``: {número de mes: (día, marcas)}. El mes con * es la renovación
    anual; si solo hay un mes con día (pago anual), ese es la renovación.
    """
    if not celdas:
        raise ValueError("Ningún mes trae día de cobro o renovación.")
    anuales = [mes for mes, (_, marcas) in celdas.items() if marcas["anual"]]
    if len(anuales) > 1:
        raise ValueError("Solo un mes puede llevar * (la fecha anual de renovación).")
    if anuales:
        mes_renovacion = anuales[0]
    elif len(celdas) == 1:
        mes_renovacion = next(iter(celdas))
    else:
        raise ValueError("Marca con * el mes de la renovación anual.")
    if anio is None:
        raise ValueError("Falta el año de renovación.")
    inicio = _fecha_existente(anio, mes_renovacion, celdas[mes_renovacion][0], "Renovación")
    cobros = []
    for mes, (dia, marcas) in celdas.items():
        # Los meses anteriores al de renovación ya son del año siguiente.
        fecha = _fecha_existente(anio if mes >= mes_renovacion else anio + 1, mes, dia, NOMBRES_MESES[mes - 1])
        cobros.append({"fecha_vencimiento": fecha, "estado": "pagado" if marcas["pagada"] else "pendiente"})
    return inicio, sorted(cobros, key=lambda cobro: cobro["fecha_vencimiento"])


def leer_fecha_renovacion(valor, anio):
    """Alternativa a los meses: una sola celda "Fecha de renovación" con sus marcas.

    Ejemplos: "15/03 ✓", "15-mar R", "15 de marzo C *", "2026-03-15".
    Devuelve la fecha completa (con el año de renovación) y las marcas.
    """
    crudo = str(valor or "").strip()
    if not crudo:
        raise ValueError("Falta la fecha de renovación.")
    texto, marcas = _separar_marcas(crudo)
    dia = mes = anio_celda = None
    if (encontrada := _ISO.search(texto)):
        anio_celda, mes, dia = (int(x) for x in encontrada.groups())
    elif (encontrada := _NUMERICA.search(texto)):
        dia, mes = int(encontrada[1]), int(encontrada[2])
        if mes > 12 >= dia:  # exportado como mes/día (formato de EE. UU.)
            dia, mes = mes, dia
        if encontrada[3]:
            anio_celda = int(encontrada[3]) + (2000 if len(encontrada[3]) == 2 else 0)
    else:
        for candidata in _CON_MES.finditer(texto):
            if candidata[2] in MESES:
                encontrada = candidata
                dia, mes = int(candidata[1]), MESES[candidata[2]]
                anio_celda = int(candidata[3]) if candidata[3] else None
                break
    if dia is None:
        raise ValueError(f"No se reconoce la fecha de renovación {crudo!r}; usa día y mes, p. ej. 15/03 o 15-mar.")
    _leer_marcas(texto[:encontrada.start()] + " " + texto[encontrada.end():], marcas, "la fecha de renovación")
    if anio is None:
        anio = anio_celda
    elif anio_celda and anio_celda != anio:
        raise ValueError(f"La fecha {crudo!r} no coincide con el año de renovación {anio}.")
    if anio is None:
        raise ValueError("Falta el año de renovación.")
    try:
        return date(anio, mes, dia), marcas
    except ValueError:
        raise ValueError(f"La fecha de renovación {crudo!r} no existe en {anio}.") from None


def separar_aseguradora(nombre, aseguradora):
    """Separa "Juan Pérez (GNP)" en asegurado y compañía, como aparece a veces en el Excel."""
    nombre, aseguradora = _espacios(nombre), _espacios(aseguradora)
    coincidencia = _ASEGURADORA_ENTRE_PARENTESIS.match(nombre)
    if coincidencia:
        entre_parentesis = _espacios(coincidencia["aseguradora"])
        if not aseguradora or normalizar(aseguradora) == normalizar(entre_parentesis):
            return coincidencia["nombre"].strip(), aseguradora or entre_parentesis
    return nombre, aseguradora


def _referente(nombre_y_cel, cel, correo):
    """"Juan Pérez / 81 1234 5678" → nombre y teléfonos de quien recomendó."""
    texto = str(nombre_y_cel or "")
    telefonos = []
    for numero in _TELEFONO_EN_TEXTO.findall(texto):
        telefonos += _telefonos(numero, "El cel de quien recomendó")
        texto = texto.replace(numero, " ")
    nombre = _espacios(texto).strip(" /-,;")
    telefonos = list(dict.fromkeys(telefonos + _telefonos(cel, "El cel de quien recomendó")))
    correos = _correos(correo, "El correo de quien recomendó")
    if not (nombre or telefonos or correos):
        return None
    return {"nombre": nombre, "rol": "referente", "telefonos": telefonos, "correos": correos}


def limpiar_fila(fila, catalogo, hoy):
    """Convierte una fila del Excel (diccionario por campo) en datos listos para guardar."""
    nombre, compania = separar_aseguradora(fila.get("asegurado"), fila.get("compania"))
    if not nombre:
        raise ValueError("Falta el nombre del asegurado.")
    if not compania:
        raise ValueError("Falta la compañía de seguro.")
    oficial = catalogo.get(normalizar(compania))
    if not oficial:
        raise ValueError(f"Compañía no reconocida: {compania!r}. Agrégala a «aseguradoras» en config.toml.")

    numero = _alfanumerico(fila.get("numero_poliza"), "El número de póliza", "-/")
    anio = _entero(fila["anio"], "El año de renovación", 2000, 2100) if _espacios(fila.get("anio")) else None
    forma_pago = normalizar(fila.get("forma_pago"))
    if forma_pago not in FORMAS_PAGO:
        raise ValueError(f"Forma de pago no válida: {fila.get('forma_pago')!r}. Usa mensual, trimestral, semestral o anual.")
    periodo = FORMAS_PAGO[forma_pago]
    avisos = []
    if any(campo in fila for campo in CAMPOS_MESES):
        celdas = {}
        for mes in range(1, 13):
            celda = leer_celda_mes(fila.get(f"mes_{mes}"), mes)
            if celda:
                celdas[mes] = celda
        inicio, cobros = fechas_desde_meses(celdas, anio)
        con_c = any(marcas["cobranza"] for _, marcas in celdas.values())
        if len(cobros) != 12 // periodo:
            avisos.append(f"la forma de pago {forma_pago} espera {12 // periodo} meses con día y trae {len(cobros)}.")
    else:
        inicio, marcas = leer_fecha_renovacion(fila.get("fecha"), anio)
        con_c = marcas["cobranza"]
        # Sin columnas de meses: el cobro del periodo actual (✓ = pagado) y el siguiente.
        fechas_cobro = [_sumar_meses(inicio, k * periodo) for k in range(12 // periodo)]
        actual = max((f for f in fechas_cobro if f <= hoy), default=fechas_cobro[0])
        cobros = [{"fecha_vencimiento": actual, "estado": "pagado" if marcas["pagada"] else "pendiente"}]
        siguiente = next((f for f in fechas_cobro if f > actual), None)
        if siguiente:
            cobros.append({"fecha_vencimiento": siguiente, "estado": "pendiente"})
    fin = _sumar_meses(inicio, 12)  # la renovación es anual
    estatus = ESTATUS.get(normalizar(fila.get("estatus")).replace("_", " ")) or ESTATUS.get(normalizar(fila.get("estatus")))
    if not estatus:
        raise ValueError(f"Estatus no válido: {fila.get('estatus')!r}. Usa En vigor o Cancelada.")

    descripcion, riesgo = _espacios(fila.get("auto")), _espacios(fila.get("danios"))
    hay_auto = bool(descripcion or _espacios(fila.get("modelo")) or _espacios(fila.get("serie")))
    if hay_auto and riesgo:
        raise ValueError("La fila trae datos de auto y de daños; usa una fila por póliza.")
    if not hay_auto and not riesgo:
        raise ValueError("Falta la descripción del auto (con modelo y serie) o la póliza de daños.")
    vehiculo = None
    if hay_auto:
        if not _espacios(fila.get("serie")):
            raise ValueError("Falta la serie del vehículo; es obligatoria en pólizas de autos.")
        if not descripcion:
            raise ValueError("Falta la descripción del auto.")
        vehiculo = {
            "descripcion": descripcion,
            "modelo": _entero(fila.get("modelo"), "El modelo", 1950, hoy.year + 2),
            "serie": _alfanumerico(fila.get("serie"), "La serie"),
            "inciso": "",
        }

    contactos = []
    telefonos, correos = _telefonos(fila.get("telefono")), _correos(fila.get("correo"))
    if telefonos or correos:
        contactos.append({"nombre": "", "rol": "general", "telefonos": telefonos, "correos": correos})
    referente = _referente(fila.get("recomendo"), fila.get("recomendo_cel"), fila.get("recomendo_correo"))
    if referente:
        contactos.append(referente)

    return {
        "nombre": nombre, "clave": normalizar(nombre), "contactos": contactos, "aseguradora": oficial,
        "numero_poliza": numero, "tipo": "autos" if vehiculo else "danios",
        "fecha_inicio": inicio, "fecha_fin": fin, "forma_pago": forma_pago,
        "descripcion_riesgo": riesgo, "vehiculo": vehiculo, "estatus": estatus,
        "cobros": cobros, "avisos": avisos,
        # Sin C en ningún mes = domiciliada (transcripción, min 1:22:59).
        "tipo_cobro": "manual" if con_c else "domiciliado",
    }


# --------------------------------------------------------------------------- lectura

def mapear_encabezado(columnas):
    """Relaciona cada encabezado del CSV con su campo. Devuelve {campo: posición} y las ignoradas."""
    alias = {normalizar(s): campo for campo, lista in SINONIMOS.items() for s in lista}
    posiciones, ignoradas = {}, []
    for posicion, columna in enumerate(columnas):
        clave = normalizar(columna)
        if not clave:
            continue  # columnas vacías al final del Excel
        campo = alias.get(clave)
        if campo is None:
            ignoradas.append(_espacios(columna))
        elif campo in posiciones:
            raise ValueError(f"La columna «{_espacios(columna)}» está repetida en el encabezado.")
        else:
            posiciones[campo] = posicion
    faltantes = [titulo for campo, titulo in COLUMNAS if campo not in posiciones and campo not in OPCIONALES]
    meses = [campo for campo in CAMPOS_MESES if campo in posiciones]
    if meses:
        faltantes += [nombre for campo, nombre in zip(CAMPOS_MESES, NOMBRES_MESES) if campo not in posiciones]
        if "fecha" in posiciones:
            ignoradas.append("Fecha de renovación (se usan las columnas de los meses)")
            del posiciones["fecha"]
    elif "fecha" not in posiciones:
        faltantes.append("Ene … Dic (o Fecha de renovación)")
    if faltantes:
        raise ValueError(f"Al CSV le faltan columnas: {', '.join(faltantes)}. No se modificó la base.")
    return posiciones, ignoradas


def leer_archivo(ruta):
    """Abre el CSV como lo exporta Excel: UTF-8 o Windows-1252, separado por coma o punto y coma."""
    contenido = Path(ruta).read_bytes()
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            texto = contenido.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    return io.StringIO(texto, newline="")


def _filas_csv(entrada):
    muestra = entrada.read(4096)
    entrada.seek(0)
    try:
        dialecto = csv.Sniffer().sniff(muestra.splitlines()[0] if muestra else ",", delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel
    return csv.reader(entrada, dialecto)


# --------------------------------------------------------------------------- carga

def _agrupar(filas, errores):
    """Junta las filas por número de póliza; varias filas de autos forman una flotilla."""
    polizas = {}
    for linea, limpia in filas:
        clave = limpia["numero_poliza"].casefold()
        grupo = polizas.get(clave)
        if grupo is None:
            polizas[clave] = {"base": limpia, "lineas": [linea], "vehiculos": [limpia["vehiculo"]] if limpia["vehiculo"] else []}
            continue
        base, primera = grupo["base"], grupo["lineas"][0]
        if limpia["tipo"] == "danios" or base["tipo"] == "danios":
            motivo = f"Póliza duplicada: el número {limpia['numero_poliza']} ya aparece en la fila {primera}."
        elif any(limpia[c] != base[c] for c in ("clave", "aseguradora", "fecha_inicio", "forma_pago", "estatus")):
            motivo = (f"La póliza {limpia['numero_poliza']} ya aparece en la fila {primera} con otro asegurado, "
                      "compañía, fecha, forma de pago o estatus.")
        elif any(v["serie"] == limpia["vehiculo"]["serie"] for v in grupo["vehiculos"]):
            motivo = f"Vehículo duplicado: la serie {limpia['vehiculo']['serie']} ya aparece en la póliza de la fila {primera}."
        else:
            grupo["lineas"].append(linea)
            grupo["vehiculos"].append(limpia["vehiculo"])
            continue
        errores.append({"fila": linea, "motivo": motivo})
    return polizas


def _fichas_asegurados(filas):
    """Un asegurado puede tener varias pólizas: se reúnen todos sus teléfonos, correos y referentes."""
    fichas = {}
    for _, limpia in filas:
        ficha = fichas.setdefault(limpia["clave"], {"nombre": limpia["nombre"], "contactos": {}})
        for contacto in limpia["contactos"]:
            llave = (contacto["rol"], normalizar(contacto["nombre"]))
            actual = ficha["contactos"].setdefault(llave, dict(contacto, telefonos=[], correos=[]))
            for medio in ("telefonos", "correos"):
                actual[medio] = list(dict.fromkeys(actual[medio] + contacto[medio]))
    return {clave: (ficha["nombre"], list(ficha["contactos"].values())) for clave, ficha in fichas.items()}


def cargar_csv(entrada, hoy=None):
    """Carga tolerante del Excel exportado a CSV. Devuelve el reporte de la carga."""
    from urdiales_polizas.configuracion import cargar_configuracion
    from urdiales_polizas.persistencia import repositorios

    hoy = hoy or timezone.localdate()
    if repositorios.listar_asegurados() or repositorios.listar_aseguradoras():
        raise ValueError("La base ya contiene datos. La carga inicial solo se hace en una base vacía; no se modificó ningún registro.")
    lector = _filas_csv(entrada)
    encabezado = next(lector, None)
    if not encabezado:
        raise ValueError("El CSV está vacío.")
    posiciones, ignoradas = mapear_encabezado(encabezado)
    catalogo = {normalizar(nombre): nombre for nombre in cargar_configuracion()["aseguradoras"]}

    resultado = {"asegurados": 0, "polizas": 0, "vigencias": 0, "cobros": 0,
                 "validas": 0, "invalidas": 0, "errores": [],
                 "advertencias": [f"Columna ignorada: «{c}»." for c in ignoradas]}
    # Pasada 1: limpieza fila por fila.
    filas = []
    for linea, celdas in enumerate(lector, start=2):
        if not any(c.strip() for c in celdas):
            continue  # renglones vacíos del Excel
        fila = {campo: celdas[i] if i < len(celdas) else "" for campo, i in posiciones.items()}
        try:
            limpia = limpiar_fila(fila, catalogo, hoy)
            filas.append((linea, limpia))
            resultado["advertencias"] += [f"Fila {linea}: {aviso}" for aviso in limpia["avisos"]]
        except (ValueError, TypeError) as error:
            resultado["errores"].append({"fila": linea, "motivo": str(error)})
    if not filas and not resultado["errores"]:
        raise ValueError("El CSV no contiene pólizas.")
    polizas = _agrupar(filas, resultado["errores"])
    fichas = _fichas_asegurados(filas)

    # Pasada 2: guardado por póliza. Las canceladas van primero para que un vehículo
    # que se cambió de póliza no choque con la regla de "una serie, una póliza en vigor".
    asegurados, companias = {}, {}
    for grupo in sorted(polizas.values(), key=lambda g: (g["base"]["estatus"] != "cancelada", g["lineas"][0])):
        base = grupo["base"]
        try:
            with transaction.atomic():
                nuevo_asegurado = base["clave"] not in asegurados
                asegurado_id = asegurados.get(base["clave"]) or repositorios.crear_asegurado(*fichas[base["clave"]])
                nueva_compania = base["aseguradora"] not in companias
                compania_id = companias.get(base["aseguradora"]) or repositorios.crear_aseguradora(base["aseguradora"])
                vehiculos = [dict(v, inciso=str(i) if len(grupo["vehiculos"]) > 1 else "")
                             for i, v in enumerate(grupo["vehiculos"], start=1)]
                vigencia = {
                    "numero_poliza": base["numero_poliza"], "fecha_inicio": base["fecha_inicio"],
                    "fecha_fin": base["fecha_fin"], "forma_pago": base["forma_pago"],
                    "tipo_cobro": base["tipo_cobro"],
                }
                cobros = base["cobros"]
                poliza_id = repositorios.crear_poliza(
                    asegurado_id, compania_id, base["tipo"], vigencia, vehiculos, cobros,
                    modalidad=("flotilla" if len(vehiculos) > 1 else "individual") if vehiculos else None,
                    descripcion_riesgo=base["descripcion_riesgo"] or None,
                )
                if base["estatus"] == "cancelada":
                    repositorios.cancelar_poliza(poliza_id)
        except (ValueError, TypeError, DatabaseError) as error:
            for linea in grupo["lineas"]:
                resultado["errores"].append({"fila": linea, "motivo": str(error)})
            continue
        # La póliza quedó guardada: ahora sí se actualiza el estado.
        if nuevo_asegurado:
            asegurados[base["clave"]] = asegurado_id
            resultado["asegurados"] += 1
        if nueva_compania:
            companias[base["aseguradora"]] = compania_id
        resultado["validas"] += len(grupo["lineas"])
        resultado["polizas"] += 1
        resultado["vigencias"] += 1
        resultado["cobros"] += len(cobros)
    resultado["errores"].sort(key=lambda error: error["fila"])
    resultado["invalidas"] = len(resultado["errores"])
    return resultado


def cargar_demostracion(archivo=None, hoy=None):
    if archivo:
        return cargar_csv(leer_archivo(archivo), hoy=hoy)
    entrada = io.StringIO()
    generar_csv(entrada, hoy=hoy)
    entrada.seek(0)
    return cargar_csv(entrada, hoy=hoy)


# --------------------------------------------------------------------------- ejemplo ficticio

def generar_filas(cantidad=300, hoy=None):
    """Filas ficticias con las mismas columnas y marcas que el Excel del despacho.

    Cada mes con cobro lleva el día; ✓ si ya se pagó; C en cada cobro de las
    pólizas de cobranza manual (las domiciliadas no llevan C); * en el mes de la
    renovación anual y R cuando la renovación vence en 30 días o menos.
    """
    if cantidad < 1:
        raise ValueError("La cantidad debe ser mayor que cero.")
    from urdiales_polizas.configuracion import cargar_configuracion

    hoy = hoy or timezone.localdate()
    companias = cargar_configuracion()["aseguradoras"]
    formas = ("Mensual", "Trimestral", "Semestral", "Anual")
    filas = 0
    poliza = 0
    while filas < cantidad:
        poliza += 1
        cliente = (poliza - 1) % 50 + 1
        tipo = "danios" if poliza % 3 == 0 else "autos"
        fin = hoy + timedelta(days=(poliza * 7) % 90)
        if poliza % 13 == 0:
            fin = hoy - timedelta(days=poliza % 20 + 1)  # no se renovó: año en rojo en el Excel
        inicio = _sumar_meses(fin, -12)
        inicio = inicio.replace(day=min(inicio.day, 28))
        fin = _sumar_meses(inicio, 12)
        forma = formas[poliza % 4]
        periodo = FORMAS_PAGO[forma.lower()]
        celdas = dict.fromkeys(NOMBRES_MESES, "")
        domiciliada = forma == "Mensual" and poliza % 8 == 0  # cargo automático: sin C
        for k in range(12 // periodo):
            fecha = _sumar_meses(inicio, k * periodo)
            # Algunos cobros vencidos quedan sin pagar para que haya cobranza pendiente.
            pagado = fecha <= hoy and not (poliza % 6 == 1 and fecha > hoy - timedelta(days=45))
            marcas = "" if domiciliada else " C"
            if pagado:
                marcas += " ✓"
            if k == 0:
                marcas += " *"
                if 0 <= (fin - hoy).days <= 30:
                    marcas += " R"
            celdas[NOMBRES_MESES[fecha.month - 1]] = f"{fecha.day}{marcas}"
        comun = {
            "Asegurado": f"Cliente Ficticio {cliente:03d}",
            "Compañía de seguro": companias[poliza % len(companias)],
            "Año de renovación": str(inicio.year),
            "Número de póliza": f"DEMO{poliza:04d}A",
            **celdas,
            "Forma de pago": forma,
            "Descripción del auto": "", "Modelo": "", "Póliza de daños": "", "Serie": "",
            "Teléfono": "" if cliente % 5 == 0 else f"81{cliente:08d}",
            "Correo": "" if cliente % 5 == 0 else f"cliente{cliente:03d}@example.com",
            "Recomendó": f"Referente Ficticio {cliente:03d} / 819{cliente:07d}" if cliente % 4 == 0 else "",
            "Cel de quien recomendó": "",
            "Correo de quien recomendó": f"referente{cliente:03d}@example.com" if cliente % 8 == 0 else "",
            "Estatus": "Cancelada" if poliza % 19 == 0 else "En vigor",
        }
        if tipo == "danios":
            yield dict(comun, **{"Póliza de daños": f"Local comercial ficticio {poliza:03d}"})
            filas += 1
            continue
        unidades = 2 if poliza % 9 == 2 else 1  # flotilla: mismo número de póliza, un vehículo por fila
        for unidad in range(1, unidades + 1):
            if filas >= cantidad:
                return
            yield dict(comun, **{
                "Descripción del auto": f"Vehículo ficticio {poliza:03d}-{unidad}",
                "Modelo": str(2018 + poliza % 9),
                "Serie": f"DEMO{poliza:06d}V{unidad}",
            })
            filas += 1


def generar_csv(salida, cantidad=300, hoy=None):
    escritor = csv.DictWriter(salida, fieldnames=ENCABEZADOS, lineterminator="\n")
    escritor.writeheader()
    escritor.writerows(generar_filas(cantidad, hoy))


def indicadores(hoy=None):
    """Tres cifras de la cartera, calculadas con las consultas SQL del repositorio."""
    from urdiales_polizas.persistencia import repositorios

    por_estado = {(f["tipo"], f["estado"]): f["total"] for f in repositorios.indicador_por_estado(hoy)}
    return {
        "Pólizas en vigor": len(repositorios.filas_cartera(estado="en_vigor")),
        "Renovaciones que requieren atención": por_estado.get(("renovacion", "requiere atencion"), 0),
        "Cobros pendientes": por_estado.get(("cobranza", "pendiente"), 0),
    }
