"""Migra la M2M legacy `workshop_progress_log_input_line_rel` al nuevo modelo
`workshop.progress.log.line`.

Antes: una placa por corrida (todo o nada), enlazada por una tabla rel.
Ahora: una corrida puede capturar consumos parciales por placa en una One2many.

La migración asume el comportamiento previo: cada placa asignada a una corrida
se consumió íntegra → `consumed_sqm = area_sqm` de la placa.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'workshop_progress_log_input_line_rel'
        )
    """)
    legacy_exists = cr.fetchone()[0]
    if not legacy_exists:
        _logger.info("[stone_workshop] No hay tabla legacy de bitácora — nada que migrar.")
        return

    cr.execute("SELECT COUNT(*) FROM workshop_progress_log_input_line_rel")
    legacy_rows = cr.fetchone()[0]
    _logger.info("[stone_workshop] Migrando %s filas de bitácora a consumos parciales...", legacy_rows)

    if legacy_rows:
        # No usamos ON CONFLICT: la constraint única `uniq_log_input` del modelo
        # nuevo se aplica DESPUÉS de los post-migrate en Odoo 19, así que aún no
        # existe en este punto. La tabla M2M de origen ya es única por el par
        # (log_id, input_line_id), de modo que no puede haber duplicados; aun
        # así filtramos con NOT EXISTS para que el script sea idempotente.
        cr.execute("""
            INSERT INTO workshop_progress_log_line
                (log_id, order_id, company_id, input_line_id, consumed_sqm,
                 create_uid, create_date, write_uid, write_date)
            SELECT
                rel.log_id,
                log.order_id,
                log.company_id,
                rel.input_line_id,
                COALESCE(input.area_sqm, input.qty_in, 0.0),
                1,
                (NOW() AT TIME ZONE 'UTC'),
                1,
                (NOW() AT TIME ZONE 'UTC')
            FROM workshop_progress_log_input_line_rel rel
            JOIN workshop_progress_log log ON log.id = rel.log_id
            JOIN workshop_input_line input ON input.id = rel.input_line_id
            WHERE NOT EXISTS (
                SELECT 1 FROM workshop_progress_log_line existing
                WHERE existing.log_id = rel.log_id
                  AND existing.input_line_id = rel.input_line_id
            );
        """)
        cr.execute("SELECT COUNT(*) FROM workshop_progress_log_line")
        inserted = cr.fetchone()[0]
        _logger.info("[stone_workshop] Insertadas %s filas de consumo parcial.", inserted)

    cr.execute("DROP TABLE IF EXISTS workshop_progress_log_input_line_rel CASCADE;")
    _logger.info("[stone_workshop] Tabla legacy eliminada.")
