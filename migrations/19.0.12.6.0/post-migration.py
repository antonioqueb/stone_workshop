# -*- coding: utf-8 -*-
"""Archiva los lotes originales de reclasificaciones ya aplicadas.

Antes de 19.0.12.6.0 la reclasificación dejaba el lote original activo (en
cero pero visible en listados y selectores). Con el campo active nuevo, este
script archiva retroactivamente esos lotes. Solo toca lotes sin existencias:
si alguien volvió a darle stock al lote viejo, se deja activo para no
esconder inventario.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE stock_lot
           SET active = FALSE
         WHERE id IN (
             SELECT l.lot_from_id
               FROM stock_lot_reclassification_line l
               JOIN stock_lot_reclassification r
                 ON r.id = l.reclassification_id
              WHERE r.state = 'done'
                AND l.lot_from_id IS NOT NULL
                AND l.lot_from_id != COALESCE(l.lot_to_id, 0)
                AND NOT EXISTS (
                    SELECT 1
                      FROM stock_quant q
                     WHERE q.lot_id = l.lot_from_id
                       AND q.quantity != 0
                )
         )
    """)
