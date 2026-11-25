"""Entidad de dominio para los cobros asociados a una vigencia."""

from datetime import date


ESTADOS_COBRO = ("pendiente", "pagado")


class Cobro:
    """Representa una fecha de cobro y si ya quedó pagada."""

    def __init__(self, fecha_vencimiento: date, estado: str = "pendiente") -> None:
        # La fecha se valida al crear el objeto y queda de solo lectura.
        if not isinstance(fecha_vencimiento, date):
            raise ValueError("La fecha de vencimiento debe ser una fecha válida.")

        # Solo se aceptan los dos estados que aparecen en el diagrama.
        if estado not in ESTADOS_COBRO:
            raise ValueError("El estado del cobro debe ser pendiente o pagado.")

        # Los atributos con guion bajo son internos por convención.
        self._fecha_vencimiento = fecha_vencimiento
        self._estado = estado

    @property
    def fecha_vencimiento(self) -> date:
        """Devuelve la fecha del cobro sin permitir asignaciones directas."""

        return self._fecha_vencimiento

    @property
    def estado(self) -> str:
        """Permite consultar si el cobro sigue pendiente o ya se pagó."""

        return self._estado

    def marcar_pagado(self) -> None:
        """Marca el cobro como pagado.

        La aplicación no procesa el pago; este método solo registra el estado
        que la persona usuaria confirma manualmente.
        """

        self._estado = "pagado"
