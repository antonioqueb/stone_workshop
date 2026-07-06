# -*- coding: utf-8 -*-
"""Elimina la constraint SQL UNIQUE(log_id, input_line_id) de las líneas de
bitácora: se reemplazó por una constraint Python (evaluada al final del flush)
porque el patrón "vaciar + recrear" del selector de placas disparaba falsos
duplicados cuando el ORM ejecutaba los INSERT antes que los DELETE."""


def migrate(cr, version):
    for constraint in (
        'workshop_progress_log_line_uniq_log_input',
        'workshop_progress_log_line__uniq_log_input',
    ):
        cr.execute(
            'ALTER TABLE workshop_progress_log_line '
            'DROP CONSTRAINT IF EXISTS "%s"' % constraint
        )
