"""Paradigma imperativo: carga del Excel del despacho exportado a CSV (datos ficticios)."""
from datetime import date
import io
from pathlib import Path
import tempfile

from django.test import TestCase

from urdiales_polizas.persistencia import repositorios
from urdiales_polizas.datos.carga_demo import (
    ENCABEZADOS, NOMBRES_MESES, cargar_csv, cargar_demostracion, generar_csv, generar_filas,
    leer_celda_mes, leer_fecha_renovacion,
)

HOY = date(2026, 11, 17)


class CargaDemoTests(TestCase):
    def test_ejemplo_usa_las_columnas_del_excel(self):
        filas = list(generar_filas(hoy=HOY))
        self.assertEqual(len(filas), 300)
        self.assertEqual(tuple(filas[0]), ENCABEZADOS)
        self.assertTrue(any(f["Póliza de daños"] for f in filas))
        celdas = [f[mes] for f in filas for mes in NOMBRES_MESES]
        for marca in ("✓", "C", "R", "*"):
            self.assertTrue(any(marca in celda for celda in celdas), marca)
        # Un solo * por fila: el mes de la renovación anual.
        self.assertTrue(all(sum("*" in f[mes] for mes in NOMBRES_MESES) == 1 for f in filas))

    def test_carga_300_filas_y_reintento_no_duplica(self):
        entrada = io.StringIO()
        generar_csv(entrada, hoy=HOY)
        entrada.seek(0)
        resultado = cargar_csv(entrada, hoy=HOY)
        self.assertEqual((resultado["validas"], resultado["invalidas"], resultado["errores"]), (300, 0, []))
        self.assertEqual((resultado["asegurados"], resultado["polizas"]), (50, 270))
        entrada.seek(0)
        with self.assertRaisesMessage(ValueError, "base ya contiene datos"):
            cargar_csv(entrada, hoy=HOY)
        self.assertEqual(sum(fila["total_polizas"] for fila in repositorios.listar_asegurados()), 270)

    def test_fila_invalida_se_reporta_y_no_detiene_la_carga(self):
        # RNF-09: una fila inválida se reporta con su motivo y las demás se cargan.
        entrada = io.StringIO()
        generar_csv(entrada, cantidad=2, hoy=HOY)
        texto = entrada.getvalue().replace("DEMO0002A", "")
        resultado = cargar_csv(io.StringIO(texto), hoy=HOY)
        self.assertEqual((resultado["validas"], resultado["invalidas"]), (1, 1))
        self.assertEqual(resultado["errores"][0]["fila"], 3)
        self.assertEqual(sum(fila["total_polizas"] for fila in repositorios.listar_asegurados()), 1)

    def test_columnas_faltantes_no_modifican_la_base(self):
        with self.assertRaisesMessage(ValueError, "faltan columnas"):
            cargar_csv(io.StringIO("Asegurado,Columna inventada\nAna,x\n"), hoy=HOY)
        self.assertEqual(repositorios.listar_asegurados(), [])


class FechaDeRenovacionTests(TestCase):
    def test_lee_dia_mes_y_marcas(self):
        fecha, marcas = leer_fecha_renovacion("15/03 ✓ R *", 2026)
        self.assertEqual(fecha, date(2026, 3, 15))
        self.assertEqual(marcas, {"pagada": True, "anual": True, "renovacion": True, "cobranza": False})
        self.assertEqual(leer_fecha_renovacion("15-mar C", 2026)[0], date(2026, 3, 15))
        self.assertTrue(leer_fecha_renovacion("15-mar C", 2026)[1]["cobranza"])
        self.assertEqual(leer_fecha_renovacion("3 de Septiembre", 2025)[0], date(2025, 9, 3))
        self.assertEqual(leer_fecha_renovacion("2026-03-15", None)[0], date(2026, 3, 15))
        self.assertEqual(leer_fecha_renovacion("3/25/2026", None)[0], date(2026, 3, 25))

    def test_errores_claros(self):
        for valor, anio, mensaje in (
            ("", 2026, "Falta la fecha"), ("mañana", 2026, "No se reconoce"),
            ("30/02", 2026, "no existe"), ("15/03 OK", 2026, "Marca no reconocida"),
            ("15/03/2025", 2026, "no coincide"), ("15/03", None, "Falta el año"),
        ):
            with self.subTest(valor=valor), self.assertRaisesMessage(ValueError, mensaje):
                leer_fecha_renovacion(valor, anio)


