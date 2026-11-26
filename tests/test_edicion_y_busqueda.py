"""Paradigma declarativo: corrección de datos capturados (RF-04, HU-06) y
búsqueda incremental (RF-17, HU-03).

Datos 100 % ficticios. Se ejecutan con: python manage.py test
"""
from datetime import date, timedelta

from django.db import connection

from tests.test_persistencia import HOY, BaseSQL
from urdiales_polizas.persistencia import repositorios as repo


class BusquedaIncrementalTests(BaseSQL):
    """HU-03: localizar asegurado, póliza o vehículo mientras se escribe."""

    def setUp(self):
        super().setUp()
        self.auto = self.poliza('AUT-500', vehiculos=[
            {'descripcion': 'Sedán ficticio', 'modelo': 2021, 'serie': '3VWFICTICIO1224'}])
        self.jose = repo.crear_asegurado('José Núñez Ficticio')

    def test_ultimos_digitos_de_la_serie(self):
        filas = repo.buscar_incremental('1224')
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['poliza_id'], self.auto)
        self.assertEqual(filas[0]['asegurado'], 'Cliente Ficticio SQL')
        self.assertEqual(filas[0]['coincide_en'], 'serie')

    def test_nombre_sin_acentos_ni_mayusculas(self):
        for texto in ('jose', 'NUNEZ', 'núñez'):
            filas = repo.buscar_incremental(texto)
            self.assertEqual([f['asegurado_id'] for f in filas], [self.jose], texto)
        self.assertIsNone(filas[0]['poliza_id'])  # asegurado sin pólizas también aparece
        self.assertEqual(filas[0]['coincide_en'], 'nombre')

    def test_numero_de_poliza_parcial(self):
        filas = repo.buscar_incremental('aut-5')
        self.assertEqual([f['numero_poliza'] for f in filas], ['AUT-500'])
        self.assertEqual(filas[0]['coincide_en'], 'poliza')

    def test_comodines_se_buscan_como_texto(self):
        self.assertEqual(repo.buscar_incremental('%%'), [])
        self.assertEqual(repo.buscar_incremental('__'), [])

    def test_texto_corto_no_trae_toda_la_cartera(self):
        self.assertEqual(repo.buscar_incremental(''), [])
        self.assertEqual(repo.buscar_incremental(' a '), [])

    def test_limite_de_resultados(self):
        for i in range(5):
            repo.crear_asegurado(f'Persona Limite {i}')
        self.assertEqual(len(repo.buscar_incremental('limite', limite=3)), 3)
        for limite in (0, 51, 'x'):
            with self.assertRaises(ValueError):
                repo.buscar_incremental('limite', limite=limite)

    def test_solo_la_vigencia_actual(self):
        anterior = repo.obtener_poliza(self.auto).vigencias[-1]
        repo.renovar_poliza(self.auto, {'numero_poliza': 'AUT-501', 'fecha_inicio': anterior.fecha_fin,
                                        'fecha_fin': anterior.fecha_fin + timedelta(days=365),
                                        'forma_pago': 'anual'},
                            [{'descripcion': 'Sedán ficticio', 'modelo': 2021, 'serie': '3VWFICTICIO1224'}])
        self.assertEqual([f['numero_poliza'] for f in repo.buscar_incremental('1224')], ['AUT-501'])
        self.assertEqual(repo.buscar_incremental('AUT-500'), [])


