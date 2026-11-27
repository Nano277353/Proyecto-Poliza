"""Casos de uso de las pantallas, apoyados en el repositorio SQLite del proyecto."""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from urdiales_polizas.configuracion import cargar_configuracion
from urdiales_polizas.persistencia import repositorios as repo


def listar_asegurados(busqueda=''):
    return repo.listar_asegurados(busqueda)


def listar_aseguradoras():
    return [{'id': aseguradora.id, 'nombre': aseguradora.nombre}
            for aseguradora in repo.listar_aseguradoras()]


def obtener_asegurado(asegurado_id):
    asegurado = repo.obtener_asegurado(asegurado_id)
    return {
        'id': asegurado.id, 'nombre': asegurado.nombre,
        'contactos': [
            {'nombre': contacto.nombre, 'rol': contacto.rol,
             'telefonos': [m.valor for m in contacto.medios if m.tipo == 'telefono'],
             'correos': [m.valor for m in contacto.medios if m.tipo == 'correo']}
            for contacto in asegurado.contactos
        ],
    }


def guardar_asegurado(nombre, contactos, asegurado_id=None):
    if asegurado_id is None:
        return repo.crear_asegurado(nombre, contactos)
    repo.actualizar_asegurado(asegurado_id, nombre, contactos)
    return asegurado_id


def guardar_aseguradora(nombre):
    return repo.crear_aseguradora(nombre)


def obtener_poliza(poliza_id):
    datos = repo.datos_poliza(poliza_id)
    poliza = repo.obtener_poliza(poliza_id)
    vigencias = []
    for periodo in poliza.vigencias:
        vigencia = repo.datos_vigencia(periodo.id)
        vigencia['vehiculos'] = repo.listar_vehiculos(periodo.id)
        vigencia['cobros'] = repo.listar_cobros(periodo.id)
        vigencia['descripcion_riesgo'] = datos['descripcion_riesgo']
        vigencias.append(vigencia)
    return {**datos, 'vigencias': vigencias}


def _vigencia(datos):
    return {campo: datos[campo] for campo in (
        'numero_poliza', 'fecha_inicio', 'fecha_fin', 'forma_pago', 'tipo_cobro', 'sin_gracia')}


def guardar_poliza(datos, vehiculos, cobros, poliza_id=None, vigencia_id=None):
    """Crea una póliza o corrige su vigencia actual en una sola transacción."""
    if poliza_id is None:
        return repo.crear_poliza(
            datos['asegurado_id'], datos['aseguradora_id'], datos['tipo'], _vigencia(datos),
            vehiculos, cobros, modalidad=datos['modalidad_auto'] or None,
            descripcion_riesgo=datos['descripcion_riesgo'] or None,
            responsable=datos['responsable'], aplica_anticipacion=datos['aplica_anticipacion'])
    with transaction.atomic():
        actual = repo.datos_poliza(poliza_id)
        periodo = next((v for v in repo.obtener_poliza(poliza_id).vigencias if not v.archivada), None)
        if periodo is None or periodo.id != vigencia_id or actual['estado'] == 'cancelada':
            raise ValueError('La vigencia cambió; vuelve a abrir la póliza.')
        if (actual['asegurado_id'] != datos['asegurado_id'] or actual['tipo'] != datos['tipo']
                or (actual['modalidad_auto'] or '') != (datos['modalidad_auto'] or '')):
            raise ValueError('El asegurado, tipo y modalidad no se cambian en una corrección.')
        repo.actualizar_datos_poliza(
            poliza_id, responsable=datos['responsable'],
            aplica_anticipacion=datos['aplica_anticipacion'],
            aseguradora_id=datos['aseguradora_id'], descripcion_riesgo=datos['descripcion_riesgo'])
        repo.actualizar_vigencia(vigencia_id, **_vigencia(datos))
        if datos['tipo'] == 'autos':
            repo.reemplazar_vehiculos(vigencia_id, vehiculos)
        repo.reemplazar_cobros_pendientes(vigencia_id, cobros)
        repo.obtener_poliza(poliza_id)
    return poliza_id


