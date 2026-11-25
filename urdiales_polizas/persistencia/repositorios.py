"""Persistencia con SQL directo y parametrizado (paradigma declarativo).

Las tablas, restricciones y triggers están en esquema.sql. Este módulo:

* guarda datos con INSERT/UPDATE parametrizados (%s), nunca concatenando texto;
* reconstruye los objetos del dominio (Asegurado, PolizaAuto, PolizaDanios,
  Vigencia, Cobro...) al leer, de modo que el dominio vuelve a validar lo
  guardado; si no lo acepta, la transacción se revierte;
* ofrece las consultas de cartera, búsqueda e indicadores (JOIN y GROUP BY)
  que usan las capas de consultas, reglas y servicios.

Los errores de la base se convierten en ValueError con mensajes comprensibles.
"""
from datetime import date

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from urdiales_polizas.dominio.clientes import ROLES_CONTACTO, Asegurado, Contacto, MedioContacto
from urdiales_polizas.dominio.cobros import Cobro
from urdiales_polizas.dominio.polizas import (
    Aseguradora, EstadoPoliza, FormaPago, ModalidadAuto, PolizaAuto, PolizaDanios,
    VehiculoAsegurado, Vigencia,
)

TIPOS_POLIZA = ('autos', 'danios')
TIPOS_COBRO = ('manual', 'domiciliado')
_BOOLEANOS = ('archivada', 'sin_gracia', 'aplica_anticipacion')
_FECHAS = ('fecha_inicio', 'fecha_fin', 'fecha_vencimiento', 'proximo_cobro')
_MENSAJES_UNICOS = {
    'aseguradoras.nombre': 'Esa aseguradora ya está registrada.',
    'vehiculos_asegurados.vigencia_id, vehiculos_asegurados.serie': 'Una serie se repite en la misma vigencia.',
    'cobros.vigencia_id, cobros.fecha_vencimiento': 'No se puede repetir una fecha de cobro en la misma vigencia.',
    'vigencias.poliza_id': 'La póliza ya tiene una vigencia actual; registra una renovación.',
    'medios_contacto.contacto_id, medios_contacto.tipo, medios_contacto.valor': 'Un contacto repite el mismo teléfono o correo.',
}


# --------------------------------------------------------------------------- utilidades

def _traducir(error):
    """Convierte un IntegrityError de SQLite en un mensaje para la persona usuaria."""
    mensaje = str(error)
    if mensaje.startswith('UNIQUE constraint failed: '):
        return ValueError(_MENSAJES_UNICOS.get(mensaje.split(': ', 1)[1], 'Ya existe un registro con esos datos.'))
    if mensaje.startswith('FOREIGN KEY'):
        return ValueError('El asegurado, la aseguradora o la póliza indicada no existe.')
    if mensaje.startswith(('CHECK constraint', 'NOT NULL')):
        return ValueError('Hay un dato con formato no válido; revisa fechas, tipo de póliza y campos obligatorios.')
    return ValueError(mensaje)  # Mensajes de los triggers (RAISE ABORT), ya escritos en español.