class CorregirVigenciaTests(BaseSQL):
    """HU-06: corregir datos guardados sin romper las reglas de la base."""

    def setUp(self):
        super().setUp()
        self.poliza_id = self.poliza('C-1', cobros=[(10, 'pendiente')])
        self.vigencia = repo.obtener_poliza(self.poliza_id).vigencias[-1]

    def test_corrige_numero_fechas_y_cobro(self):
        repo.actualizar_vigencia(self.vigencia.id, numero_poliza=' c-1b ', fecha_fin=HOY + timedelta(days=40),
                                 forma_pago='mensual', tipo_cobro='domiciliado')
        datos = repo.datos_vigencia(self.vigencia.id)
        self.assertEqual(datos['numero_poliza'], 'C-1B')
        self.assertEqual(datos['fecha_fin'], HOY + timedelta(days=40))
        self.assertEqual((datos['forma_pago'], datos['tipo_cobro']), ('mensual', 'domiciliado'))
        self.assertEqual(datos['fecha_inicio'], self.vigencia.fecha_inicio)  # lo que no se manda no cambia

    def test_fecha_inexistente_no_guarda_nada(self):
        with self.assertRaisesMessage(ValueError, 'La fecha de fin debe ser una fecha válida.'):
            repo.actualizar_vigencia(self.vigencia.id, numero_poliza='OTRO', fecha_fin='2026-02-30')
        self.assertEqual(repo.datos_vigencia(self.vigencia.id)['numero_poliza'], 'C-1')

    def test_datos_invalidos(self):
        for cambios in ({'fecha_fin': self.vigencia.fecha_inicio}, {'forma_pago': 'diaria'},
                        {'tipo_cobro': 'efectivo'}, {'numero_poliza': '   '}):
            with self.assertRaises(ValueError, msg=cambios):
                repo.actualizar_vigencia(self.vigencia.id, **cambios)

    def test_cobros_deben_quedar_dentro_de_las_nuevas_fechas(self):
        with self.assertRaisesMessage(ValueError, 'Hay cobros fuera de las nuevas fechas'):
            repo.actualizar_vigencia(self.vigencia.id, fecha_fin=HOY + timedelta(days=5))
        self.assertEqual(repo.datos_vigencia(self.vigencia.id)['fecha_fin'], self.vigencia.fecha_fin)
        cobro = repo.listar_cobros(self.vigencia.id)[0]
        repo.eliminar_cobro(cobro['id'])
        repo.actualizar_vigencia(self.vigencia.id, fecha_fin=HOY + timedelta(days=5))
        self.assertEqual(repo.datos_vigencia(self.vigencia.id)['fecha_fin'], HOY + timedelta(days=5))

    def test_cobro_pagado_no_se_elimina(self):
        cobro = repo.listar_cobros(self.vigencia.id)[0]
        repo.registrar_pago(cobro['id'])
        with self.assertRaisesMessage(ValueError, 'Un cobro pagado no se elimina.'):
            repo.eliminar_cobro(cobro['id'])

    def test_renovacion_no_inicia_antes_de_la_anterior(self):
        repo.renovar_poliza(self.poliza_id, {'numero_poliza': 'C-2', 'fecha_inicio': self.vigencia.fecha_fin,
                                             'fecha_fin': self.vigencia.fecha_fin + timedelta(days=365),
                                             'forma_pago': 'anual'},
                            [{'descripcion': 'Vehículo ficticio', 'modelo': 2022, 'serie': 'SERIEC-1'}])
        nueva = repo.obtener_poliza(self.poliza_id).vigencias[-1]
        with self.assertRaisesMessage(ValueError, 'La renovación debe iniciar'):
            repo.actualizar_vigencia(nueva.id, fecha_inicio=self.vigencia.fecha_fin - timedelta(days=1))
        with self.assertRaisesMessage(ValueError, 'Las vigencias del histórico no se editan.'):
            repo.actualizar_vigencia(self.vigencia.id, numero_poliza='C-1X')

    def test_poliza_cancelada_no_se_edita(self):
        repo.cancelar_poliza(self.poliza_id)
        with self.assertRaisesMessage(ValueError, 'Una póliza cancelada no se edita.'):
            repo.actualizar_vigencia(self.vigencia.id, numero_poliza='C-1X')

    def test_trigger_de_fecha_de_cobro_editada(self):
        cobro = repo.listar_cobros(self.vigencia.id)[0]
        with self.assertRaisesMessage(ValueError, 'La fecha del cobro debe estar dentro de la vigencia.'):
            repo._ejecutar('UPDATE cobros SET fecha_vencimiento = %s WHERE id = %s',
                           [(self.vigencia.fecha_fin + timedelta(days=1)).isoformat(), cobro['id']])

    def test_triggers_existen_en_la_base(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            nombres = {fila[0] for fila in cursor.fetchall()}
        self.assertLessEqual({'cobros_dentro_de_vigencia_editada', 'cobro_editado_dentro_de_vigencia'}, nombres)


class CorregirVehiculoTests(BaseSQL):
    def setUp(self):
        super().setUp()
        self.poliza_id = self.poliza('V-1')
        self.vigencia = repo.obtener_poliza(self.poliza_id).vigencias[-1]
        self.vehiculo = repo.listar_vehiculos(self.vigencia.id)[0]

    def test_corrige_serie_mal_escrita(self):
        repo.actualizar_vehiculo(self.vehiculo['id'], serie='nueva serie 987', modelo='2023')
        vehiculo = repo.listar_vehiculos(self.vigencia.id)[0]
        self.assertEqual((vehiculo['serie'], vehiculo['modelo']), ('NUEVASERIE987', 2023))
        self.assertEqual(vehiculo['descripcion'], 'Vehículo ficticio')
        self.assertEqual(repo.buscar_por_serie('987')[0]['poliza_id'], self.poliza_id)

    def test_serie_de_otra_poliza_en_vigor(self):
        self.poliza('V-2')  # su serie es SERIEV-2
        with self.assertRaisesMessage(ValueError, 'La serie ya pertenece a otra póliza en vigor.'):
            repo.actualizar_vehiculo(self.vehiculo['id'], serie='SERIEV-2')

    def test_modelo_invalido(self):
        with self.assertRaises(ValueError):
            repo.actualizar_vehiculo(self.vehiculo['id'], modelo='dos mil')
        self.assertEqual(repo.listar_vehiculos(self.vigencia.id)[0]['modelo'], 2022)

    def test_flotilla_no_puede_repetir_inciso(self):
        flotilla = self.poliza('F-1', modalidad='flotilla', vehiculos=[
            {'descripcion': 'Camión ficticio', 'modelo': 2020, 'serie': 'FLOT001', 'inciso': '1'},
            {'descripcion': 'Camión ficticio', 'modelo': 2020, 'serie': 'FLOT002', 'inciso': '2'}])
        vigencia = repo.obtener_poliza(flotilla).vigencias[-1]
        primero = repo.listar_vehiculos(vigencia.id)[0]
        with self.assertRaisesMessage(ValueError, 'no cumplen las reglas de autos'):
            repo.actualizar_vehiculo(primero['id'], inciso='2')
        self.assertEqual(repo.listar_vehiculos(vigencia.id)[0]['inciso'], '1')