def renovar_poliza(poliza_id, anterior_id, datos, vehiculos, cobros):
    actual = repo.datos_poliza(poliza_id)
    periodo = next((v for v in repo.obtener_poliza(poliza_id).vigencias if not v.archivada), None)
    if periodo is None or periodo.id != anterior_id or actual['estado'] == 'cancelada':
        raise ValueError('La vigencia cambió; vuelve a abrir la póliza.')
    if (actual['asegurado_id'] != datos['asegurado_id'] or actual['aseguradora_id'] != datos['aseguradora_id']
            or actual['tipo'] != datos['tipo'] or (actual['modalidad_auto'] or '') != (datos['modalidad_auto'] or '')):
        raise ValueError('La renovación conserva el asegurado y los datos de la póliza.')
    return repo.renovar_poliza(poliza_id, _vigencia(datos), vehiculos, cobros)


def cancelar_poliza(poliza_id):
    repo.cancelar_poliza(poliza_id)


def actualizar_cobro(cobro_id, estado):
    if estado != 'pagado':
        raise ValueError('Solo se puede marcar un cobro pendiente como pagado.')
    cobro = repo.datos_cobro(cobro_id)
    if cobro['archivada'] or cobro['estado_poliza'] == 'cancelada':
        raise ValueError('Los cobros del histórico y las pólizas canceladas son de consulta.')
    repo.registrar_pago(cobro_id)
    return cobro['poliza_id']


def _fecha_atencion(fecha, dias, inhabiles):
    aviso = fecha - timedelta(days=dias)
    while aviso.weekday() >= 5 or aviso in inhabiles:
        aviso -= timedelta(days=1)
    return aviso


def consultar_cartera(filtros=None, historico=False):
    """Clasifica vigencias actuales e históricas y ordena por fecha próxima."""
    filtros = filtros or {}
    hoy = timezone.localdate()
    configuracion = cargar_configuracion()
    tipo = filtros.get('tipo') or 'todos'
    atencion = filtros.get('atencion') or 'todos'
    if tipo not in ('todos', 'renovacion', 'cobranza') or atencion not in ('todos', 'requiere', 'sin_atencion'):
        raise ValueError('El filtro solicitado no es válido.')
    desde, hasta = filtros.get('desde'), filtros.get('hasta')
    if desde and hasta and desde > hasta:
        raise ValueError('La fecha inicial debe ser anterior a la final.')
    filas = repo.filas_cartera(asegurado_id=filtros.get('asegurado_id'),
                              aseguradora_id=filtros.get('aseguradora_id'), historico=True)
    resultado = []
    for fila in filas:
        es_historica = fila['archivada'] or fila['estado'] == 'cancelada' or fila['fecha_fin'] < hoy
        if bool(es_historica) != historico:
            continue
        fechas = ([fila['fecha_fin']] if tipo != 'cobranza' else [])
        if tipo != 'renovacion' and fila['proximo_cobro'] is not None:
            fechas.append(fila['proximo_cobro'])
        if not fechas:
            continue
        proxima = min(fechas)
        if (desde and proxima < desde) or (hasta and proxima > hasta):
            continue
        motivos = []
        if not es_historica and fila['aplica_anticipacion']:
            for fecha, motivo in ((fila['fecha_fin'], 'Atención por renovación'),
                                  (fila['proximo_cobro'], 'Atención por cobranza')):
                if fecha and hoy >= _fecha_atencion(fecha, configuracion['anticipacion_dias'],
                                                    configuracion['dias_inhabiles']):
                    motivos.append(motivo)
        if (atencion == 'requiere' and not motivos) or (atencion == 'sin_atencion' and motivos):
            continue
        resultado.append({**fila, 'id': fila['vigencia_id'], 'es_historica': bool(es_historica),
                          'motivos': motivos, 'fecha_proxima': proxima})
    return sorted(resultado, key=lambda fila: (fila['fecha_proxima'], fila['numero_poliza']))
