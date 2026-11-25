"""Carga inicial del Excel del despacho exportado a CSV, o de un ejemplo ficticio."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from urdiales_polizas.datos.carga_demo import cargar_demostracion, generar_csv, indicadores


class Command(BaseCommand):
    help = "Carga el Excel del despacho exportado a CSV (o 300 filas ficticias). Exige una base vacía, nunca borra datos y reporta las filas inválidas."

    def add_arguments(self, parser):
        origen = parser.add_mutually_exclusive_group()
        origen.add_argument("--archivo", type=Path, help="CSV exportado del Excel, con las columnas de --generar.")
        origen.add_argument("--generar", type=Path, help="Guarda un CSV de ejemplo (ficticio) con las columnas del Excel y termina.")
        parser.add_argument("--indicadores", action="store_true", help="Muestra tres cifras al finalizar la carga.")

    def handle(self, *args, **options):
        try:
            if options["generar"]:
                with options["generar"].open("x", encoding="utf-8-sig", newline="") as salida:
                    generar_csv(salida)
                self.stdout.write(self.style.SUCCESS(f"CSV de ejemplo creado: {options['generar']} (300 filas ficticias)."))
                return
            resultado = cargar_demostracion(options["archivo"])
            self._mostrar_reporte(resultado)
            if options["indicadores"]:
                self._mostrar_indicadores()
        except (OSError, ValueError, DatabaseError) as error:
            raise CommandError(str(error)) from error

    def _mostrar_reporte(self, resultado):
        """Reporte de registros válidos e inválidos (RF-02)."""
        self.stdout.write(self.style.SUCCESS(
            f"Filas válidas: {resultado['validas']} · Filas inválidas: {resultado['invalidas']}"
        ))
        self.stdout.write("Guardado: " + ", ".join(
            f"{resultado[clave]} {clave}" for clave in ("asegurados", "polizas", "vigencias", "cobros")) + ".")
        for aviso in resultado.get("advertencias", []):
            self.stdout.write(aviso)
        for error in resultado["errores"]:
            self.stdout.write(self.style.WARNING(f"  Fila {error['fila']}: {error['motivo']}"))

    def _mostrar_indicadores(self):
        for nombre, cantidad in indicadores().items():
            self.stdout.write(f"{nombre}: {cantidad}")
