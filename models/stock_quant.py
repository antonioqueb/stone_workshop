from odoo import models, api
from odoo.fields import Domain
import logging

_logger = logging.getLogger(__name__)

ACTIVE_WORKSHOP_STATES = (
    'in_workshop',
)


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.model
    def _workshop_safe_int_list(self, values):
        result = []
        for value in values or []:
            try:
                result.append(int(value))
            except (TypeError, ValueError):
                continue
        return result

    @api.model
    def _workshop_get_committed_lot_ids(self, product_id, current_lot_ids=None, order_id=False):
        current_lot_ids = set(self._workshop_safe_int_list(current_lot_ids))
        domain = [
            ('product_id', '=', int(product_id)),
            ('lot_id', '!=', False),
            ('state', 'not in', ('done', 'cancelled')),
            ('order_id.state', 'in', ACTIVE_WORKSHOP_STATES),
        ]
        if order_id:
            try:
                domain.append(('order_id', '!=', int(order_id)))
            except (TypeError, ValueError):
                pass

        lines = self.env['workshop.input.line'].search(domain)
        committed_ids = set(lines.mapped('lot_id').ids)
        return list(committed_ids - current_lot_ids)

    @api.model
    def _workshop_lot_field_exists(self, field_name):
        return field_name in self.env['stock.lot']._fields

    @api.model
    def _build_workshop_lot_domain(self, product_id, filters=None, current_lot_ids=None, location_id=False, order_id=False):
        filters = filters or {}
        current_lot_ids = self._workshop_safe_int_list(current_lot_ids)
        excluded_lot_ids = self._workshop_get_committed_lot_ids(product_id, current_lot_ids, order_id=order_id)

        base_domain = [
            ('product_id', '=', int(product_id)),
            ('lot_id', '!=', False),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]

        if location_id:
            try:
                base_domain.append(('location_id', 'child_of', int(location_id)))
            except (TypeError, ValueError):
                pass

        if excluded_lot_ids:
            base_domain.append(('lot_id', 'not in', excluded_lot_ids))

        free_domain = []
        if 'reserved_quantity' in self._fields:
            free_domain.append(('reserved_quantity', '=', 0))
        if 'x_tiene_hold' in self._fields:
            free_domain.append(('x_tiene_hold', '=', False))

        if current_lot_ids and free_domain:
            domain = list(Domain.AND([
                base_domain,
                Domain.OR([[('lot_id', 'in', current_lot_ids)], free_domain]),
            ]))
        else:
            domain = base_domain + free_domain

        if filters.get('lot_name'):
            domain.append(('lot_id.name', 'ilike', filters['lot_name']))
        if filters.get('bloque') and self._workshop_lot_field_exists('x_bloque'):
            domain.append(('lot_id.x_bloque', 'ilike', filters['bloque']))
        if filters.get('atado') and self._workshop_lot_field_exists('x_atado'):
            domain.append(('lot_id.x_atado', 'ilike', filters['atado']))
        if filters.get('alto_min') and self._workshop_lot_field_exists('x_alto'):
            try:
                domain.append(('lot_id.x_alto', '>=', float(filters['alto_min'])))
            except (TypeError, ValueError):
                pass
        if filters.get('ancho_min') and self._workshop_lot_field_exists('x_ancho'):
            try:
                domain.append(('lot_id.x_ancho', '>=', float(filters['ancho_min'])))
            except (TypeError, ValueError):
                pass
        if filters.get('tipo') and self._workshop_lot_field_exists('x_tipo'):
            domain.append(('lot_id.x_tipo', '=', filters['tipo']))

        return domain

    @api.model
    def _workshop_safe_lot_value(self, lot, field_name, default=False):
        if lot and field_name in lot._fields:
            value = lot[field_name]
            if hasattr(value, 'display_name'):
                return value.display_name or default
            return value if value not in (False, None) else default
        return default

    @api.model
    def _build_workshop_lots_data(self, lot_ids):
        lots_data = {}
        if not lot_ids:
            return lots_data

        for lot in self.env['stock.lot'].browse(lot_ids).exists():
            lots_data[lot.id] = {
                'name': lot.name or '',
                'x_grosor': self._workshop_safe_lot_value(lot, 'x_grosor', 0) or self._workshop_safe_lot_value(lot, 'thickness_cm', 0) or 0,
                'x_alto': self._workshop_safe_lot_value(lot, 'x_alto', 0) or self._workshop_safe_lot_value(lot, 'marble_height', 0) or 0,
                'x_ancho': self._workshop_safe_lot_value(lot, 'x_ancho', 0) or self._workshop_safe_lot_value(lot, 'marble_width', 0) or 0,
                'x_tipo': self._workshop_safe_lot_value(lot, 'x_tipo', '') or '',
                'x_bloque': self._workshop_safe_lot_value(lot, 'x_bloque', '') or self._workshop_safe_lot_value(lot, 'lot_general', '') or '',
                'x_atado': self._workshop_safe_lot_value(lot, 'x_atado', '') or '',
                'x_color': self._workshop_safe_lot_value(lot, 'x_color', '') or '',
                'x_origen': self._workshop_safe_lot_value(lot, 'x_origen', '') or '',
                'x_pedimento': self._workshop_safe_lot_value(lot, 'x_pedimento', '') or '',
                'x_fotografia_principal': self._workshop_safe_lot_value(lot, 'x_fotografia_principal', False) or False,
                'x_cantidad_fotos': self._workshop_safe_lot_value(lot, 'x_cantidad_fotos', 0) or 0,
                'x_detalles_placa': self._workshop_safe_lot_value(lot, 'x_detalles_placa', '') or '',
            }
        return lots_data

    @api.model
    def _workshop_quants_to_result(self, quants, lots_data):
        result = []
        for quant in quants:
            lot_id = quant.lot_id.id if quant.lot_id else False
            lot_info = lots_data.get(lot_id, {})
            reserved_qty = quant.reserved_quantity if 'reserved_quantity' in quant._fields else 0.0
            available_qty = (quant.quantity or 0.0) - (reserved_qty or 0.0)
            result.append({
                'id': quant.id,
                'lot_id': [lot_id, lot_info.get('name', '')] if lot_id else False,
                'location_id': [quant.location_id.id, quant.location_id.display_name] if quant.location_id else False,
                'quantity': quant.quantity or 0.0,
                'reserved_quantity': reserved_qty or 0.0,
                'available_quantity': available_qty,
                'x_grosor': lot_info.get('x_grosor', 0) or 0,
                'x_alto': lot_info.get('x_alto', 0) or 0,
                'x_ancho': lot_info.get('x_ancho', 0) or 0,
                'x_tipo': lot_info.get('x_tipo', '') or '',
                'x_bloque': lot_info.get('x_bloque', '') or '',
                'x_atado': lot_info.get('x_atado', '') or '',
                'x_color': lot_info.get('x_color', '') or '',
                'x_origen': lot_info.get('x_origen', '') or '',
                'x_pedimento': lot_info.get('x_pedimento', '') or '',
                'x_fotografia_principal': lot_info.get('x_fotografia_principal', False),
                'x_cantidad_fotos': lot_info.get('x_cantidad_fotos', 0) or 0,
                'x_detalles_placa': lot_info.get('x_detalles_placa', '') or '',
            })
        return result

    @api.model
    def search_workshop_lot_inventory(self, product_id, filters=None, current_lot_ids=None, location_id=False, order_id=False):
        filters = filters or {}
        domain = self._build_workshop_lot_domain(
            product_id=product_id,
            filters=filters,
            current_lot_ids=current_lot_ids,
            location_id=location_id,
            order_id=order_id,
        )
        quants = self.search(domain, limit=300, order='lot_id, location_id, id')
        lots_data = self._build_workshop_lots_data(quants.mapped('lot_id').ids)
        result = self._workshop_quants_to_result(quants, lots_data)
        _logger.info('[WORKSHOP LOT SELECTOR] product=%s result=%s', product_id, len(result))
        return result

    @api.model
    def search_workshop_lot_inventory_paginated(self, product_id, filters=None, current_lot_ids=None, page=0, page_size=35, location_id=False, order_id=False):
        filters = filters or {}
        page = int(page or 0)
        page_size = int(page_size or 35)
        domain = self._build_workshop_lot_domain(
            product_id=product_id,
            filters=filters,
            current_lot_ids=current_lot_ids,
            location_id=location_id,
            order_id=order_id,
        )
        total = self.search_count(domain)
        quants = self.search(domain, limit=page_size, offset=page * page_size, order='lot_id, location_id, id')
        lots_data = self._build_workshop_lots_data(quants.mapped('lot_id').ids)
        return {
            'items': self._workshop_quants_to_result(quants, lots_data),
            'total': total,
        }
