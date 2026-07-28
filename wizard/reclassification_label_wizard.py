# -*- coding: utf-8 -*-
import base64

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ReclassificationLabelWizard(models.TransientModel):
    """Impresión de etiquetas ZPL para los lotes NUEVOS de una
    reclasificación aplicada.

    Reutiliza el generador estándar del sistema
    (stock.quant.generate_zpl_labels, de inventory_shopping_cart) y el mismo
    flujo de descarga que la recepción: adjunto .zpl + act_url."""
    _name = 'stock.lot.reclassification.label.wizard'
    _description = 'Etiquetas de lotes reclasificados'

    reclassification_id = fields.Many2one(
        'stock.lot.reclassification',
        string='Reclasificación',
        required=True,
        readonly=True,
    )
    label_format = fields.Selection([
        ('10x5', 'Estándar (10x5 cm)'),
        ('17.5x1', 'Canto/Lomo (17.5x1 cm)'),
        ('20x10', 'Grande (20x10 cm)'),
    ], string='Formato de Etiqueta', default='17.5x1', required=True)
    lot_count = fields.Integer(compute='_compute_lot_count')

    @api.depends('reclassification_id')
    def _compute_lot_count(self):
        for wizard in self:
            wizard.lot_count = len(
                wizard.reclassification_id.line_ids.mapped('lot_to_id'))

    def action_print(self):
        self.ensure_one()
        rec = self.reclassification_id
        if rec.state != 'done':
            raise UserError(_(
                'Las etiquetas solo se imprimen con la reclasificación '
                'aplicada: los lotes nuevos aún no existen.'
            ))

        lots = rec.line_ids.mapped('lot_to_id')
        if not lots:
            raise UserError(_('La reclasificación no tiene lotes nuevos.'))

        Quant = self.env['stock.quant'].sudo()
        quant_ids = []
        missing = []
        for lot in lots:
            quant = Quant.search([
                ('lot_id', '=', lot.id),
                ('location_id.usage', '=', 'internal'),
                ('quantity', '>', 0),
            ], limit=1)
            if not quant:
                quant = Quant.search([
                    ('lot_id', '=', lot.id),
                    ('quantity', '>', 0),
                ], limit=1)
            if quant:
                quant_ids.append(quant.id)
            else:
                missing.append(lot.name)

        if not quant_ids:
            raise UserError(_(
                'No se encontraron existencias de los lotes reclasificados '
                'para imprimir (¿ya salieron del inventario?).'
            ))

        if not hasattr(self.env['stock.quant'], 'generate_zpl_labels'):
            raise UserError(_(
                'El módulo de impresión de etiquetas (generate_zpl_labels) '
                'no está disponible.'
            ))

        result = self.env['stock.quant'].generate_zpl_labels(
            quant_ids, self.label_format)
        if not result.get('success'):
            raise UserError(
                result.get('message', _('Error al generar etiquetas.')))

        filename = 'etiquetas_%s_%s.zpl' % (
            (rec.name or 'RECLA').replace('/', '-'), self.label_format)
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(
                (result.get('zpl_data') or '').encode('utf-8')),
            'mimetype': 'text/plain',
            'res_model': 'stock.lot.reclassification',
            'res_id': rec.id,
        })

        if missing:
            rec.message_post(body=_(
                'Etiquetas generadas (%(fmt)s). Sin existencias para: '
                '%(lots)s.'
            ) % {'fmt': self.label_format, 'lots': ', '.join(missing)})

        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }
