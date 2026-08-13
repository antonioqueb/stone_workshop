# -*- coding: utf-8 -*-
"""Orden cronológico del "Historial de logs" del lote (Inventario Visual).

La fecha que se muestra al usuario va formateada ("13 ago 2026 14:30") y ese
texto NO ordena. Cada entrada que agrega este módulo lleva además la clave
técnica 'fecha_sort' con la fecha en ISO, que sí ordena como texto y viaja
sin problema al frontend (es una cadena, no un datetime).

La lista se comparte con otros módulos: `stock.quant.get_lot_history` la va
componiendo por herencia y cada capa la reordena. Por eso 'fecha_sort' NO se
elimina al final (la siguiente capa la vuelve a necesitar) y por eso la clave
de orden sabe leer entradas ajenas que solo traen la etiqueta.
"""

from .som_date_format import MESES_ES

#: Ordena al final las entradas sin fecha reconocible (orden descendente).
_NO_DATE = ''


def som_log_sort_key(log):
    """Devuelve una cadena ISO ordenable para una entrada del historial."""
    value = log.get('fecha_sort')
    if value:
        return value

    # Entradas de otros módulos: solo traen la etiqueta ya formateada.
    label = (log.get('fecha') or '').strip()
    if not label:
        return _NO_DATE

    parts = label.split()
    if len(parts) >= 3 and parts[1] in MESES_ES:
        # "13 ago 2026" / "13 ago 2026 14:30"
        try:
            iso = '%04d-%02d-%02d' % (
                int(parts[2]), MESES_ES.index(parts[1]) + 1, int(parts[0]))
        except (TypeError, ValueError):
            return _NO_DATE
        return '%s %s' % (iso, parts[3]) if len(parts) >= 4 else iso

    # Formato ISO heredado ("2026-08-13 14:30"): ya ordena tal cual.
    return label


def som_sort_general_logs(logs):
    """Ordena el historial general de más reciente a más antiguo."""
    logs.sort(key=som_log_sort_key, reverse=True)
    return logs
