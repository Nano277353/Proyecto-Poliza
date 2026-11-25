"""Paradigma declarativo: esquema SQL, restricciones, consultas y config.toml.

Datos 100 % ficticios. Se ejecutan con: python manage.py test
"""
from datetime import date, timedelta
import os
from pathlib import Path
import tempfile

from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase

from urdiales_polizas.configuracion import cargar_configuracion
from urdiales_polizas.dominio.clientes import Asegurado
from urdiales_polizas.dominio.polizas import EstadoPoliza, ModalidadAuto, PolizaAuto, PolizaDanios
from urdiales_polizas.persistencia import repositorios as repo

HOY = date(2026, 11, 25)


class BaseSQL(TestCase):
    def setUp(self):
        self.cliente = repo.crear_asegurado('Cliente Ficticio SQL', [
            {'rol': 'general', 'telefonos': ['8100000001', '8100000001'], 'correos': ['CLIENTE@EXAMPLE.COM']},
            {'nombre': 'Referente Ficticio', 'rol': 'referente', 'telefonos': ['8190000001']},
        ])
        self.compania = repo.crear_aseguradora('Compañía Ficticia Norte')

    def poliza(self, numero, tipo='autos', dias_fin=20, cobros=(), vehiculos=None, modalidad='individual', **extra):
        vigencia = {'numero_poliza': numero, 'fecha_inicio': HOY - timedelta(days=300),
                    'fecha_fin': HOY + timedelta(days=dias_fin), 'forma_pago': 'anual'}
        vigencia.update({k: extra.pop(k) for k in ('tipo_cobro', 'sin_gracia', 'forma_pago') if k in extra})
        if vehiculos is None:
            vehiculos = [{'descripcion': 'Vehículo ficticio', 'modelo': 2022, 'serie': f'SERIE{numero}'}] if tipo == 'autos' else []
        cobros = [{'fecha_vencimiento': HOY + timedelta(days=d), 'estado': e} for d, e in cobros]
        return repo.crear_poliza(self.cliente, self.compania, tipo, vigencia, vehiculos, cobros,
                                 modalidad=modalidad if tipo == 'autos' else None,
                                 descripcion_riesgo='Local ficticio' if tipo == 'danios' else None, **extra)


