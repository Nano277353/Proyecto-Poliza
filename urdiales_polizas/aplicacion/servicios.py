"""
aplicacion/servicios.py  (paradigma estructurado)

Acciones del sistema que llama la interfaz: registrar, editar, renovar, cancelar y consultar.
- Cada función hace una sola cosa y solo usa secuencia, if y ciclos.
- Si un dato no es válido lanza ValueError con un mensaje claro que la interfaz muestra.
- Todo lo que guarda va en "with repo.transaccion()": si algo falla, no se guarda nada.
- Indicadores (funcional), consultas lógicas (lógico) y carga de archivos (imperativo)
  están en otros módulos.

El repositorio debe tener: transaccion, insertar_asegurado, obtener_asegurado,
actualizar_asegurado, insertar_contacto, listar_contactos, existe_aseguradora,
insertar_poliza, obtener_poliza, actualizar_poliza, cancelar_poliza, insertar_vigencia,
vigencia_actual, archivar_vigencia, insertar_vehiculo, copiar_vehiculos, insertar_cobro,
obtener_cobro, actualizar_cobro, listar_polizas(filtros, historico) y listar_pendientes().
Las filas de listar_polizas traen "fecha_fin" (date o texto AAAA-MM-DD) y listar_pendientes
solo trae pendientes de pólizas En vigor y de su vigencia actual.
"""
import sqlite3
from calendar import monthrange
from datetime import date, datetime, timedelta
from operator import itemgetter

COBROS_POR_FORMA_PAGO = {"anual": 1, "semestral": 2, "trimestral": 4, "mensual": 12}
TIPOS_POLIZA = ["autos", "daños"]
TIPOS_COBRO = ["manual", "domiciliado"]
TIPOS_CONTACTO = ["telefono", "correo"]
CAMPOS_EDITABLES = ["numero_poliza", "aseguradora", "responsable", "sin_periodo_gracia"]

COBRO_POR_COBRAR = "Pendiente de cobrar"        # cobro manual
COBRO_POR_VERIFICAR = "Pendiente de verificar"  # cobro domiciliado
COBRO_PAGADO = "Pagado"
ESTADOS_POR_TIPO_COBRO = {
    "manual": [COBRO_POR_COBRAR, COBRO_PAGADO],
    "domiciliado": [COBRO_POR_VERIFICAR, COBRO_PAGADO],
}

EN_VIGOR = "En vigor"
CANCELADA = "Cancelada"
ARCHIVADA = "Archivada por renovación"
MENSAJE_DUPLICADO = ("No se pudo guardar: ya existe una póliza con ese número en esa "
                     "aseguradora, o algún dato relacionado no es válido.")


# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------
def parsear_fecha(valor):
    """Convierte a fecha. Acepta 2027-01-15 o 15/01/2027; rechaza fechas que no existen."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor

    texto = "" if valor is None else str(valor).strip()
    if texto == "":
        raise ValueError("La fecha es obligatoria.")
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass  # probamos el siguiente formato
    raise ValueError(f"La fecha «{texto}» no existe o no tiene un formato válido.")


def sumar_meses(fecha, meses):
    """Suma meses; si el día no existe en el mes destino (31 en abril), usa el último día."""
    total = fecha.month - 1 + meses
    anio = fecha.year + total // 12
    mes = total % 12 + 1
    return date(anio, mes, min(fecha.day, monthrange(anio, mes)[1]))


def generar_fechas_cobro(inicio, forma_pago):
    """Trimestral desde 15-ene -> 15-ene, 15-abr, 15-jul, 15-oct."""
    cantidad = COBROS_POR_FORMA_PAGO[forma_pago]
    fechas = []
    for numero in range(cantidad):
        fechas.append(sumar_meses(inicio, numero * (12 // cantidad)))
    return fechas


def calcular_fecha_atencion(vencimiento, dias_anticipacion=30, inhabiles=()):
    """30 días antes del vencimiento; si no es día hábil (fin de semana o feriado), se adelanta."""
    fecha = vencimiento - timedelta(days=dias_anticipacion)
    while fecha.weekday() >= 5 or fecha in inhabiles:  # 5 y 6 = sábado y domingo
        fecha = fecha - timedelta(days=1)
    return fecha


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------
def _limpiar(texto):
    return "" if texto is None else str(texto).strip()


def _validar_contacto(tipo, valor):
    tipo = _limpiar(tipo).lower()
    valor = _limpiar(valor)  # siempre texto, para no perder el + ni los ceros
    if tipo not in TIPOS_CONTACTO:
        raise ValueError("El contacto debe ser «telefono» o «correo».")
    if valor == "":
        raise ValueError("El contacto no puede estar vacío.")
    if tipo == "correo" and "@" not in valor:
        raise ValueError(f"El correo «{valor}» no es válido.")
    return tipo, valor


def validar_poliza(datos):
    """Devuelve los datos limpios o lanza ValueError con todos los errores (uno por línea)."""
    errores = []
    poliza = {
        "asegurado_id": datos.get("asegurado_id"),
        "numero_poliza": _limpiar(datos.get("numero_poliza")),  # texto: puede llevar ceros
        "aseguradora": _limpiar(datos.get("aseguradora")),
        "tipo": _limpiar(datos.get("tipo")).lower().replace("danos", "daños"),
        "forma_pago": _limpiar(datos.get("forma_pago")).lower(),
        "tipo_cobro": _limpiar(datos.get("tipo_cobro")).lower(),
        "responsable": _limpiar(datos.get("responsable")) or "Sin asignar",
        "sin_periodo_gracia": bool(datos.get("sin_periodo_gracia", False)),
    }

    if poliza["asegurado_id"] is None:
        errores.append("Elija un asegurado.")
    if poliza["numero_poliza"] == "":
        errores.append("El número de póliza es obligatorio.")
    if poliza["aseguradora"] == "":
        errores.append("La aseguradora es obligatoria.")
    if poliza["tipo"] not in TIPOS_POLIZA:
        errores.append("El tipo debe ser «autos» o «daños».")
    if poliza["forma_pago"] not in COBROS_POR_FORMA_PAGO:
        errores.append("La forma de pago debe ser anual, semestral, trimestral o mensual.")
    if poliza["tipo_cobro"] not in TIPOS_COBRO:
        errores.append("El tipo de cobro debe ser «manual» o «domiciliado».")
    try:
        poliza["fecha_renovacion"] = parsear_fecha(datos.get("fecha_renovacion"))
    except ValueError as error:
        errores.append("Fecha de renovación: " + str(error))

    # Vehículos: cada uno lleva serie (obligatoria) e inciso (si no se da, es su posición)
    vehiculos = []
    series_vistas = []
    for numero, vehiculo in enumerate(datos.get("vehiculos") or [], start=1):
        copia = dict(vehiculo)
        copia["serie"] = _limpiar(vehiculo.get("serie")).upper()
        copia["inciso"] = _limpiar(vehiculo.get("inciso")) or str(numero)
        vehiculos.append(copia)
        if copia["serie"] == "":
            errores.append(f"Vehículo {numero}: el número de serie es obligatorio.")
        elif copia["serie"] in series_vistas:
            errores.append(f"Vehículo {numero}: la serie {copia['serie']} está repetida.")
        series_vistas.append(copia["serie"])
    poliza["vehiculos"] = vehiculos

    if poliza["tipo"] == "autos" and len(vehiculos) == 0:
        errores.append("Una póliza de autos necesita al menos un vehículo.")
    if poliza["tipo"] == "daños" and len(vehiculos) > 0:
        errores.append("Las pólizas de daños no llevan vehículos.")

    if len(errores) > 0:
        raise ValueError("\n".join(errores))
    return poliza


def _limpiar_cambios(cambios):
    """Se queda con los campos editables y revisa que número y aseguradora no queden vacíos."""
    nuevos = {}
    for campo in CAMPOS_EDITABLES:
        if campo in cambios:
            nuevos[campo] = cambios[campo]
    if len(nuevos) == 0:
        raise ValueError("No hay cambios que guardar.")

    for campo in ("numero_poliza", "aseguradora"):
        if campo in nuevos:
            nuevos[campo] = _limpiar(nuevos[campo])
            if nuevos[campo] == "":
                raise ValueError("El número de póliza y la aseguradora no pueden quedar vacíos.")
    if "responsable" in nuevos:
        nuevos["responsable"] = _limpiar(nuevos["responsable"]) or "Sin asignar"
    return nuevos


# ---------------------------------------------------------------------------
# Asegurados y contactos
# ---------------------------------------------------------------------------
def registrar_asegurado(repo, nombre, contactos=()):
    """contactos: lista de pares (tipo, valor), con tipo 'telefono' o 'correo'. Son opcionales."""
    nombre = _limpiar(nombre)
    if nombre == "":
        raise ValueError("El nombre del asegurado es obligatorio.")
    contactos_limpios = []
    for tipo, valor in contactos:
        contactos_limpios.append(_validar_contacto(tipo, valor))

    with repo.transaccion():
        asegurado_id = repo.insertar_asegurado(nombre)
        for tipo, valor in contactos_limpios:
            repo.insertar_contacto(asegurado_id, tipo, valor)
    return asegurado_id


def editar_asegurado(repo, asegurado_id, nombre):
    nombre = _limpiar(nombre)
    if nombre == "":
        raise ValueError("El nombre del asegurado no puede quedar vacío.")

    with repo.transaccion():
        if repo.obtener_asegurado(asegurado_id) is None:
            raise ValueError("El asegurado no existe.")
        repo.actualizar_asegurado(asegurado_id, nombre)


def agregar_contacto(repo, asegurado_id, tipo, valor):
    tipo, valor = _validar_contacto(tipo, valor)

    with repo.transaccion():
        if repo.obtener_asegurado(asegurado_id) is None:
            raise ValueError("El asegurado no existe.")
        return repo.insertar_contacto(asegurado_id, tipo, valor)


def obtener_ficha_asegurado(repo, asegurado_id):
    asegurado = repo.obtener_asegurado(asegurado_id)
    if asegurado is None:
        raise ValueError("El asegurado no existe.")
    return {
        "asegurado": asegurado,
        "contactos": repo.listar_contactos(asegurado_id),
        "polizas": consultar_polizas(repo, {"asegurado_id": asegurado_id}),
    }


# ---------------------------------------------------------------------------
# Pólizas
# ---------------------------------------------------------------------------
def _crear_vigencia(repo, poliza_id, inicio, forma_pago, tipo_cobro):
    """Vigencia de un año con sus cobros. La usan registrar_poliza y renovar_poliza."""
    vigencia_id = repo.insertar_vigencia(poliza_id, inicio, sumar_meses(inicio, 12))
    if tipo_cobro == "domiciliado":
        estado = COBRO_POR_VERIFICAR
    else:
        estado = COBRO_POR_COBRAR
    for fecha in generar_fechas_cobro(inicio, forma_pago):
        repo.insertar_cobro(vigencia_id, fecha, estado)
    return vigencia_id


def registrar_poliza(repo, datos):
    poliza = validar_poliza(datos)

    try:
        with repo.transaccion():
            if repo.obtener_asegurado(poliza["asegurado_id"]) is None:
                raise ValueError("El asegurado indicado no existe.")
            if not repo.existe_aseguradora(poliza["aseguradora"]):
                raise ValueError(f"La aseguradora «{poliza['aseguradora']}» no está registrada.")

            poliza_id = repo.insertar_poliza({
                "asegurado_id": poliza["asegurado_id"],
                "numero_poliza": poliza["numero_poliza"],
                "aseguradora": poliza["aseguradora"],
                "tipo": poliza["tipo"],
                "forma_pago": poliza["forma_pago"],
                "tipo_cobro": poliza["tipo_cobro"],
                "responsable": poliza["responsable"],
                "sin_periodo_gracia": poliza["sin_periodo_gracia"],
                "estatus": EN_VIGOR,
            })
            vigencia_id = _crear_vigencia(repo, poliza_id, poliza["fecha_renovacion"],
                                          poliza["forma_pago"], poliza["tipo_cobro"])
            for vehiculo in poliza["vehiculos"]:
                repo.insertar_vehiculo(vigencia_id, vehiculo)
    except sqlite3.IntegrityError:
        raise ValueError(MENSAJE_DUPLICADO) from None
    return poliza_id


def editar_poliza(repo, poliza_id, cambios):
    """Cambia número, aseguradora, responsable o 'sin periodo de gracia'. Fechas y pagos: renovar."""
    nuevos = _limpiar_cambios(cambios)

    try:
        with repo.transaccion():
            poliza = repo.obtener_poliza(poliza_id)
            if poliza is None:
                raise ValueError("La póliza no existe.")
            if poliza["estatus"] == CANCELADA:
                raise ValueError("Una póliza cancelada no se puede modificar ni reactivar.")
            if "aseguradora" in nuevos and not repo.existe_aseguradora(nuevos["aseguradora"]):
                raise ValueError(f"La aseguradora «{nuevos['aseguradora']}» no está registrada.")
            repo.actualizar_poliza(poliza_id, nuevos)
    except sqlite3.IntegrityError:
        raise ValueError(MENSAJE_DUPLICADO) from None


def renovar_poliza(repo, poliza_id, nuevo_numero=None):
    """La vigencia anterior pasa al histórico y se crea la nueva. Si la aseguradora cambió el número, va en nuevo_numero."""
    try:
        with repo.transaccion():
            poliza = repo.obtener_poliza(poliza_id)
            if poliza is None:
                raise ValueError("La póliza no existe.")
            if poliza["estatus"] == CANCELADA:
                raise ValueError("Una póliza cancelada no se puede renovar.")
            vigente = repo.vigencia_actual(poliza_id)
            if vigente is None:
                raise ValueError("La póliza no tiene una vigencia actual que renovar.")

            repo.archivar_vigencia(vigente["id"], ARCHIVADA)
            inicio = parsear_fecha(vigente["fecha_fin"])  # la nueva empieza donde terminó la anterior
            nueva_id = _crear_vigencia(repo, poliza_id, inicio,
                                       poliza["forma_pago"], poliza["tipo_cobro"])
            repo.copiar_vehiculos(vigente["id"], nueva_id)
            if _limpiar(nuevo_numero) != "":
                repo.actualizar_poliza(poliza_id, {"numero_poliza": _limpiar(nuevo_numero)})
    except sqlite3.IntegrityError:
        raise ValueError(MENSAJE_DUPLICADO) from None
    return nueva_id


def cancelar_poliza(repo, poliza_id):
    """Cancelación administrativa: la póliza se conserva para consulta y ya no se puede reactivar."""
    with repo.transaccion():
        poliza = repo.obtener_poliza(poliza_id)
        if poliza is None:
            raise ValueError("La póliza no existe.")
        if poliza["estatus"] == CANCELADA:
            raise ValueError("La póliza ya estaba cancelada.")
        repo.cancelar_poliza(poliza_id)


# ---------------------------------------------------------------------------
# Cobros
# ---------------------------------------------------------------------------
def marcar_cobro(repo, cobro_id, estado):
    """Manual: por cobrar o pagado. Domiciliado: por verificar o pagado."""
    with repo.transaccion():
        cobro = repo.obtener_cobro(cobro_id)
        if cobro is None:
            raise ValueError("El cobro no existe.")
        if cobro["poliza_estatus"] == CANCELADA:
            raise ValueError("La póliza de este cobro está cancelada.")
        permitidos = ESTADOS_POR_TIPO_COBRO[cobro["tipo_cobro"]]
        if estado not in permitidos:
            raise ValueError("Para este cobro el estado debe ser: " + " o ".join(permitidos) + ".")
        repo.actualizar_cobro(cobro_id, estado)


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def consultar_polizas(repo, filtros=None, historico=False):
    """
    Filtros opcionales y combinables: asegurado, aseguradora, tipo, tipo_pendiente, estatus,
    serie (últimos dígitos), fecha_desde y fecha_hasta. Primero las que vencen más pronto.
    """
    usados = {}
    for campo, valor in (filtros or {}).items():
        if valor not in (None, ""):
            usados[campo] = valor
    for campo in ("fecha_desde", "fecha_hasta"):
        if campo in usados:
            usados[campo] = parsear_fecha(usados[campo])
    if "fecha_desde" in usados and "fecha_hasta" in usados and usados["fecha_desde"] > usados["fecha_hasta"]:
        raise ValueError("La fecha inicial no puede ser posterior a la final.")

    return sorted(repo.listar_polizas(usados, historico), key=itemgetter("fecha_fin"))


def requiere_atencion(repo, hoy, dias_anticipacion=30, inhabiles=()):
    """
    Renovaciones y cobros que ya hay que atender, del más próximo al más lejano.
    dias_anticipacion e inhabiles (fechas tipo date) salen de config.toml; los pasa quien llama.
    """
    hoy = parsear_fecha(hoy)
    casos = []
    for pendiente in repo.listar_pendientes():
        vencimiento = parsear_fecha(pendiente["fecha_vencimiento"])
        fecha_atencion = calcular_fecha_atencion(vencimiento, dias_anticipacion, inhabiles)
        if hoy >= fecha_atencion:
            caso = dict(pendiente)
            caso["fecha_vencimiento"] = vencimiento
            caso["fecha_atencion"] = fecha_atencion
            caso["dias_restantes"] = (vencimiento - hoy).days  # negativo = ya venció
            casos.append(caso)
    return sorted(casos, key=itemgetter("fecha_vencimiento"))