def _ejecutar(sql, parametros=()):
    """Ejecuta un INSERT/UPDATE/DELETE y devuelve el id insertado."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, list(parametros))
            return cursor.lastrowid
    except IntegrityError as error:
        raise _traducir(error) from error


def _filas(sql, parametros=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, list(parametros))
        columnas = [columna[0] for columna in cursor.description]
        registros = [dict(zip(columnas, fila)) for fila in cursor.fetchall()]
    for registro in registros:
        for campo in _FECHAS:
            if registro.get(campo) is not None:
                registro[campo] = date.fromisoformat(registro[campo])
        for campo in _BOOLEANOS:
            if campo in registro:
                registro[campo] = bool(registro[campo])
    return registros


def _uno(sql, parametros, mensaje):
    filas = _filas(sql, parametros)
    if not filas:
        raise ValueError(mensaje)
    return filas[0]


def _id(valor, etiqueta='registro'):
    if isinstance(valor, bool):
        raise ValueError(f'El id de {etiqueta} no es válido.')
    try:
        valor = int(valor)
    except (TypeError, ValueError):
        raise ValueError(f'El id de {etiqueta} no es válido.') from None
    if valor <= 0:
        raise ValueError(f'El id de {etiqueta} no es válido.')
    return valor


def _fecha(valor, etiqueta):
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor))
    except ValueError:
        raise ValueError(f'{etiqueta} debe ser una fecha válida.') from None


def _hoy(hoy):
    return hoy or timezone.localdate()


def _anticipacion(anticipacion):
    if anticipacion is None:
        from urdiales_polizas.configuracion import cargar_configuracion
        anticipacion = cargar_configuracion()['anticipacion_dias']
    return anticipacion


def _espacios(texto):
    return ' '.join(str(texto or '').split())


# --------------------------------------------------------------------------- asegurados

def _validar_contactos(contactos):
    """Valida con el dominio cada contacto: {"nombre", "rol", "telefonos", "correos"}."""
    limpios = []
    for contacto in contactos:
        rol = contacto.get('rol', 'general')
        if rol not in ROLES_CONTACTO:
            raise ValueError("El rol debe ser 'general' o 'referente'.")
        medios = []
        for campo, tipo in (('telefonos', 'telefono'), ('correos', 'correo')):
            for valor in contacto.get(campo, ()):
                valor = _espacios(valor).lower() if tipo == 'correo' else _espacios(valor)
                medio = MedioContacto(tipo, valor)  # el dominio valida el formato
                if medio not in medios:
                    medios.append(medio)
        nombre = _espacios(contacto.get('nombre')) or None
        if nombre or medios:
            limpios.append((nombre, rol, medios))
    return limpios


def _guardar_contactos(asegurado_id, contactos):
    for nombre, rol, medios in _validar_contactos(contactos):
        contacto_id = _ejecutar('INSERT INTO contactos (asegurado_id, nombre, rol) VALUES (%s, %s, %s)',
                                [asegurado_id, nombre, rol])
        for medio in medios:
            _ejecutar('INSERT INTO medios_contacto (contacto_id, tipo, valor) VALUES (%s, %s, %s)',
                      [contacto_id, medio.tipo, medio.valor])


@transaction.atomic
def crear_asegurado(nombre, contactos=()):
    """Registra un asegurado con sus contactos opcionales y devuelve su id."""
    nombre = _espacios(nombre)
    if not nombre:
        raise ValueError('El nombre del asegurado es obligatorio.')
    asegurado_id = _ejecutar('INSERT INTO asegurados (nombre) VALUES (%s)', [nombre])
    _guardar_contactos(asegurado_id, contactos)
    return asegurado_id


@transaction.atomic
def actualizar_asegurado(asegurado_id, nombre, contactos=()):
    """Cambia el nombre y reemplaza los contactos (el dominio valida el nombre)."""
    asegurado = obtener_asegurado(asegurado_id)
    asegurado.cambiar_nombre(_espacios(nombre))
    _ejecutar('UPDATE asegurados SET nombre = %s WHERE id = %s', [asegurado.nombre, asegurado.id])
    _ejecutar('DELETE FROM contactos WHERE asegurado_id = %s', [asegurado.id])
    _guardar_contactos(asegurado.id, contactos)


def obtener_asegurado(asegurado_id):
    """Devuelve el objeto Asegurado del dominio con sus contactos y medios."""
    fila = _uno('SELECT id, nombre FROM asegurados WHERE id = %s', [_id(asegurado_id, 'asegurado')],
                'No se encontró el asegurado.')
    contactos = []
    for contacto in _filas('SELECT id, nombre, rol FROM contactos WHERE asegurado_id = %s ORDER BY id', [fila['id']]):
        medios = [MedioContacto(m['tipo'], m['valor']) for m in _filas(
            'SELECT tipo, valor FROM medios_contacto WHERE contacto_id = %s ORDER BY id', [contacto['id']])]
        contactos.append(Contacto(contacto['id'], contacto['nombre'], contacto['rol'], medios))
    return Asegurado(fila['id'], fila['nombre'], contactos)


def listar_asegurados(busqueda=''):
    """Asegurados con cuántas pólizas tienen (LEFT JOIN + GROUP BY)."""
    texto = _espacios(busqueda)
    return _filas('''
        SELECT a.id, a.nombre,
               COUNT(p.id) AS total_polizas,
               COALESCE(SUM(p.estado = 'en_vigor'), 0) AS polizas_en_vigor
        FROM asegurados a
        LEFT JOIN polizas p ON p.asegurado_id = a.id
        WHERE %s = '' OR a.nombre LIKE '%%' || %s || '%%'
        GROUP BY a.id, a.nombre
        ORDER BY a.nombre COLLATE NOCASE, a.id
    ''', [texto, texto])


# --------------------------------------------------------------------------- aseguradoras

def crear_aseguradora(nombre):
    nombre = _espacios(nombre)
    if not nombre:
        raise ValueError('El nombre de la aseguradora es obligatorio.')
    return _ejecutar('INSERT INTO aseguradoras (nombre) VALUES (%s)', [nombre])


def obtener_o_crear_aseguradora(nombre):
    nombre = _espacios(nombre)
    existentes = _filas('SELECT id FROM aseguradoras WHERE nombre = %s', [nombre])  # COLLATE NOCASE
    return existentes[0]['id'] if existentes else crear_aseguradora(nombre)


def listar_aseguradoras():
    return [Aseguradora(f['id'], f['nombre'])
            for f in _filas('SELECT id, nombre FROM aseguradoras ORDER BY nombre')]


# --------------------------------------------------------------------------- pólizas

def _vehiculo(datos):
    """Acepta un VehiculoAsegurado o un diccionario; la serie se guarda en mayúsculas."""
    if isinstance(datos, VehiculoAsegurado):
        datos = datos.__dict__
    modelo = datos.get('modelo')
    try:
        modelo = int(modelo)
    except (TypeError, ValueError):
        raise ValueError('El modelo del vehículo debe ser un año entero.') from None
    return VehiculoAsegurado(_espacios(datos.get('descripcion')), modelo,
                             _espacios(datos.get('serie')).upper().replace(' ', ''),
                             _espacios(datos.get('inciso')))


def _cobro(datos):
    if isinstance(datos, Cobro):
        return datos
    return Cobro(_fecha(datos.get('fecha_vencimiento'), 'La fecha del cobro'), datos.get('estado', 'pendiente'))


def _insertar_vigencia(poliza_id, vigencia, vehiculos, cobros, renueva_a_id=None):
    tipo_cobro = vigencia.get('tipo_cobro') or 'manual'
    if tipo_cobro not in TIPOS_COBRO:
        raise ValueError('El tipo de cobro debe ser manual o domiciliado.')
    try:
        forma_pago = FormaPago(vigencia.get('forma_pago')).value
    except ValueError:
        raise ValueError('Selecciona una forma de pago válida.') from None
    inicio = _fecha(vigencia.get('fecha_inicio'), 'La fecha de inicio')
    fin = _fecha(vigencia.get('fecha_fin'), 'La fecha de fin')
    if fin <= inicio:
        raise ValueError('La fecha de fin debe ser posterior a la de inicio.')
    vigencia_id = _ejecutar('''
        INSERT INTO vigencias (poliza_id, numero_poliza, fecha_inicio, fecha_fin, forma_pago,
                               tipo_cobro, sin_gracia, renueva_a_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ''', [poliza_id, _espacios(vigencia.get('numero_poliza')).upper(), inicio.isoformat(), fin.isoformat(),
          forma_pago, tipo_cobro, int(bool(vigencia.get('sin_gracia'))), renueva_a_id])
    for vehiculo in vehiculos:
        _ejecutar('''INSERT INTO vehiculos_asegurados (vigencia_id, descripcion, modelo, serie, inciso)
                     VALUES (%s, %s, %s, %s, %s)''',
                  [vigencia_id, vehiculo.descripcion, vehiculo.modelo, vehiculo.serie, vehiculo.inciso])
    for cobro in cobros:
        _ejecutar('INSERT INTO cobros (vigencia_id, fecha_vencimiento, estado) VALUES (%s, %s, %s)',
                  [vigencia_id, cobro.fecha_vencimiento.isoformat(), cobro.estado])
    return vigencia_id


@transaction.atomic
def crear_poliza(asegurado_id, aseguradora_id, tipo, vigencia, vehiculos=(), cobros=(),
                 modalidad=None, descripcion_riesgo=None, responsable='', aplica_anticipacion=True):
    """Registra una póliza con su primera vigencia, vehículos y cobros; devuelve su id.

    ``vigencia``: {"numero_poliza", "fecha_inicio", "fecha_fin", "forma_pago",
    "tipo_cobro" (opcional), "sin_gracia" (opcional)}.
    Al final se reconstruye con el dominio (PolizaAuto o PolizaDanios); si sus
    reglas no se cumplen, nada queda guardado.
    """
    if tipo not in TIPOS_POLIZA:
        raise ValueError('El tipo de póliza debe ser autos o daños.')
    vehiculos = [_vehiculo(v) for v in vehiculos]
    cobros = [_cobro(c) for c in cobros]
    if tipo == 'autos':
        try:
            modalidad = ModalidadAuto(modalidad).value
        except ValueError:
            raise ValueError('La modalidad debe ser individual o flotilla.') from None
        descripcion_riesgo = None
    else:
        if modalidad:
            raise ValueError('Las pólizas de daños no tienen modalidad de autos.')
        modalidad, descripcion_riesgo = None, _espacios(descripcion_riesgo)
        if not descripcion_riesgo:
            raise ValueError('La descripción del riesgo es obligatoria.')
    poliza_id = _ejecutar('''
        INSERT INTO polizas (asegurado_id, aseguradora_id, tipo, modalidad_auto, descripcion_riesgo,
                             aplica_anticipacion, responsable)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', [_id(asegurado_id, 'asegurado'), _id(aseguradora_id, 'aseguradora'), tipo, modalidad,
          descripcion_riesgo, int(bool(aplica_anticipacion)), _espacios(responsable)])
    _insertar_vigencia(poliza_id, vigencia, vehiculos, cobros)
    obtener_poliza(poliza_id)  # el dominio valida; un ValueError revierte todo
    return poliza_id


def _vigencia(fila):
    vehiculos = tuple(VehiculoAsegurado(v['descripcion'], v['modelo'], v['serie'], v['inciso']) for v in _filas(
        'SELECT descripcion, modelo, serie, inciso FROM vehiculos_asegurados WHERE vigencia_id = %s ORDER BY id',
        [fila['id']]))
    cobros = tuple(Cobro(c['fecha_vencimiento'], c['estado']) for c in _filas(
        'SELECT fecha_vencimiento, estado FROM cobros WHERE vigencia_id = %s ORDER BY fecha_vencimiento',
        [fila['id']]))
    return Vigencia(fila['id'], fila['numero_poliza'], fila['fecha_inicio'], fila['fecha_fin'],
                    fila['forma_pago'], fila['archivada'], vehiculos, cobros)


def obtener_poliza(poliza_id):
    """Devuelve PolizaAuto o PolizaDanios con todas sus vigencias (la actual al final)."""
    fila = _uno('SELECT * FROM polizas WHERE id = %s', [_id(poliza_id, 'póliza')], 'No se encontró la póliza.')
    vigencias = [_vigencia(v) for v in _filas(
        'SELECT * FROM vigencias WHERE poliza_id = %s ORDER BY archivada DESC, fecha_inicio, id', [fila['id']])]
    if fila['tipo'] == 'autos':
        poliza = PolizaAuto(fila['id'], fila['asegurado_id'], fila['aseguradora_id'], fila['modalidad_auto'], vigencias)
    else:
        poliza = PolizaDanios(fila['id'], fila['asegurado_id'], fila['aseguradora_id'], fila['descripcion_riesgo'], vigencias)
    if fila['estado'] == EstadoPoliza.CANCELADA.value:
        poliza.cancelar()
    return poliza


def datos_poliza(poliza_id):
    """Datos de la póliza para pantallas: nombres y campos que el dominio no maneja."""
    return _uno('''
        SELECT p.*, a.nombre AS asegurado, c.nombre AS aseguradora
        FROM polizas p
        JOIN asegurados a   ON a.id = p.asegurado_id
        JOIN aseguradoras c ON c.id = p.aseguradora_id
        WHERE p.id = %s
    ''', [_id(poliza_id, 'póliza')], 'No se encontró la póliza.')


@transaction.atomic
def actualizar_datos_poliza(poliza_id, responsable=None, aplica_anticipacion=None):
    actual = datos_poliza(poliza_id)
    _ejecutar('UPDATE polizas SET responsable = %s, aplica_anticipacion = %s WHERE id = %s', [
        _espacios(actual['responsable'] if responsable is None else responsable),
        int(bool(actual['aplica_anticipacion'] if aplica_anticipacion is None else aplica_anticipacion)),
        actual['id'],
    ])


@transaction.atomic
def renovar_poliza(poliza_id, vigencia, vehiculos=(), cobros=()):
    """Archiva la vigencia actual y registra la nueva (histórico). Devuelve el id de la nueva.

    Usa ``Poliza.agregar_vigencia`` del dominio para validar la renovación.
    """
    poliza = obtener_poliza(poliza_id)
    if poliza.estado == EstadoPoliza.CANCELADA:
        raise ValueError('No se puede agregar una vigencia a una póliza cancelada.')
    anterior = poliza.vigencias[-1]
    inicio = _fecha(vigencia.get('fecha_inicio'), 'La fecha de inicio')
    if inicio < anterior.fecha_fin:
        raise ValueError('La renovación debe iniciar cuando termina la vigencia anterior o después.')
    _ejecutar('UPDATE vigencias SET archivada = 1 WHERE id = %s', [anterior.id])
    nueva_id = _insertar_vigencia(poliza.id, vigencia, [_vehiculo(v) for v in vehiculos],
                                  [_cobro(c) for c in cobros], renueva_a_id=anterior.id)
    nueva = _vigencia(_uno('SELECT * FROM vigencias WHERE id = %s', [nueva_id], 'No se guardó la vigencia.'))
    poliza.agregar_vigencia(nueva)  # el dominio valida el riesgo; un ValueError revierte todo
    return nueva_id


@transaction.atomic
def cancelar_poliza(poliza_id):
    poliza = obtener_poliza(poliza_id)
    poliza.cancelar()  # regla del dominio
    _ejecutar('UPDATE polizas SET estado = %s WHERE id = %s', [poliza.estado.value, poliza.id])


# --------------------------------------------------------------------------- cobros

def listar_cobros(vigencia_id):
    return _filas('SELECT id, vigencia_id, fecha_vencimiento, estado FROM cobros WHERE vigencia_id = %s '
                  'ORDER BY fecha_vencimiento', [_id(vigencia_id, 'vigencia')])


def agregar_cobro(vigencia_id, fecha_vencimiento, estado='pendiente'):
    cobro = _cobro({'fecha_vencimiento': fecha_vencimiento, 'estado': estado})
    return _ejecutar('INSERT INTO cobros (vigencia_id, fecha_vencimiento, estado) VALUES (%s, %s, %s)',
                     [_id(vigencia_id, 'vigencia'), cobro.fecha_vencimiento.isoformat(), cobro.estado])


def registrar_pago(cobro_id):
    """Marca un cobro como pagado (captura manual; no se procesa ningún pago)."""
    fila = _uno('SELECT fecha_vencimiento, estado FROM cobros WHERE id = %s', [_id(cobro_id, 'cobro')],
                'No se encontró el cobro.')
    cobro = Cobro(fila['fecha_vencimiento'], fila['estado'])
    cobro.marcar_pagado()
    _ejecutar('UPDATE cobros SET estado = %s WHERE id = %s', [cobro.estado, cobro_id])


# --------------------------------------------------------------------------- consultas

def filas_cartera(asegurado_id=None, aseguradora_id=None, tipo=None, estado=None, historico=False):
    """Cartera con filtros opcionales y combinables (JOIN de 4 tablas).

    Por omisión solo trae la vigencia actual de cada póliza; ``historico=True``
    trae también las archivadas. ``proximo_cobro`` es el cobro pendiente más
    próximo, que usa reglas/atencion.py para el aviso de cobranza.
    """
    condiciones, parametros = [], []
    if not historico:
        condiciones.append('v.archivada = 0')
    for columna, valor, permitidos, mensaje in (
        ('p.asegurado_id', asegurado_id, None, ''),
        ('p.aseguradora_id', aseguradora_id, None, ''),
        ('p.tipo', tipo, TIPOS_POLIZA, 'El tipo de póliza debe ser autos o daños.'),
        ('p.estado', estado, tuple(e.value for e in EstadoPoliza), 'El estatus debe ser en vigor o cancelada.'),
    ):
        if valor in (None, ''):
            continue
        if permitidos is None:
            valor = _id(valor)
        elif valor not in permitidos:
            raise ValueError(mensaje)
        condiciones.append(f'{columna} = %s')  # solo nombres de columna fijos; el valor va parametrizado
        parametros.append(valor)
    donde = ('WHERE ' + ' AND '.join(condiciones)) if condiciones else ''
    return _filas(f'''
        SELECT p.id AS poliza_id, v.id AS vigencia_id, v.numero_poliza, v.fecha_inicio, v.fecha_fin,
               v.forma_pago, v.tipo_cobro, v.sin_gracia, v.archivada,
               p.tipo, p.modalidad_auto, p.descripcion_riesgo, p.estado, p.aplica_anticipacion,
               p.responsable,
               a.id AS asegurado_id, a.nombre AS asegurado, c.id AS aseguradora_id, c.nombre AS aseguradora,
               (SELECT group_concat(ve.serie, ', ') FROM vehiculos_asegurados ve
                WHERE ve.vigencia_id = v.id) AS series,
               (SELECT MIN(cb.fecha_vencimiento) FROM cobros cb
                WHERE cb.vigencia_id = v.id AND cb.estado = 'pendiente') AS proximo_cobro
        FROM vigencias v
        JOIN polizas p      ON p.id = v.poliza_id
        JOIN asegurados a   ON a.id = p.asegurado_id
        JOIN aseguradoras c ON c.id = p.aseguradora_id
        {donde}
        ORDER BY v.fecha_fin, v.numero_poliza
    ''', parametros)


def historico_vigencias(poliza_id):
    """Todas las vigencias de una póliza, de la más reciente a la más antigua."""
    return _filas('''
        SELECT v.id, v.numero_poliza, v.fecha_inicio, v.fecha_fin, v.forma_pago, v.archivada,
               COUNT(cb.id) AS cobros, COALESCE(SUM(cb.estado = 'pagado'), 0) AS cobros_pagados
        FROM vigencias v
        LEFT JOIN cobros cb ON cb.vigencia_id = v.id
        WHERE v.poliza_id = %s
        GROUP BY v.id
        ORDER BY v.fecha_inicio DESC, v.id DESC
    ''', [_id(poliza_id, 'póliza')])


def buscar_por_serie(ultimos_digitos):
    """Vehículos de vigencias actuales cuya serie termina con los dígitos dados (mínimo 3)."""
    digitos = _espacios(ultimos_digitos).upper().replace(' ', '')
    if len(digitos) < 3:
        raise ValueError('Escribe al menos los últimos 3 caracteres de la serie.')
    return _filas('''
        SELECT ve.serie, ve.descripcion, ve.modelo, ve.inciso,
               v.numero_poliza, v.fecha_fin, p.id AS poliza_id, p.estado,
               a.nombre AS asegurado, c.nombre AS aseguradora
        FROM vehiculos_asegurados ve
        JOIN vigencias v    ON v.id = ve.vigencia_id AND v.archivada = 0
        JOIN polizas p      ON p.id = v.poliza_id
        JOIN asegurados a   ON a.id = p.asegurado_id
        JOIN aseguradoras c ON c.id = p.aseguradora_id
        WHERE substr(upper(ve.serie), -length(%s)) = %s
        ORDER BY p.estado DESC, v.fecha_fin
    ''', [digitos, digitos])


def polizas_por_aseguradora():
    """Pólizas en vigor por compañía y tipo (LEFT JOIN + GROUP BY)."""
    return _filas('''
        SELECT c.nombre AS aseguradora,
               COALESCE(SUM(p.tipo = 'autos'  AND p.estado = 'en_vigor'), 0) AS autos,
               COALESCE(SUM(p.tipo = 'danios' AND p.estado = 'en_vigor'), 0) AS danios,
               COALESCE(SUM(p.estado = 'en_vigor'), 0) AS total
        FROM aseguradoras c
        LEFT JOIN polizas p ON p.aseguradora_id = c.id
        GROUP BY c.id, c.nombre
        ORDER BY total DESC, c.nombre
    ''')


# Indicadores: los datos se agregan en SQL (JOIN + GROUP BY).

def indicador_por_vencer(hoy=None):
    """Pólizas en vigor que vencen en 0-30, 31-60 y 61-90 días."""
    hoy = _hoy(hoy).isoformat()
    return _filas('''
        SELECT CASE
                 WHEN julianday(v.fecha_fin) - julianday(%s) <= 30 THEN '0-30 días'
                 WHEN julianday(v.fecha_fin) - julianday(%s) <= 60 THEN '31-60 días'
                 ELSE '61-90 días'
               END AS rango,
               COUNT(*) AS total
        FROM vigencias v
        JOIN polizas p ON p.id = v.poliza_id
        WHERE p.estado = 'en_vigor' AND v.archivada = 0
          AND julianday(v.fecha_fin) - julianday(%s) BETWEEN 0 AND 90
        GROUP BY 1
        ORDER BY 1
    ''', [hoy, hoy, hoy])


def indicador_por_estado(hoy=None, anticipacion=None):
    """Renovaciones (vencida / requiere atención / en tiempo) y cobros por estado."""
    hoy, anticipacion = _hoy(hoy).isoformat(), _anticipacion(anticipacion)
    # GROUP BY 1, 2 por posición: "tipo" y "estado" también son columnas de polizas.
    return _filas('''
        SELECT 'renovacion' AS tipo,
               CASE
                 WHEN julianday(v.fecha_fin) < julianday(%s) THEN 'vencida'
                 WHEN julianday(v.fecha_fin) - julianday(%s) <= %s THEN 'requiere atencion'
                 ELSE 'en tiempo'
               END AS estado,
               COUNT(*) AS total
        FROM vigencias v
        JOIN polizas p ON p.id = v.poliza_id
        WHERE p.estado = 'en_vigor' AND v.archivada = 0
        GROUP BY 1, 2
        UNION ALL
        SELECT 'cobranza' AS tipo, cb.estado, COUNT(*) AS total
        FROM cobros cb
        JOIN vigencias v ON v.id = cb.vigencia_id
        JOIN polizas p   ON p.id = v.poliza_id
        WHERE p.estado = 'en_vigor' AND v.archivada = 0
        GROUP BY 1, 2
        ORDER BY 1, 2
    ''', [hoy, hoy, anticipacion])


def indicador_por_responsable(hoy=None, anticipacion=None):
    """Pendientes por responsable: renovaciones dentro del plazo y cobros pendientes
    vencidos o dentro del plazo (sin periodo de gracia)."""
    hoy, anticipacion = _hoy(hoy).isoformat(), _anticipacion(anticipacion)
    return _filas('''
        SELECT CASE WHEN trim(pendientes.responsable) = '' THEN 'Sin asignar'
                    ELSE pendientes.responsable END AS responsable,
               SUM(pendientes.tipo = 'renovacion') AS renovaciones,
               SUM(pendientes.tipo = 'cobranza') AS cobranzas,
               COUNT(*) AS total
        FROM (
            SELECT p.responsable, 'renovacion' AS tipo
            FROM vigencias v JOIN polizas p ON p.id = v.poliza_id
            WHERE p.estado = 'en_vigor' AND v.archivada = 0
              AND julianday(v.fecha_fin) - julianday(%s) BETWEEN 0 AND %s
            UNION ALL
            SELECT p.responsable, 'cobranza' AS tipo
            FROM cobros cb
            JOIN vigencias v ON v.id = cb.vigencia_id
            JOIN polizas p   ON p.id = v.poliza_id
            WHERE p.estado = 'en_vigor' AND v.archivada = 0 AND cb.estado = 'pendiente'
              AND julianday(cb.fecha_vencimiento) - julianday(%s) <= %s
        ) AS pendientes
        GROUP BY 1
        ORDER BY total DESC, responsable
    ''', [hoy, anticipacion, hoy, anticipacion])
