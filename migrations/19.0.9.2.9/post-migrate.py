"""Inicializa `queue_sequence` para borradores existentes.

El panel del taller pasa de prioridad por estrellitas a drag-and-drop manual.
Para que el primer render post-upgrade conserve el orden actual, se asigna
`queue_sequence` siguiendo la ordenación previa (priority desc, date_planned
asc, id asc) con paso 10.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'workshop_order'
              AND column_name = 'queue_sequence'
        )
    """)
    if not cr.fetchone()[0]:
        _logger.info("[stone_workshop] queue_sequence aún no existe; nada que migrar.")
        return

    cr.execute("""
        WITH ordered AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       ORDER BY COALESCE(NULLIF(priority, '')::integer, 0) DESC,
                                date_planned ASC NULLS LAST,
                                id ASC
                   ) * 10 AS new_seq
            FROM workshop_order
            WHERE state = 'draft'
        )
        UPDATE workshop_order o
        SET queue_sequence = ordered.new_seq
        FROM ordered
        WHERE o.id = ordered.id;
    """)
    cr.execute("SELECT COUNT(*) FROM workshop_order WHERE state = 'draft'")
    drafts = cr.fetchone()[0]
    _logger.info("[stone_workshop] queue_sequence inicializado para %s borradores.", drafts)
