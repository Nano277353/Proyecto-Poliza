"""Clases del dominio para asegurados, contactos y medios de contacto.

Los datos se validan en el dominio para que las reglas no dependan solamente
de una pantalla o formulario específico.
"""

from dataclasses import dataclass
from re import fullmatch
from collections.abc import Iterable


ROLES_CONTACTO = ("general", "referente")
TIPOS_MEDIO_CONTACTO = ("telefono", "correo")


def _validar_id(valor: int, nombre: str) -> None:
    """Exige que el identificador sea un entero positivo."""

    if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
        raise ValueError(f"{nombre} debe ser un entero positivo.")


def _validar_nombre(nombre: str) -> str:
    """Valida un nombre obligatorio y devuelve su versión sin espacios extra."""

    if not isinstance(nombre, str) or not nombre.strip():
        raise ValueError("El nombre del asegurado es obligatorio.")
    return nombre.strip()


class Asegurado:
    """Persona o entidad titular de una o más pólizas.

    Se usa una clase normal porque el nombre puede cambiar y el asegurado
    administra una colección de contactos. Los cambios pasan por métodos que
    conservan las reglas del objeto.
    """

    def __init__(
        self,
        id: int,
        nombre: str,
        contactos: Iterable["Contacto"] = (),
    ) -> None:
        # Comprueba los datos básicos al momento de crear el asegurado.
        _validar_id(id, "El id del asegurado")
        self._id = id
        self._nombre = _validar_nombre(nombre)

        # Copia la colección recibida para que cambios externos no modifiquen
        # la lista interna del objeto sin pasar por agregar_contacto().
        try:
            contactos_iniciales = list(contactos)
        except TypeError as error:
            raise ValueError("Los contactos deben recibirse como una colección.") from error
        if not all(isinstance(contacto, Contacto) for contacto in contactos_iniciales):
            raise ValueError("Todos los contactos deben ser objetos Contacto.")
        self._contactos = contactos_iniciales

    @property
    def id(self) -> int:
        """Identificador del asegurado; se consulta, no se cambia desde fuera."""

        return self._id

    @property
    def nombre(self) -> str:
        """Nombre actual del asegurado."""

        return self._nombre

    @property
    def contactos(self) -> tuple["Contacto", ...]:
        """Contactos asociados, expuestos como una colección de solo lectura."""

        return tuple(self._contactos)

    def cambiar_nombre(self, nuevo_nombre: str) -> None:
        """Cambia el nombre únicamente si el nuevo valor no está vacío."""

        self._nombre = _validar_nombre(nuevo_nombre)

    def agregar_contacto(self, contacto: "Contacto") -> None:
        """Agrega un contacto perteneciente a este asegurado."""

        if not isinstance(contacto, Contacto):
            raise ValueError("Se debe agregar un objeto Contacto.")
        self._contactos.append(contacto)


class Contacto:
    """Persona relacionada con un asegurado, con uno o más medios de contacto."""

    def __init__(
        self,
        id: int,
        nombre: str | None = None,
        rol: str = "general",
        medios: Iterable["MedioContacto"] = (),
    ) -> None:
        _validar_id(id, "El id del contacto")

        # El nombre del contacto puede omitirse, pero si se proporciona no
        # debe ser un texto compuesto solamente por espacios.
        if nombre is not None:
            if not isinstance(nombre, str):
                raise ValueError("El nombre del contacto debe ser texto o None.")
            nombre = nombre.strip()
            if not nombre:
                nombre = None

        if rol not in ROLES_CONTACTO:
            raise ValueError("El rol debe ser 'general' o 'referente'.")

        try:
            medios_iniciales = list(medios)
        except TypeError as error:
            raise ValueError("Los medios deben recibirse como una colección.") from error
        if not all(isinstance(medio, MedioContacto) for medio in medios_iniciales):
            raise ValueError("Todos los medios deben ser objetos MedioContacto.")

        self._id = id
        self._nombre = nombre
        self._rol = rol
        self._medios = medios_iniciales

    @property
    def id(self) -> int:
        """Identificador del contacto."""

        return self._id

    @property
    def nombre(self) -> str | None:
        """Nombre opcional de la persona de contacto."""

        return self._nombre

    @property
    def rol(self) -> str:
        """Relación del contacto con el asegurado."""

        return self._rol

    @property
    def medios(self) -> tuple["MedioContacto", ...]:
        """Medios asociados, expuestos como una colección de solo lectura."""

        return tuple(self._medios)

    def agregar_medio(self, medio: "MedioContacto") -> None:
        """Agrega un teléfono o correo después de comprobar su validez."""

        if not isinstance(medio, MedioContacto):
            raise ValueError("Se debe agregar un objeto MedioContacto.")
        if not medio.validar():
            raise ValueError("El medio de contacto no es válido.")
        self._medios.append(medio)


@dataclass(frozen=True)
class MedioContacto:
    """Un teléfono o correo asociado a un contacto.

    Es una ``dataclass`` inmutable: una vez creado, el tipo y valor del medio
    no cambian. Si se captura un dato distinto, se crea otro objeto.
    """

    tipo: str
    valor: str

    def __post_init__(self) -> None:
        # Valida al crear el objeto para evitar almacenar medios inválidos.
        if not self.validar():
            raise ValueError("El medio debe ser un teléfono o correo válido.")

    def validar(self) -> bool:
        """Comprueba el tipo permitido y el formato básico del valor.

        Los teléfonos se conservan como texto para mantener prefijos, signos
        y ceros iniciales. La verificación solo exige que incluyan dígitos;
        no impone un formato telefónico único.
        """

        if self.tipo not in TIPOS_MEDIO_CONTACTO:
            return False
        if not isinstance(self.valor, str) or not self.valor.strip():
            return False

        valor = self.valor.strip()
        if self.tipo == "telefono":
            return any(caracter.isdigit() for caracter in valor)

        # Revisión básica de correo; no pretende implementar toda la norma
        # formal de direcciones de correo electrónico.
        return fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", valor) is not None
