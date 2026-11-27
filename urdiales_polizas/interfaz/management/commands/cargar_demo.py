"""Carga opcional por terminal de datos exclusivamente ficticios."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from urdiales_polizas.datos.carga_demo import cargar_demostracion, generar_csv, indicadores


class Command(BaseCommand):
    help = "Genera o carga 300 pólizas ficticias. La carga exige una base vacía y nunca borra datos."

    def add_arguments(self, parser):
        origen = parser.add_mutually_exclusive_group()
        origen.add_argument("--archivo", type=Path, help="CSV ficticio creado con el formato de --generar.")
        origen.add_argument("--generar", type=Path, help="Guarda un CSV sintético y termina sin cargarlo.")
        parser.add_argument("--indicadores", action="store_true", help="Muestra tres cifras al finalizar la carga.")

    def handle(self, *args, **options):
        try:
            if options["generar"]:
                with options["generar"].open("x", encoding="utf-8", newline="") as salida:
                    generar_csv(salida)
                self.stdout.write(self.style.SUCCESS(f"CSV ficticio creado: {options['generar']} (300 pólizas)."))
                return
            resultado = cargar_demostracion(options["archivo"])
            self.stdout.write(self.style.SUCCESS(
                "Demostración cargada: " + ", ".join(f"{valor} {nombre}" for nombre, valor in resultado.items()) + "."
            ))
            if options["indicadores"]:
                self._mostrar_indicadores()
        except (OSError, ValueError, DatabaseError) as error:
            raise CommandError(str(error)) from error

    def _mostrar_indicadores(self):
        for nombre, cantidad in indicadores().items():
            self.stdout.write(f"{nombre}: {cantidad}")