class DominioTests(BaseSQL):
    """Lo guardado se reconstruye con las clases del dominio."""

    def test_asegurado_con_contactos(self):
        asegurado = repo.obtener_asegurado(self.cliente)
        self.assertIsInstance(asegurado, Asegurado)
        general, referente = asegurado.contactos
        self.assertEqual([m.valor for m in general.medios], ['8100000001', 'cliente@example.com'])
        self.assertEqual((referente.nombre, referente.rol), ('Referente Ficticio', 'referente'))

    def test_poliza_de_autos_y_de_danios(self):
        auto = repo.obtener_poliza(self.poliza('a-1'))
        self.assertIsInstance(auto, PolizaAuto)
        self.assertEqual(auto.modalidad, ModalidadAuto.INDIVIDUAL)
        self.assertEqual(auto.vigencias[0].numero_poliza, 'A-1')
        self.assertEqual(auto.vigencias[0].vehiculos[0].serie, 'SERIEA-1')
        danios = repo.obtener_poliza(self.poliza('D-1', tipo='danios'))
        self.assertIsInstance(danios, PolizaDanios)
        self.assertEqual(danios.descripcion_riesgo, 'Local ficticio')

    def test_regla_del_dominio_revierte_el_guardado(self):
        dos = [{'descripcion': 'Uno', 'modelo': 2020, 'serie': 'S1'}, {'descripcion': 'Dos', 'modelo': 2021, 'serie': 'S2'}]
        with self.assertRaisesMessage(ValueError, 'reglas de autos'):
            self.poliza('X-1', vehiculos=dos, modalidad='individual')
        self.assertEqual(repo.filas_cartera(), [])

    def test_flotilla_requiere_incisos(self):
        dos = [{'descripcion': 'Uno', 'modelo': 2020, 'serie': 'S1', 'inciso': '1'},
               {'descripcion': 'Dos', 'modelo': 2021, 'serie': 'S2', 'inciso': '2'}]
        poliza = repo.obtener_poliza(self.poliza('F-1', vehiculos=dos, modalidad='flotilla'))
        self.assertEqual(len(poliza.vigencias[0].vehiculos), 2)

    def test_cancelar_y_no_renovar(self):
        poliza_id = self.poliza('C-1')
        repo.cancelar_poliza(poliza_id)
        self.assertEqual(repo.obtener_poliza(poliza_id).estado, EstadoPoliza.CANCELADA)
        with self.assertRaisesMessage(ValueError, 'cancelada'):
            repo.renovar_poliza(poliza_id, {'numero_poliza': 'C-1', 'fecha_inicio': HOY + timedelta(days=20),
                                            'fecha_fin': HOY + timedelta(days=385), 'forma_pago': 'anual'},
                                [{'descripcion': 'Vehículo ficticio', 'modelo': 2022, 'serie': 'SERIEC-1'}])

    def test_renovar_guarda_historico(self):
        poliza_id = self.poliza('R-1')
        anterior = repo.obtener_poliza(poliza_id).vigencias[-1]
        nueva = {'numero_poliza': 'R-1-27', 'fecha_inicio': anterior.fecha_fin,
                 'fecha_fin': anterior.fecha_fin + timedelta(days=365), 'forma_pago': 'semestral'}
        repo.renovar_poliza(poliza_id, nueva, [{'descripcion': 'Vehículo ficticio', 'modelo': 2022, 'serie': 'SERIER-1'}])
        vigencias = repo.obtener_poliza(poliza_id).vigencias
        self.assertEqual([v.archivada for v in vigencias], [True, False])
        self.assertEqual(len(repo.historico_vigencias(poliza_id)), 2)
        self.assertEqual(len(repo.filas_cartera()), 1)
        self.assertEqual(len(repo.filas_cartera(historico=True)), 2)

    def test_renovacion_no_puede_empezar_antes(self):
        poliza_id = self.poliza('R-2')
        with self.assertRaisesMessage(ValueError, 'cuando termina la vigencia anterior'):
            repo.renovar_poliza(poliza_id, {'numero_poliza': 'R-2', 'fecha_inicio': HOY,
                                            'fecha_fin': HOY + timedelta(days=365), 'forma_pago': 'anual'})

    def test_registrar_pago(self):
        poliza_id = self.poliza('P-1', cobros=[(5, 'pendiente')])
        vigencia_id = repo.obtener_poliza(poliza_id).vigencias[-1].id
        cobro = repo.listar_cobros(vigencia_id)[0]
        repo.registrar_pago(cobro['id'])
        self.assertEqual(repo.listar_cobros(vigencia_id)[0]['estado'], 'pagado')


