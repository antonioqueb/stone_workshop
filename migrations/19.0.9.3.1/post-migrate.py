"""Normaliza estados legacy de workshop.order.

Versiones antiguas del módulo usaban estados que ya no existen (p. ej.
'confirmed'). Esas filas quedan con un valor fuera de la selección actual
(draft / in_workshop / done / cancel), lo que rompe el searchpanel del panel
de órdenes (KeyError al pintar la etiqueta del estado).

Mapeo:
  - 'confirmed'  -> 'in_workshop'  (equivalente semántico: confirmada / en taller)
  - cualquier otro valor inválido -> 'draft'  (estado limpio, re-procesable)

Se hace por SQL para no depender de la validación de selección del ORM.
"""
import logging

_logger = logging.getLogger(__name__)

VALID_STATES = ('draft', 'in_workshop', 'done', 'cancel')


def migrate(cr, version):
    if not version:
        return

    # 'confirmed' legacy -> 'in_workshop'
    cr.execute(
        "UPDATE workshop_order SET state = 'in_workshop' WHERE state = 'confirmed'"
    )
    confirmed_fixed = cr.rowcount

    # Cualquier otro estado fuera de la selección actual -> 'draft'
    cr.execute(
        "UPDATE workshop_order SET state = 'draft' "
        "WHERE state IS NOT NULL AND state NOT IN %s",
        (VALID_STATES,),
    )
    other_fixed = cr.rowcount

    _logger.info(
        "[stone_workshop] Estados legacy normalizados: %s 'confirmed'->in_workshop, "
        "%s otros->draft.",
        confirmed_fixed,
        other_fixed,
    )
