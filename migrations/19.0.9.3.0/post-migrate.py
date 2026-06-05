"""Seed de Minutos/m² en el catálogo de servicios (workshop.process).

El cliente entregó `Tiempos de taller.xlsx` con el rendimiento (min/m²) de cada
servicio. Los servicios YA existen en el sistema, así que aquí se rellena
`minutes_per_sqm` buscando cada proceso por su código.

Idempotente y no destructivo: solo escribe cuando el proceso existe y aún no
tiene valor capturado (0/vacío), para no pisar ajustes manuales. Corre una sola
vez en este salto de versión.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# Código de servicio -> minutos por m² (origen: Tiempos de taller.xlsx)
MINUTES_BY_CODE = {
    # Corte mármol
    'CM01': 30, 'CM02': 25, 'CM03': 25, 'CM04': 20, 'CM05': 22, 'CM06': 20,
    'CM07': 21, 'CM08': 18, 'CM09': 18, 'CM10': 18, 'CM11': 18, 'CM12': 18,
    'CM13': 18, 'CM14': 18,
    # Acabado mármol
    'MM01': 16, 'PM01': 16, 'CPM01': 16, 'BM01': 16, 'SBM01': 16,
    'RM01': 36, 'RM02': 18, 'RM03': 48, 'RM04': 24,
    # Corte granito
    'CG01': 37.5, 'CG02': 31.25, 'CG03': 31.25, 'CG04': 25, 'CG05': 27.5,
    'CG06': 25, 'CG07': 26.25, 'CG08': 22.5, 'CG09': 22.5, 'CG10': 22.5,
    'CG11': 22.5, 'CG12': 22.5, 'CG13': 22.5, 'CG14': 22.5,
    # Acabado granito
    'MG01': 32, 'CPG01': 48, 'PG01': 32,
    'RG01': 43.2, 'RG02': 21.6, 'RG03': 57.6, 'RG04': 28.8,
    # Acabado cuarcita
    'MC01': 32, 'CPC01': 16, 'PC01': 48,
}


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Process = env['workshop.process']

    updated = 0
    skipped_existing = 0
    not_found = []
    for code, minutes in MINUTES_BY_CODE.items():
        process = Process.search([('code', '=', code)], limit=1)
        if not process:
            not_found.append(code)
            continue
        if process.minutes_per_sqm:
            skipped_existing += 1
            continue
        process.minutes_per_sqm = float(minutes)
        updated += 1

    _logger.info(
        "[stone_workshop] min/m² seed: %s procesos actualizados, %s ya tenían valor, "
        "%s códigos no encontrados%s",
        updated,
        skipped_existing,
        len(not_found),
        (': %s' % ', '.join(not_found)) if not_found else '',
    )