class RestriccionesTests(BaseSQL):
    """Reglas declaradas en esquema.sql (CHECK, UNIQUE, triggers)."""

    def test_valores_por_omision(self):
        fila = repo.filas_cartera()[0] if repo.filas_cartera() else None
        self.assertIsNone(fila)
        self.poliza('V-1')
        fila = repo.filas_cartera()[0]
        self.assertEqual((fila['tipo_cobro'], fila['sin_gracia'], fila['responsable']), ('manual', False, ''))

    def test_guarda_cobranza_y_responsable(self):
        self.poliza('V-2', tipo_cobro='domiciliado', sin_gracia=True, responsable='  Natalia   Ficticia ')
        fila = repo.filas_cartera()[0]
        self.assertEqual((fila['tipo_cobro'], fila['sin_gracia'], fila['responsable']),
                         ('domiciliado', True, 'Natalia Ficticia'))

    def test_tipo_de_cobro_invalido(self):
        with self.assertRaisesMessage(ValueError, 'manual o domiciliado'):
            self.poliza('V-3', tipo_cobro='efectivo')

    def test_check_rechaza_datos_invalidos_aunque_se_use_sql_directo(self):
        poliza_id = self.poliza('V-4')
        for sql in ("UPDATE vigencias SET tipo_cobro = 'efectivo' WHERE poliza_id = %s",
                    "UPDATE vigencias SET fecha_fin = '25/11/2027' WHERE poliza_id = %s",
                    "UPDATE polizas SET tipo = 'vida' WHERE id = %s"):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as c:
                c.execute(sql, [poliza_id])

    def test_trigger_no_reactivar_cancelada(self):
        poliza_id = self.poliza('V-5')
        repo.cancelar_poliza(poliza_id)
        with self.assertRaisesMessage(IntegrityError, 'no puede volver'), transaction.atomic(), connection.cursor() as c:
            c.execute("UPDATE polizas SET estado = 'en_vigor' WHERE id = %s", [poliza_id])

    def test_serie_no_se_repite_en_dos_polizas_en_vigor(self):
        self.poliza('B-1', vehiculos=[{'descripcion': 'Aveo', 'modelo': 2022, 'serie': 'ABC1224'}])
        with self.assertRaisesMessage(ValueError, 'otra póliza en vigor'):
            self.poliza('B-2', vehiculos=[{'descripcion': 'Aveo', 'modelo': 2022, 'serie': 'abc 1224'}])

    def test_serie_de_cancelada_puede_volver_a_asegurarse(self):
        anterior = self.poliza('B-3', vehiculos=[{'descripcion': 'Aveo', 'modelo': 2022, 'serie': 'XYZ9876'}])
        repo.cancelar_poliza(anterior)
        self.poliza('B-4', vehiculos=[{'descripcion': 'Aveo', 'modelo': 2022, 'serie': 'XYZ9876'}])

    def test_cobro_fuera_de_la_vigencia(self):
        with self.assertRaisesMessage(ValueError, 'dentro de la vigencia'):
            self.poliza('B-5', cobros=[(400, 'pendiente')])

    def test_aseguradora_repetida_sin_importar_mayusculas(self):
        # COLLATE NOCASE de SQLite compara sin mayúsculas solo en letras A-Z;
        # los acentos ya los unifica la carga del CSV con el catálogo de config.toml.
        gnp = repo.crear_aseguradora('GNP')
        with self.assertRaisesMessage(ValueError, 'ya está registrada'):
            repo.crear_aseguradora('gnp')
        self.assertEqual(repo.obtener_o_crear_aseguradora('  Gnp '), gnp)


