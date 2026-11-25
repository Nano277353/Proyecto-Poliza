-- Esquema de la base de datos (paradigma declarativo).
-- Se aplica con la migración interfaz/0001_inicial: python manage.py migrate
--
-- Las reglas que no deben depender de una pantalla se declaran aquí mismo:
-- CHECK, UNIQUE, llaves foráneas, índices y triggers. Las fechas se guardan
-- como texto ISO (AAAA-MM-DD) para que SQLite las compare y calcule con date().

-- ---------------------------------------------------------------- asegurados

CREATE TABLE asegurados (
    id     INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL CHECK (length(trim(nombre)) > 0)
);

-- "general" = contacto del asegurado; "referente" = quien lo recomendó.
CREATE TABLE contactos (
    id           INTEGER PRIMARY KEY,
    asegurado_id INTEGER NOT NULL REFERENCES asegurados(id) ON DELETE CASCADE,
    nombre       TEXT,
    rol          TEXT NOT NULL DEFAULT 'general' CHECK (rol IN ('general', 'referente'))
);

-- Teléfono y correo pueden no existir; si existen, no se repiten en el contacto.
CREATE TABLE medios_contacto (
    id          INTEGER PRIMARY KEY,
    contacto_id INTEGER NOT NULL REFERENCES contactos(id) ON DELETE CASCADE,
    tipo        TEXT NOT NULL CHECK (tipo IN ('telefono', 'correo')),
    valor       TEXT NOT NULL CHECK (length(trim(valor)) > 0),
    UNIQUE (contacto_id, tipo, valor)
);

-- ---------------------------------------------------------------- aseguradoras

CREATE TABLE aseguradoras (
    id     INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(nombre)) > 0)
);

-- ---------------------------------------------------------------- pólizas

-- Datos que se conservan entre renovaciones. Solo autos o daños:
--   autos  → modalidad individual o flotilla, sin descripción de riesgo.
--   daños  → descripción del bien asegurado, sin modalidad.
CREATE TABLE polizas (
    id                  INTEGER PRIMARY KEY,
    asegurado_id        INTEGER NOT NULL REFERENCES asegurados(id),
    aseguradora_id      INTEGER NOT NULL REFERENCES aseguradoras(id),
    tipo                TEXT NOT NULL CHECK (tipo IN ('autos', 'danios')),
    modalidad_auto      TEXT CHECK (modalidad_auto IN ('individual', 'flotilla')),
    descripcion_riesgo  TEXT,
    estado              TEXT NOT NULL DEFAULT 'en_vigor' CHECK (estado IN ('en_vigor', 'cancelada')),
    aplica_anticipacion INTEGER NOT NULL DEFAULT 1 CHECK (aplica_anticipacion IN (0, 1)),
    -- Persona del despacho que atiende la póliza (p. ej. autos y daños).
    responsable         TEXT NOT NULL DEFAULT '',
    CHECK (
        (tipo = 'autos'  AND modalidad_auto IS NOT NULL AND descripcion_riesgo IS NULL)
     OR (tipo = 'danios' AND modalidad_auto IS NULL AND length(trim(coalesce(descripcion_riesgo, ''))) > 0)
    )
);

CREATE INDEX polizas_por_asegurado   ON polizas(asegurado_id);
CREATE INDEX polizas_por_aseguradora ON polizas(aseguradora_id);

-- Cada renovación crea una vigencia nueva y archiva la anterior (histórico).
CREATE TABLE vigencias (
    id            INTEGER PRIMARY KEY,
    poliza_id     INTEGER NOT NULL REFERENCES polizas(id),
    -- Texto: puede tener letras, guiones o ceros iniciales.
    numero_poliza TEXT NOT NULL CHECK (length(trim(numero_poliza)) > 0),
    fecha_inicio  TEXT NOT NULL CHECK (date(fecha_inicio) IS fecha_inicio),
    fecha_fin     TEXT NOT NULL CHECK (date(fecha_fin) IS fecha_fin),
    forma_pago    TEXT NOT NULL CHECK (forma_pago IN ('mensual', 'trimestral', 'semestral', 'anual')),
    -- manual = la "C" del Excel (hay que cobrarle al cliente);
    -- domiciliado = cargo automático a tarjeta o cuenta (solo se verifica).
    tipo_cobro    TEXT NOT NULL DEFAULT 'manual' CHECK (tipo_cobro IN ('manual', 'domiciliado')),
    sin_gracia    INTEGER NOT NULL DEFAULT 0 CHECK (sin_gracia IN (0, 1)),
    archivada     INTEGER NOT NULL DEFAULT 0 CHECK (archivada IN (0, 1)),
    renueva_a_id  INTEGER REFERENCES vigencias(id),
    CHECK (fecha_fin > fecha_inicio)
);