class ColumnasDeMesesTests(TestCase):
    """El formato del Excel del despacho: una columna por mes con el día y sus marcas."""

    MESES = ",".join(NOMBRES_MESES)
    ENCABEZADO = f"Asegurado,Compañía de seguro,Año de renovación,Número de póliza,{MESES},Forma de pago,Descripción del auto,Modelo,Póliza de daños,Serie,Teléfono,Correo,Recomendó,Estatus"

    @staticmethod
    def meses(**dias):
        return ",".join(dias.get(m.lower(), "") for m in NOMBRES_MESES)

    def cargar(self, *filas):
        return cargar_csv(io.StringIO("\n".join((self.ENCABEZADO,) + filas) + "\n"), hoy=date(2026, 11, 25))

    def test_lee_dia_y_marcas_de_cada_mes(self):
        self.assertEqual(leer_celda_mes("15 ✓ R *", 3), (15, {"pagada": True, "anual": True, "renovacion": True, "cobranza": False}))
        self.assertEqual(leer_celda_mes(" 7.0 C ", 5)[0], 7)
        self.assertEqual(leer_celda_mes("15/03 ✓", 3)[0], 15)
        self.assertIsNone(leer_celda_mes("  ", 1))
        for valor, mes, mensaje in (("15/04", 3, "otro mes"), ("ok", 1, "escribe el día"), ("40", 1, "no es válido"),
                                    ("15 X", 1, "Marca no reconocida")):
            with self.subTest(valor=valor), self.assertRaisesMessage(ValueError, mensaje):
                leer_celda_mes(valor, mes)

    def test_trimestral_crea_sus_cuatro_cobros(self):
        # Renovación 15 de marzo de 2026 (*): cobros en mar, jun, sep (pagados) y dic (cobranza).
        fila = f"Ana Ficticia,GNP,2026,TR-1,{self.meses(mar='15 ✓ *', jun='15 ✓', sep='15 ✓', dic='15 C')},Trimestral,Aveo,2022,,SER1,,,,En vigor"
        resultado = self.cargar(fila)
        self.assertEqual((resultado["validas"], resultado["invalidas"], resultado["advertencias"]), (1, 0, []))
        cartera = repositorios.filas_cartera()[0]
        self.assertEqual((cartera["fecha_inicio"], cartera["fecha_fin"]), (date(2026, 3, 15), date(2027, 3, 15)))
        cobros = repositorios.listar_cobros(cartera["vigencia_id"])
        self.assertEqual([(c["fecha_vencimiento"], c["estado"]) for c in cobros], [
            (date(2026, 3, 15), "pagado"), (date(2026, 6, 15), "pagado"),
            (date(2026, 9, 15), "pagado"), (date(2026, 12, 15), "pendiente"),
        ])

    def test_meses_antes_de_la_renovacion_son_del_anio_siguiente(self):
        # Renovación en octubre de 2026: el cobro de abril es de 2027.
        fila = f"Beto Ficticio,Banorte,2026,SE-1,{self.meses(oct='5 ✓ *', abr='5')},Semestral,,,Bodega,,,,,En vigor"
        self.cargar(fila)
        cobros = repositorios.listar_cobros(repositorios.filas_cartera()[0]["vigencia_id"])
        self.assertEqual([c["fecha_vencimiento"] for c in cobros], [date(2026, 10, 5), date(2027, 4, 5)])

    def test_anual_sin_asterisco_y_errores_de_meses(self):
        resultado = self.cargar(
            f"Ana Ficticia,GNP,2026,AN-1,{self.meses(jul='20 ✓')},Anual,,,Casa,,,,,En vigor",
            f"Ana Ficticia,GNP,2026,ME-1,{self.meses(jul='20', ago='20')},Mensual,,,Casa,,,,,En vigor",
            f"Ana Ficticia,GNP,2026,ME-2,{self.meses(jul='20 *', ago='20 *')},Mensual,,,Casa,,,,,En vigor",
            f"Ana Ficticia,GNP,2026,ME-3,{self.meses()},Mensual,,,Casa,,,,,En vigor",
            f"Ana Ficticia,GNP,2026,ME-4,{self.meses(feb='30 *')},Anual,,,Casa,,,,,En vigor",
        )
        motivos = {e["fila"]: e["motivo"] for e in resultado["errores"]}
        self.assertEqual(resultado["validas"], 1)
        self.assertIn("Marca con *", motivos[3])
        self.assertIn("Solo un mes", motivos[4])
        self.assertIn("Ningún mes", motivos[5])
        self.assertIn("no existe", motivos[6])

    def test_aviso_si_los_meses_no_coinciden_con_la_forma_de_pago(self):
        resultado = self.cargar(f"Ana Ficticia,GNP,2026,ME-5,{self.meses(jul='20 *', ago='20')},Mensual,,,Casa,,,,,En vigor")
        self.assertEqual(resultado["validas"], 1)
        self.assertEqual(resultado["advertencias"], ["Fila 2: la forma de pago mensual espera 12 meses con día y trae 2."])

    def test_faltan_meses_en_el_encabezado(self):
        with self.assertRaisesMessage(ValueError, "Dic"):
            cargar_csv(io.StringIO(self.ENCABEZADO.replace(",Dic", "") + "\n"), hoy=date(2026, 11, 25))