class ConsultasTests(BaseSQL):
    def test_buscar_por_ultimos_digitos_de_la_serie(self):
        self.poliza('S-1', vehiculos=[{'descripcion': 'Aveo', 'modelo': 2022, 'serie': '3G1TA5AF1224'}])
        self.poliza('S-2', vehiculos=[{'descripcion': 'Aveo', 'modelo': 2022, 'serie': '3G1TA5AF5871'}])
        resultado = repo.buscar_por_serie(' 1224 ')
        self.assertEqual([fila['numero_poliza'] for fila in resultado], ['S-1'])
        self.assertEqual(resultado[0]['asegurado'], 'Cliente Ficticio SQL')

    def test_buscar_por_serie_exige_tres_caracteres_y_no_usa_comodines(self):
        with self.assertRaisesMessage(ValueError, 'al menos los últimos 3'):
            repo.buscar_por_serie('12')
        self.poliza('S-3', vehiculos=[{'descripcion': 'Aveo', 'modelo': 2022, 'serie': 'SERIE123'}])
        self.assertEqual(repo.buscar_por_serie('%%%'), [])

    def test_filtros_combinables(self):
        auto = self.poliza('F-1')
        danios = self.poliza('F-2', tipo='danios')
        cancelada = self.poliza('F-3', tipo='danios')
        repo.cancelar_poliza(cancelada)
        ids = lambda **f: {fila['poliza_id'] for fila in repo.filas_cartera(**f)}
        self.assertEqual(ids(tipo='danios', estado='en_vigor'), {danios})
        self.assertEqual(ids(estado='cancelada'), {cancelada})
        self.assertEqual(ids(tipo='autos', asegurado_id=self.cliente), {auto})
        with self.assertRaisesMessage(ValueError, 'autos o daños'):
            repo.filas_cartera(tipo='vida')

    def test_rango_de_fechas_por_renovacion(self):
        # RF-09: desde/hasta inclusivos sobre la fecha de fin de la vigencia.
        a = self.poliza('RG-1', dias_fin=10)
        b = self.poliza('RG-2', dias_fin=30)
        c = self.poliza('RG-3', dias_fin=60)
        ids = lambda **f: {fila['poliza_id'] for fila in repo.filas_cartera(**f)}
        self.assertEqual(ids(desde=HOY + timedelta(days=10), hasta=HOY + timedelta(days=30)), {a, b})
        self.assertEqual(ids(desde=HOY + timedelta(days=11)), {b, c})
        self.assertEqual(ids(hasta=(HOY + timedelta(days=10)).isoformat()), {a})  # también acepta texto AAAA-MM-DD
        self.assertEqual(ids(desde='', hasta=None), {a, b, c})  # vacío = sin filtro

    def test_rango_de_fechas_por_proximo_cobro(self):
        con_cobro = self.poliza('RC-1', dias_fin=200, cobros=[(-20, 'pagado'), (15, 'pendiente')])
        self.poliza('RC-2', dias_fin=200, cobros=[(15, 'pagado')])  # sin cobros pendientes
        self.poliza('RC-3', dias_fin=200, cobros=[(90, 'pendiente')])
        filas = repo.filas_cartera(desde=HOY, hasta=HOY + timedelta(days=30), fecha_de='cobro')
        self.assertEqual([f['poliza_id'] for f in filas], [con_cobro])
        self.assertEqual(filas[0]['proximo_cobro'], HOY + timedelta(days=15))

    def test_rango_se_combina_con_otros_filtros(self):
        self.poliza('RX-1', dias_fin=20)
        danios = self.poliza('RX-2', tipo='danios', dias_fin=20)
        filas = repo.filas_cartera(tipo='danios', desde=HOY, hasta=HOY + timedelta(days=30))
        self.assertEqual([f['poliza_id'] for f in filas], [danios])

    def test_rango_invalido(self):
        for filtros, mensaje in (
            ({'desde': HOY, 'hasta': HOY - timedelta(days=1)}, 'no puede ser posterior'),
            ({'desde': '25/11/2026'}, 'fecha válida'),
            ({'desde': HOY, 'fecha_de': 'emision'}, 'renovación o al cobro'),
        ):
            with self.subTest(filtros=filtros), self.assertRaisesMessage(ValueError, mensaje):
                repo.filas_cartera(**filtros)

    def test_listar_asegurados_con_conteos(self):
        self.poliza('L-1')
        cancelada = self.poliza('L-2', tipo='danios')
        repo.cancelar_poliza(cancelada)
        fila = repo.listar_asegurados('ficticio sql')[0]
        self.assertEqual((fila['total_polizas'], fila['polizas_en_vigor']), (2, 1))
        self.assertEqual(repo.listar_asegurados('nadie'), [])

    def test_polizas_por_aseguradora(self):
        self.poliza('G-1')
        self.poliza('G-2', tipo='danios')
        repo.crear_aseguradora('Compañía Ficticia Sur')
        resumen = {f['aseguradora']: (f['autos'], f['danios'], f['total']) for f in repo.polizas_por_aseguradora()}
        self.assertEqual(resumen, {'Compañía Ficticia Norte': (1, 1, 2), 'Compañía Ficticia Sur': (0, 0, 0)})


class IndicadoresTests(BaseSQL):
    def setUp(self):
        super().setUp()
        self.poliza('I-1', dias_fin=10, responsable='Responsable A', cobros=[(5, 'pendiente'), (-60, 'pagado'), (-5, 'pendiente')])
        self.poliza('I-2', dias_fin=45, responsable='Responsable A', cobros=[(40, 'pendiente')])
        self.poliza('I-3', tipo='danios', dias_fin=80, responsable='Responsable B', cobros=[(15, 'pendiente')])
        self.poliza('I-4', tipo='danios', dias_fin=200, cobros=[(20, 'pagado')])
        cancelada = self.poliza('I-5', dias_fin=3, responsable='Responsable B', cobros=[(1, 'pendiente')])
        repo.cancelar_poliza(cancelada)

    def test_por_vencer_en_30_60_y_90_dias(self):
        self.assertEqual({f['rango']: f['total'] for f in repo.indicador_por_vencer(HOY)},
                         {'0-30 días': 1, '31-60 días': 1, '61-90 días': 1})

    def test_por_estado(self):
        resumen = {(f['tipo'], f['estado']): f['total'] for f in repo.indicador_por_estado(HOY, anticipacion=30)}
        self.assertEqual(resumen, {
            ('cobranza', 'pagado'): 2, ('cobranza', 'pendiente'): 4,
            ('renovacion', 'en tiempo'): 3, ('renovacion', 'requiere atencion'): 1,
        })

    def test_por_responsable_incluye_cobros_vencidos(self):
        resumen = {f['responsable']: (f['renovaciones'], f['cobranzas'], f['total'])
                   for f in repo.indicador_por_responsable(HOY, anticipacion=30)}
        self.assertEqual(resumen, {'Responsable A': (1, 2, 3), 'Responsable B': (0, 1, 1)})

    def test_anticipacion_por_omision_viene_de_config(self):
        self.assertEqual(repo.indicador_por_estado(HOY), repo.indicador_por_estado(HOY, anticipacion=30))


class ConfiguracionTests(SimpleTestCase):
    def _cargar(self, texto):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / 'config.toml'
            ruta.write_text(texto, encoding='utf-8')
            anterior = os.environ.get('URDIALES_CONFIG')
            os.environ['URDIALES_CONFIG'] = str(ruta)
            try:
                return cargar_configuracion()
            finally:
                if anterior is None:
                    os.environ.pop('URDIALES_CONFIG')
                else:
                    os.environ['URDIALES_CONFIG'] = anterior

    def test_config_del_repositorio(self):
        configuracion = cargar_configuracion()
        self.assertEqual(configuracion['anticipacion_dias'], 30)
        self.assertIn(date(2026, 12, 25), configuracion['dias_inhabiles'])
        self.assertIn('Qualitas', configuracion['aseguradoras'])

    def test_dias_inhabiles_ordenados_y_sin_repetir(self):
        configuracion = self._cargar('anticipacion_dias = 15\ndias_inhabiles = ["2027-01-01", "2026-12-25", "2027-01-01"]\n')
        self.assertEqual(configuracion['dias_inhabiles'], (date(2026, 12, 25), date(2027, 1, 1)))

    def test_valores_invalidos(self):
        for texto, mensaje in (('dias_inhabiles = ["2027-02-30"]\n', 'Día inhábil no válido'),
                               ('anticipacion_dias = 400\n', 'entre 0 y 365'),
                               ('aseguradoras = "GNP"\n', 'lista de nombres')):
            with self.subTest(texto=texto), self.assertRaisesMessage(ValueError, mensaje):
                self._cargar(texto)

    def test_valores_por_omision(self):
        configuracion = self._cargar('')
        self.assertEqual((configuracion['anticipacion_dias'], configuracion['dias_inhabiles']), (30, ()))
        self.assertEqual(len(configuracion['aseguradoras']), 6)
