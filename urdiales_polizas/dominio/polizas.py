"""Entidades del dominio relacionadas con aseguradoras, pólizas y vigencias.

Este módulo contiene las reglas que describen qué es una póliza y qué datos
debe tener. No depende de Django, formularios HTML ni consultas SQL.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Iterable

from .cobros import Cobro


class EstadoPoliza(str, Enum):
    """Estados permitidos para una póliza.

    Hereda de ``str`` para que el valor también pueda intercambiarse con capas
    que reciben texto, por ejemplo la persistencia o una plantilla.
    """

    EN_VIGOR = "en_vigor"
    CANCELADA = "cancelada"


class FormaPago(str, Enum):
    """Frecuencias de pago admitidas por el proyecto."""

    MENSUAL = "mensual"
    TRIMESTRAL = "trimestral"
    SEMESTRAL = "semestral"
    ANUAL = "anual"


class ModalidadAuto(str, Enum):
    """Modalidades de una póliza de autos."""

    INDIVIDUAL = "individual"
    FLOTILLA = "flotilla"


def _validar_id(valor: int, nombre: str) -> None:
    """Rechaza identificadores que no sean enteros positivos.

    ``bool`` es una subclase de ``int`` en Python, por eso se rechaza por
    separado: ``True`` no debe aceptarse como el identificador 1.
    """

    if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
        raise ValueError(f"{nombre} debe ser un entero positivo.")


def _validar_texto_requerido(valor: str, nombre: str) -> None:
    """Comprueba que un dato de texto exista y no contenga solo espacios."""

    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"{nombre} es obligatorio.")


@dataclass(frozen=True)
class Aseguradora:
    """Compañía que emite las pólizas.

    Es una ``dataclass`` inmutable porque, en este modelo, solo representa el
    identificador y el nombre de una compañía del catálogo.
    """

    id: int
    nombre: str

    def __post_init__(self) -> None:
        # Las anotaciones indican los tipos esperados, pero Python no los
        # verifica automáticamente; por eso se validan explícitamente.
        _validar_id(self.id, "El id de la aseguradora")
        _validar_texto_requerido(self.nombre, "El nombre de la aseguradora")


@dataclass(frozen=True)
class VehiculoAsegurado:
    """Vehículo incluido en una vigencia de una póliza de autos."""

    descripcion: str
    modelo: int
    serie: str
    inciso: str = ""

    def __post_init__(self) -> None:
        # Se valida al construirlo para que no exista un vehículo inválido.
        if not self.validar():
            raise ValueError(
                "El vehículo requiere descripción, modelo entero positivo y serie."
            )

    def validar(self) -> bool:
        """Indica si los datos del vehículo cumplen sus reglas básicas.

        El inciso puede quedar vacío para un auto individual; la póliza de
        flotilla exige que cada vehículo tenga uno.
        """

        return (
            isinstance(self.descripcion, str)
            and bool(self.descripcion.strip())
            and isinstance(self.modelo, int)
            and not isinstance(self.modelo, bool)
            and self.modelo > 0
            and isinstance(self.serie, str)
            and bool(self.serie.strip())
            and isinstance(self.inciso, str)
        )


@dataclass
class Vigencia:
    """Periodo de cobertura de una póliza.

    Una renovación crea otra ``Vigencia``. Así se conservan las fechas y el
    número de póliza que correspondieron a cada periodo histórico.
    """

    id: int
    numero_poliza: str
    fecha_inicio: date
    fecha_fin: date
    forma_pago: FormaPago | str
    archivada: bool = False
    vehiculos: tuple[VehiculoAsegurado, ...] = ()
    cobros: tuple[Cobro, ...] = ()

    def __post_init__(self) -> None:
        # Identifica esta vigencia y conserva su número como texto, ya que
        # puede contener letras, guiones o ceros iniciales.
        _validar_id(self.id, "El id de la vigencia")
        _validar_texto_requerido(self.numero_poliza, "El número de póliza")

        # Comprueba que se recibieron fechas y que el periodo tenga duración.
        if not isinstance(self.fecha_inicio, date):
            raise ValueError("La fecha de inicio debe ser una fecha válida.")
        if not isinstance(self.fecha_fin, date):
            raise ValueError("La fecha de fin debe ser una fecha válida.")
        if self.fecha_fin <= self.fecha_inicio:
            raise ValueError("La fecha de fin debe ser posterior a la de inicio.")

        # Convierte el texto recibido a una opción controlada de FormaPago.
        # Si el texto no existe en el enum, se informa como dato inválido.
        try:
            self.forma_pago = FormaPago(self.forma_pago)
        except (TypeError, ValueError) as error:
            raise ValueError("Selecciona una forma de pago válida.") from error

        if not isinstance(self.archivada, bool):
            raise ValueError("El indicador de vigencia archivada debe ser booleano.")

        # Las relaciones se guardan como tuplas para que una colección externa
        # (por ejemplo, una lista recibida del formulario) no cambie por fuera.
        try:
            self.vehiculos = tuple(self.vehiculos)
            self.cobros = tuple(self.cobros)
        except TypeError as error:
            raise ValueError("Vehículos y cobros deben ser colecciones.") from error

        if not all(isinstance(v, VehiculoAsegurado) for v in self.vehiculos):
            raise ValueError("La vigencia solo puede contener vehículos válidos.")
        if not all(isinstance(c, Cobro) for c in self.cobros):
            raise ValueError("La vigencia solo puede contener cobros válidos.")

    def archivar(self) -> None:
        """Marca este periodo como histórico, por ejemplo al renovarlo."""

        self.archivada = True

    def esta_vencida(self, hoy: date) -> bool:
        """Devuelve si el periodo terminó antes de la fecha indicada.

        ``hoy`` se recibe como parámetro para que la regla sea comprobable y no
        dependa de la fecha del sistema al ejecutar el programa.
        """

        if not isinstance(hoy, date):
            raise ValueError("La fecha de comparación debe ser una fecha válida.")
        return self.fecha_fin < hoy


class Poliza(ABC):
    """Clase base abstracta para cualquier tipo de póliza.

    Las pólizas comparten asegurado, aseguradora, estado y vigencias. Esta
    clase no puede instanciarse directamente: las subclases deben implementar
    ``validar_riesgo`` según el tipo de seguro.
    """

    def __init__(
        self,
        id: int,
        asegurado_id: int,
        aseguradora_id: int,
        vigencias: Iterable[Vigencia],
    ) -> None:
        # Verifica los identificadores que relacionan la póliza con los otros
        # registros del dominio.
        _validar_id(id, "El id de la póliza")
        _validar_id(asegurado_id, "El id del asegurado")
        _validar_id(aseguradora_id, "El id de la aseguradora")

        # La asociación del diagrama requiere al menos una vigencia por póliza.
        try:
            periodos = tuple(vigencias)
        except TypeError as error:
            raise ValueError("Las vigencias deben recibirse como una colección.") from error
        if not periodos:
            raise ValueError("La póliza debe tener al menos una vigencia.")
        if not all(isinstance(v, Vigencia) for v in periodos):
            raise ValueError("Todas las vigencias deben ser objetos Vigencia.")

        # El guion bajo señala que estos atributos son internos por convención.
        self._id = id
        self._asegurado_id = asegurado_id
        self._aseguradora_id = aseguradora_id
        self._vigencias = list(periodos)
        self._estado = EstadoPoliza.EN_VIGOR

    @property
    def id(self) -> int:
        """Identificador de la póliza, disponible para consulta."""

        return self._id

    @property
    def asegurado_id(self) -> int:
        """Identificador del asegurado titular."""

        return self._asegurado_id

    @property
    def aseguradora_id(self) -> int:
        """Identificador de la compañía que emite la póliza."""

        return self._aseguradora_id

    @property
    def estado(self) -> EstadoPoliza:
        """Estado actual de la póliza."""

        return self._estado

    @estado.setter
    def estado(self, nuevo_estado: EstadoPoliza | str) -> None:
        """Valida los cambios de estado y evita reactivar pólizas canceladas.

        Esta es la propiedad que protege una regla del dominio: solo se
        aceptan estados declarados en ``EstadoPoliza`` y una póliza cancelada
        no puede regresar a ``en_vigor``.
        """

        try:
            estado_validado = EstadoPoliza(nuevo_estado)
        except (TypeError, ValueError) as error:
            raise ValueError("El estado de la póliza no es válido.") from error

        if (
            getattr(self, "_estado", None) == EstadoPoliza.CANCELADA
            and estado_validado != EstadoPoliza.CANCELADA
        ):
            raise ValueError("Una póliza cancelada no puede volver a estar en vigor.")

        self._estado = estado_validado

    @property
    def vigencias(self) -> tuple[Vigencia, ...]:
        """Devuelve los periodos en una colección que no se puede modificar.

        Para agregar un periodo debe usarse ``agregar_vigencia``, que primero
        comprueba las reglas del tipo de póliza.
        """

        return tuple(self._vigencias)

    def cancelar(self) -> None:
        """Cancela la póliza.

        No hay una operación para volver a ponerla en vigor; si se necesita
        una nueva póliza, se registra como otra póliza.
        """

        # La asignación pasa por el setter, que valida la transición.
        self.estado = EstadoPoliza.CANCELADA

    def agregar_vigencia(self, vigencia: Vigencia) -> None:
        """Agrega una renovación después de validar el riesgo de ese periodo."""

        if self.estado == EstadoPoliza.CANCELADA:
            raise ValueError("No se puede agregar una vigencia a una póliza cancelada.")
        if not isinstance(vigencia, Vigencia):
            raise ValueError("Se debe agregar un objeto Vigencia.")
        if not self.validar_riesgo(vigencia):
            raise ValueError("La vigencia no cumple las reglas de riesgo de la póliza.")

        # El periodo anterior se archiva y el nuevo se agrega a la historia.
        if self._vigencias:
            self._vigencias[-1].archivar()
        self._vigencias.append(vigencia)

    @abstractmethod
    def validar_riesgo(self, vigencia: Vigencia) -> bool:
        """Indica si el riesgo de una vigencia es válido para esta póliza.

        ``PolizaAuto`` y ``PolizaDanios`` implementan esta operación de forma
        distinta. Devuelve ``True`` si cumple las reglas y ``False`` si no.
        """

        raise NotImplementedError("Cada tipo de póliza debe validar su riesgo.")


class PolizaAuto(Poliza):
    """Póliza de autos, individual o de flotilla."""

    def __init__(
        self,
        id: int,
        asegurado_id: int,
        aseguradora_id: int,
        modalidad: ModalidadAuto | str,
        vigencias: Iterable[Vigencia],
    ) -> None:
        # Normaliza la modalidad antes de validar las vigencias.
        try:
            self._modalidad = ModalidadAuto(modalidad)
        except (TypeError, ValueError) as error:
            raise ValueError("La modalidad debe ser individual o flotilla.") from error

        super().__init__(id, asegurado_id, aseguradora_id, vigencias)

        # Comprueba que cada periodo recibido cumpla las reglas de autos.
        if not all(self.validar_riesgo(periodo) for periodo in self.vigencias):
            raise ValueError("Una o más vigencias no cumplen las reglas de autos.")

    @property
    def modalidad(self) -> ModalidadAuto:
        """Modalidad de autos, disponible para consulta."""

        return self._modalidad

    def validar_riesgo(self, vigencia: Vigencia) -> bool:
        """Valida vehículos, series y, para flotillas, incisos.

        Una póliza individual necesita exactamente un vehículo. Una flotilla
        necesita uno o más vehículos y un inciso único para cada uno.
        """

        if not isinstance(vigencia, Vigencia):
            return False

        vehiculos = vigencia.vehiculos
        if not vehiculos:
            return False
        if not all(vehiculo.validar() for vehiculo in vehiculos):
            return False
        if self.modalidad == ModalidadAuto.INDIVIDUAL and len(vehiculos) != 1:
            return False

        # La serie identifica a cada unidad y no debe repetirse en el periodo.
        series = [vehiculo.serie.strip().casefold() for vehiculo in vehiculos]
        if len(set(series)) != len(series):
            return False

        if self.modalidad == ModalidadAuto.FLOTILLA:
            incisos = [vehiculo.inciso.strip() for vehiculo in vehiculos]
            if any(not inciso for inciso in incisos):
                return False
            if len(set(incisos)) != len(incisos):
                return False

        return True


class PolizaDanios(Poliza):
    """Póliza que cubre un bien o riesgo distinto a un vehículo."""

    def __init__(
        self,
        id: int,
        asegurado_id: int,
        aseguradora_id: int,
        descripcion_riesgo: str,
        vigencias: Iterable[Vigencia],
    ) -> None:
        # Conserva la descripción como parte de los datos de esta póliza.
        _validar_texto_requerido(descripcion_riesgo, "La descripción del riesgo")
        self._descripcion_riesgo = descripcion_riesgo.strip()

        super().__init__(id, asegurado_id, aseguradora_id, vigencias)

        # Daños no debe tener vehículos asociados a sus vigencias.
        if not all(self.validar_riesgo(periodo) for periodo in self.vigencias):
            raise ValueError("Una póliza de daños no puede contener vehículos.")

    @property
    def descripcion_riesgo(self) -> str:
        """Descripción del bien o riesgo cubierto."""

        return self._descripcion_riesgo

    def validar_riesgo(self, vigencia: Vigencia) -> bool:
        """Valida que exista la descripción y que la vigencia no tenga autos."""

        return (
            isinstance(vigencia, Vigencia)
            and bool(self.descripcion_riesgo.strip())
            and not vigencia.vehiculos
        )