-- Una póliza tiene a lo más una vigencia actual (no archivada).
CREATE UNIQUE INDEX una_vigencia_actual ON vigencias(poliza_id) WHERE archivada = 0;
CREATE INDEX vigencias_por_vencimiento ON vigencias(fecha_fin);

CREATE TABLE vehiculos_asegurados (
    id          INTEGER PRIMARY KEY,
    vigencia_id INTEGER NOT NULL REFERENCES vigencias(id),
    descripcion TEXT NOT NULL CHECK (length(trim(descripcion)) > 0),
    modelo      INTEGER NOT NULL CHECK (modelo > 0),
    -- La serie distingue vehículos del mismo modelo; se busca por sus últimos dígitos.
    serie       TEXT NOT NULL CHECK (length(trim(serie)) > 0),
    inciso      TEXT NOT NULL DEFAULT '',
    UNIQUE (vigencia_id, serie)
);

CREATE INDEX buscar_serie ON vehiculos_asegurados(serie);

-- Los cobros se capturan a mano; la aplicación no procesa pagos.
CREATE TABLE cobros (
    id                INTEGER PRIMARY KEY,
    vigencia_id       INTEGER NOT NULL REFERENCES vigencias(id),
    fecha_vencimiento TEXT NOT NULL CHECK (date(fecha_vencimiento) IS fecha_vencimiento),
    estado            TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'pagado')),
    UNIQUE (vigencia_id, fecha_vencimiento)
);

CREATE INDEX cobros_pendientes ON cobros(vigencia_id, estado);

-- ---------------------------------------------------------------- triggers

-- Una póliza cancelada no vuelve a estar en vigor.
CREATE TRIGGER no_reactivar_cancelada
BEFORE UPDATE OF estado ON polizas
WHEN OLD.estado = 'cancelada' AND NEW.estado <> 'cancelada'
BEGIN
    SELECT RAISE(ABORT, 'Una póliza cancelada no puede volver a estar en vigor.');
END;

-- A una póliza cancelada no se le agregan renovaciones.
CREATE TRIGGER no_renovar_cancelada
BEFORE INSERT ON vigencias
WHEN (SELECT estado FROM polizas WHERE id = NEW.poliza_id) = 'cancelada'
BEGIN
    SELECT RAISE(ABORT, 'No se puede agregar una vigencia a una póliza cancelada.');
END;

-- Solo las pólizas de autos tienen vehículos.
CREATE TRIGGER vehiculos_solo_en_autos
BEFORE INSERT ON vehiculos_asegurados
WHEN (SELECT p.tipo FROM vigencias v JOIN polizas p ON p.id = v.poliza_id
      WHERE v.id = NEW.vigencia_id) <> 'autos'
BEGIN
    SELECT RAISE(ABORT, 'Una póliza de daños no puede contener vehículos.');
END;

-- Un mismo vehículo (serie) no puede estar en dos pólizas en vigor.
CREATE TRIGGER serie_unica_en_vigor_insertar
BEFORE INSERT ON vehiculos_asegurados
WHEN EXISTS (
    SELECT 1
    FROM vehiculos_asegurados otro
    JOIN vigencias v ON v.id = otro.vigencia_id AND v.archivada = 0
    JOIN polizas p   ON p.id = v.poliza_id AND p.estado = 'en_vigor'
    WHERE upper(trim(otro.serie)) = upper(trim(NEW.serie))
      AND p.id <> (SELECT poliza_id FROM vigencias WHERE id = NEW.vigencia_id)
)
BEGIN
    SELECT RAISE(ABORT, 'La serie ya pertenece a otra póliza en vigor.');
END;

CREATE TRIGGER serie_unica_en_vigor_actualizar
BEFORE UPDATE OF serie ON vehiculos_asegurados
WHEN EXISTS (
    SELECT 1
    FROM vehiculos_asegurados otro
    JOIN vigencias v ON v.id = otro.vigencia_id AND v.archivada = 0
    JOIN polizas p   ON p.id = v.poliza_id AND p.estado = 'en_vigor'
    WHERE otro.id <> NEW.id
      AND upper(trim(otro.serie)) = upper(trim(NEW.serie))
      AND p.id <> (SELECT poliza_id FROM vigencias WHERE id = NEW.vigencia_id)
)
BEGIN
    SELECT RAISE(ABORT, 'La serie ya pertenece a otra póliza en vigor.');
END;

-- Cada cobro cae dentro de su vigencia.
CREATE TRIGGER cobro_dentro_de_vigencia
BEFORE INSERT ON cobros
WHEN NEW.fecha_vencimiento NOT BETWEEN
     (SELECT fecha_inicio FROM vigencias WHERE id = NEW.vigencia_id)
 AND (SELECT fecha_fin    FROM vigencias WHERE id = NEW.vigencia_id)
BEGIN
    SELECT RAISE(ABORT, 'La fecha del cobro debe estar dentro de la vigencia.');
END;