class ExcelRealTests(TestCase):
    """Un CSV como lo exporta Excel en español: punto y coma, Windows-1252, encabezados variados."""

    ENCABEZADO = ("Nombre del asegurado;Compañía;Año;No. de póliza;Fecha renovación;Forma de pago;"
                  "Descripción del auto;Modelo;Póliza de daños;Serie del vehículo;Tel;Correo;"
                  "Nombre de persona que recomendó;Correo de quien recomendó;Estatus;Notas")
    FILAS = (
        # 2: individual con palomita y referente "nombre / cel"
        "Ana Ficticia;QUÁLITAS;2026;ab12c;15/03 ✓;Mensual;Aveo;2022;;3g1ta5af1224;81 1234 5678;ANA@EXAMPLE.COM;Luis Ficticio / 81-9999-0000;;En vigor;x",
        # 3 y 4: flotilla (mismo número, dos series)
        "Taller Ficticio (GNP);;2025;FL-77;10-dic *;Anual;Camioneta;2020;;SER001;;;;;En vigor;",
        "Taller Ficticio;GNP;2025;FL-77;10-dic;Anual;Camioneta;2021;;SER002;;;;;En vigor;",
        # 5: daños, estatus en plural
        "Ana Ficticia;Afirme;2026;D-500;1 de febrero C;Semestral;;;Casa habitación;;;;;;Canceladas;",
        # 6: compañía fuera del catálogo
        "Beto Ficticio;Mapfre;2026;M1;01/01;Anual;;;Local;;;;;;En vigor;",
        # 7: teléfono en notación científica
        "Carla Ficticia;Banorte;2026;B9;01/04;Anual;;;Bodega;;8.11E+09;;;;En vigor;",
        # 8: duplicado de la serie de la flotilla
        "Taller Ficticio;GNP;2025;FL-77;10-dic;Anual;Camioneta;2021;;SER002;;;;;En vigor;",
        # 9: misma póliza con otra forma de pago
        "Taller Ficticio;GNP;2025;FL-77;10-dic;Mensual;Pick up;2019;;SER003;;;;;En vigor;",
        # 10: autos sin serie
        "Dora Ficticia;Afirme;2026;S0;05/05;Anual;Sentra;2018;;;;;;;En vigor;",
        # 11: vacío
        ";;;;;;;;;;;;;;;",
    )

    def cargar(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "excel.csv"
            texto = "\r\n".join((self.ENCABEZADO,) + self.FILAS) + "\r\n"
            ruta.write_bytes(texto.replace("✓", "ü").encode("cp1252"))  # palomita de Wingdings
            return cargar_demostracion(ruta, hoy=date(2026, 11, 25))

    def test_reporte_de_validas_e_invalidas(self):
        resultado = self.cargar()
        motivos = {e["fila"]: e["motivo"] for e in resultado["errores"]}
        self.assertEqual((resultado["validas"], resultado["invalidas"]), (4, 5))
        self.assertEqual(sorted(motivos), [6, 7, 8, 9, 10])
        self.assertIn("Compañía no reconocida", motivos[6])
        self.assertIn("notación científica", motivos[7])
        self.assertIn("Vehículo duplicado", motivos[8])
        self.assertIn("ya aparece en la fila 3", motivos[9])
        self.assertIn("serie", motivos[10])
        self.assertEqual(resultado["advertencias"], ["Columna ignorada: «Notas»."])

    def test_datos_guardados(self):
        self.cargar()
        asegurados = {a["nombre"]: a for a in repositorios.listar_asegurados()}
        self.assertEqual(set(asegurados), {"Ana Ficticia", "Taller Ficticio"})
        self.assertEqual(asegurados["Ana Ficticia"]["total_polizas"], 2)
        filas = {f["numero_poliza"]: f for f in repositorios.filas_cartera()}
        ana = filas["AB12C"]
        self.assertEqual((ana["aseguradora"], ana["modalidad_auto"]), ("Qualitas", "individual"))
        self.assertEqual((ana["fecha_inicio"], ana["fecha_fin"]), (date(2026, 3, 15), date(2027, 3, 15)))
        self.assertEqual(ana["series"], "3G1TA5AF1224")
        poliza = repositorios.obtener_poliza(ana["poliza_id"])
        self.assertEqual([c.estado for c in poliza.vigencias[-1].cobros], ["pagado", "pendiente"])
        flotilla = filas["FL-77"]
        self.assertEqual((flotilla["modalidad_auto"], flotilla["aseguradora"]), ("flotilla", "GNP"))
        vehiculos = repositorios.obtener_poliza(flotilla["poliza_id"]).vigencias[-1].vehiculos
        self.assertEqual([v.inciso for v in vehiculos], ["1", "2"])
        self.assertEqual(filas["D-500"]["estado"], "cancelada")
        contactos = repositorios.obtener_asegurado(asegurados["Ana Ficticia"]["id"]).contactos
        roles = {c.rol: c for c in contactos}
        self.assertEqual(roles["referente"].nombre, "Luis Ficticio")
        self.assertEqual([m.valor for m in roles["referente"].medios], ["8199990000"])
